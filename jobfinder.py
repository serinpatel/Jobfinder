import os, re, time, smtplib, yaml
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from serpapi import GoogleSearch   # safer import
from datetime import datetime
from sentence_transformers import SentenceTransformer, util

# --- Load config ---
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

SERPAPI_KEY  = os.getenv("SERPAPI_KEY")
EMAIL_USER   = os.getenv("EMAIL_USER")
EMAIL_PASS   = os.getenv("EMAIL_PASS")
EMAIL_TO     = os.getenv("EMAIL_TO", EMAIL_USER)

# --- Load resumes ---
def _read_txt(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

resumes = {
    "Data Analyst": _read_txt("Data Analyst.txt"),
    "Application Support Analyst": _read_txt("Application Support Analyst.txt"),
}

# --- Embedding model ---
model = SentenceTransformer("all-MiniLM-L6-v2")
resume_embeddings = {n: model.encode(t, convert_to_tensor=True) for n, t in resumes.items()}

# -------- Helpers --------
def extract_job_link(job):
    for k in ("apply_options", "related_links"):
        opts = job.get(k)
        if opts and isinstance(opts, list) and "link" in opts[0]:
            return opts[0]["link"]
    return job.get("link", "#")

def _parse_age_hours(txt):
    t = (txt or "").lower()
    if "just" in t or "today" in t:
        return 0.0
    m = re.search(r"(\d+)\s*(minute|minutes|hour|hours|day|days)", t)
    if not m: return float("inf")
    n, unit = int(m.group(1)), m.group(2)
    return n/60 if "minute" in unit else n if "hour" in unit else n*24

def is_fresh(job, max_hours=24):
    det = job.get("detected_extensions", {}) or {}
    txt = str(det.get("posted_at") or det.get("posted") or "")
    return _parse_age_hours(txt) <= max_hours

def _dedup(jobs):
    seen, out = set(), []
    for j in jobs:
        key = (j.get("title",""), j.get("company_name",""), j.get("location",""))
        if key not in seen:
            seen.add(key); out.append(j)
    return out

# -------- SerpAPI search --------
def search_jobs_for_role(role, locations, sleep_sec=1.5):
    """Fetch freshest available jobs (<=24h) for each role across given locations."""
    collected = []
    for loc in locations:
        q = f"{role} {loc}"
        params = {
            "engine": "google_jobs", "q": q, "hl": "en",
            "api_key": SERPAPI_KEY, "date_posted": "past_24_hours",
            "sort_by": "date", "num": 20
        }
        results = GoogleSearch(params).get_dict().get("jobs_results", []) or []
        fresh = [r for r in results if is_fresh(r)]
        if fresh:
            collected.extend(fresh)
        time.sleep(sleep_sec)
    return _dedup(collected)

# -------- Matching --------
def match_jobs_to_resumes(jobs):
    matches = {n: [] for n in resumes}
    descs, job_refs = [], []
    for j in jobs:
        d = " ".join([j.get("title",""), j.get("company_name",""), j.get("description","")]).strip()
        if d:
            descs.append(d); job_refs.append(j)
    if not job_refs:
        return matches
    job_embs = model.encode(descs, convert_to_tensor=True)
    for idx, j in enumerate(job_refs):
        emb = job_embs[idx]
        for n, res_emb in resume_embeddings.items():
            score = float(util.cos_sim(res_emb, emb))
            matches[n].append((score, j))
    return matches

# -------- Email --------
def build_email(matches):
    today = datetime.now().strftime('%b %d, %Y')
    html = [f"<html><body><h2>🧭 AI-Powered Job Digest ({today})</h2>"]
    for name, jobs in matches.items():
        html.append(f"<h3>👤 {name}</h3>")
        if not jobs:
            html.append("<p>No fresh jobs today.</p>")
            continue
        top = sorted(jobs, key=lambda x: x[0], reverse=True)[:15]
        html.append("<ul>")
        for score, j in top:
            html.append(
                f"<li><b>{j.get('title','')}</b> at {j.get('company_name','')} "
                f"({j.get('location','')}) – <b>{int(score*100)}%</b> match<br>"
                f"<a href='{extract_job_link(j)}' target='_blank' "
                f"style='color:#1a73e8;text-decoration:none;'>🔗 View Job</a></li>"
            )
        html.append("</ul><br>")
    html.append("</body></html>")
    return "".join(html)

def send_email(subject, html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_USER, EMAIL_TO
    msg.attach(MIMEText(html_content, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls(); s.login(EMAIL_USER, EMAIL_PASS); s.send_message(msg)
    print(f"✅ Email sent to {EMAIL_TO}")

# -------- Main --------
def main():
    roles = []
    seen = set()
    for r in config["roles"]:
        rl = r.strip()
        if rl.lower() not in seen:
            seen.add(rl.lower()); roles.append(rl)

    all_jobs = []
    for role in roles:
        fresh = search_jobs_for_role(role, config["locations"])
        print(f"{role}: {len(fresh)} fresh jobs")
        all_jobs.extend(fresh)
        time.sleep(1)

    matches = match_jobs_to_resumes(all_jobs)
    html = build_email(matches)
    send_email(f"🧭 {len(roles)}-Role Job Digest – {datetime.now():%b %d, %Y}", html)

if __name__ == "__main__":
    main()
