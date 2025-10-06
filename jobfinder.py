import os
import re
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from serpapi import GoogleSearch
import yaml
from datetime import datetime
from sentence_transformers import SentenceTransformer, util

# --- Load config ---
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

# --- Load resumes ---
def _read_txt(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

resumes = {
    "Data Analyst": _read_txt("Data Analyst.txt"),
    "Application Support Analyst": _read_txt("Application Support Analyst.txt"),
}


# --- Embedding model (precompute resume embeddings) ---
model = SentenceTransformer("all-MiniLM-L6-v2")
resume_embeddings = {name: model.encode(text, convert_to_tensor=True) for name, text in resumes.items()}

# -------- Helpers --------
def extract_job_link(job):
    """Return the best available link for a job posting."""
    if job.get("apply_options"):
        link = job["apply_options"][0].get("link")
        if link:
            return link
    if job.get("related_links"):
        link = job["related_links"][0].get("link")
        if link:
            return link
    if job.get("link"):
        return job["link"]
    return "#"

def _parse_age_hours(text: str) -> float:
    """
    Convert '3 hours ago', '45 minutes ago', '1 day ago', 'today', 'just posted'
    to a numeric age in hours. Unknown -> +inf.
    """
    t = (text or "").lower().strip()
    if not t:
        return float("inf")
    if "just" in t or "today" in t:
        return 0.0
    m = re.search(r"(\d+)\s*(minute|minutes|hour|hours|day|days)", t)
    if not m:
        return float("inf")
    n = int(m.group(1))
    unit = m.group(2)
    if "minute" in unit:
        return n / 60.0
    if "hour" in unit:
        return float(n)
    if "day" in unit:
        return float(n * 24)
    return float("inf")

def is_fresh(job, max_hours=24.0):
    """
    Keep only postings clearly within last 24h using SerpAPI metadata.
    Checks detected_extensions.posted_at or detected_extensions.posted.
    """
    det = job.get("detected_extensions", {}) or {}
    posted = str(det.get("posted_at") or det.get("posted") or "").strip()
    hours = _parse_age_hours(posted)
    return hours <= max_hours

def _dedup_by_title_company_location(jobs):
    seen = set()
    out = []
    for j in jobs:
        key = (j.get("title", "").strip(), j.get("company_name", "").strip(), j.get("location", "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out

# -------- SerpAPI search --------
def _serpapi_call(q, start=0, num=20):
    params = {
        "engine": "google_jobs",
        "q": q,
        "hl": "en",
        "api_key": SERPAPI_KEY,
        "date_posted": "past_24_hours",  # request filter (we'll also verify ourselves)
        "sort_by": "date",
        "start": start,
        "num": num
    }
    return GoogleSearch(params).get_dict().get("jobs_results", []) or []

def search_jobs_for_role(role, locations, min_jobs=10, broaden_to_country=None, sleep_sec=1.5):
    """
    Collect >= min_jobs truly-fresh jobs for a role by:
      - querying all provided locations (round-robin)
      - paginating per location (start += 20)
      - optional broadening to a country if still short
    """
    # normalize locations list (no duplicates, keep order)
    seen_loc = set()
    locs = []
    for loc in locations:
        if loc not in seen_loc:
            seen_loc.add(loc)
            locs.append(loc)

    per_loc_start = {loc: 0 for loc in locs}
    collected = []
    rounds = 0
    hard_cap_rounds = 8  # safety cap

    while len(collected) < min_jobs and rounds < hard_cap_rounds:
        rounds += 1
        for loc in locs:
            q = f"{role} {loc}"
            results = _serpapi_call(q, start=per_loc_start[loc], num=20)
            per_loc_start[loc] += 20

            # keep only fresh jobs
            fresh = [r for r in results if is_fresh(r)]
            if fresh:
                collected.extend(fresh)

            time.sleep(sleep_sec)
            if len(collected) >= min_jobs:
                break

        if len(collected) >= min_jobs:
            break

        # If still short after 2 rounds, optionally broaden query
        if rounds == 2 and broaden_to_country:
            if broaden_to_country not in per_loc_start:
                per_loc_start[broaden_to_country] = 0
                locs.append(broaden_to_country)

    collected = _dedup_by_title_company_location(collected)
    return collected[:max(min_jobs, len(collected))]

# -------- Matching to resumes --------
def match_jobs_to_resumes(jobs):
    matches = {name: [] for name in resumes}

    # batch-encode job descriptions for speed
    descs, job_refs = [], []
    for j in jobs:
        desc = " ".join([j.get("title", ""), j.get("company_name", ""), j.get("description", "")]).strip()
        if not desc:
            continue
        descs.append(desc)
        job_refs.append(j)

    if not job_refs:
        return matches

    job_embs = model.encode(descs, convert_to_tensor=True)
    for idx, j in enumerate(job_refs):
        emb = job_embs[idx]
        for name, res_emb in resume_embeddings.items():
            score = float(util.cos_sim(res_emb, emb))
            matches[name].append((score, j))
    return matches

# -------- Email --------
def build_email(matches):
    today = datetime.now().strftime('%b %d, %Y')
    html = [f"<html><body><h2>🧭 AI-Powered Job Digest ({today})</h2>"]

    for name, job_list in matches.items():
        html.append(f"<h3>👤 {name}</h3>")
        if not job_list:
            html.append("<p>No matches found today.</p>")
            continue

        # top 15 per resume
        top_jobs = sorted(job_list, key=lambda x: x[0], reverse=True)[:15]
        html.append("<ul>")
        for score, j in top_jobs:
            title = j.get("title", "No title")
            company = j.get("company_name", "Unknown")
            location = j.get("location", "Unknown")
            link = extract_job_link(j)
            html.append(
                f"<li><b>{title}</b> at {company} ({location}) "
                f"– <b>{int(score*100)}%</b> match<br>"
                f"<a href='{link}' target='_blank' style='color:#1a73e8;text-decoration:none;'>🔗 View Job</a></li>"
            )
        html.append("</ul><br>")
    html.append("</body></html>")
    return "".join(html)

def send_email(subject, html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_content, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
    print(f"✅ Email sent to {EMAIL_TO}")

# -------- Main --------
def main():
    # Remove exact duplicate roles in config (safety)
    roles = []
    seen = set()
    for r in config["roles"]:
        rr = r.strip()
        if rr.lower() not in seen:
            seen.add(rr.lower())
            roles.append(rr)

    all_fresh_jobs = []
    for role in roles:
        fresh_for_role = search_jobs_for_role(
            role=role,
            locations=list(config["locations"]),
            min_jobs=10,
            broaden_to_country=None,   # You already have Remote/Ontario; set "Canada" to broaden if needed
            sleep_sec=1.0
        )
        print(f"{role}: collected {len(fresh_for_role)} fresh jobs (≤24h)")
        all_fresh_jobs.extend(fresh_for_role)
        time.sleep(1)  # keep total request rate gentle

    matches = match_jobs_to_resumes(all_fresh_jobs)
    html = build_email(matches)
    send_email(
        subject=f"🧭 {len(roles)}-Role Job Digest – {datetime.now().strftime('%b %d, %Y')}",
        html_content=html
    )

if __name__ == "__main__":
    main()
