import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from serpapi.google_search_results import GoogleSearch
import yaml
from datetime import datetime
from sentence_transformers import SentenceTransformer, util
import time

# --- Load config ---
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

# --- Load resumes ---
resumes = {
    "Data Analyst": open("Data Analyst.txt", "r", encoding="utf-8").read(),
    "Application Support Analyst": open("Application Support Analyst.txt", "r", encoding="utf-8").read()
}

# --- Embedding model ---
model = SentenceTransformer("all-MiniLM-L6-v2")
resume_embeddings = {name: model.encode(text, convert_to_tensor=True) for name, text in resumes.items()}

# --- Extract valid job link ---
def extract_job_link(job):
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

# --- Search Jobs with Retry and Pagination ---
def search_jobs(role, location, min_jobs=10):
    """Fetch at least min_jobs from the past 24 hours."""
    params = {
        "engine": "google_jobs",
        "q": f"{role} {location}",
        "hl": "en",
        "api_key": SERPAPI_KEY,
        "date_posted": "past_24_hours",
        "num": 10
    }
    all_results = []
    attempts = 0

    while len(all_results) < min_jobs and attempts < 5:
        search = GoogleSearch(params)
        results = search.get_dict().get("jobs_results", [])
        for job in results:
            # Avoid duplicates
            key = (job.get("title", ""), job.get("company_name", ""))
            if key not in {(j.get('title', ''), j.get('company_name', '')) for j in all_results}:
                all_results.append(job)
        attempts += 1
        time.sleep(2)  # avoid rate limits

        # Try broadening the query if still fewer jobs
        if len(all_results) < min_jobs and attempts == 3:
            params["q"] = f"{role} Canada"  # expand to broader search

    return all_results[:max(min_jobs, len(all_results))]

# --- Match Jobs to Resumes ---
def match_jobs_to_resumes(jobs):
    matches = {name: [] for name in resumes}
    for j in jobs:
        desc = " ".join([j.get("title", ""), j.get("company_name", ""), j.get("description", "")])
        if not desc.strip():
            continue
        job_emb = model.encode(desc, convert_to_tensor=True)
        for name, res_emb in resume_embeddings.items():
            score = float(util.cos_sim(res_emb, job_emb))
            matches[name].append((score, j))
    return matches

# --- Build HTML Email ---
def build_email(matches):
    today = datetime.now().strftime('%b %d, %Y')
    html = f"<html><body><h2>🧭 AI-Powered Job Digest ({today})</h2>"

    for name, job_list in matches.items():
        html += f"<h3>👤 {name}</h3>"
        if not job_list:
            html += "<p>No matches found today.</p>"
            continue

        # Sort by similarity & limit top 15
        top_jobs = sorted(job_list, key=lambda x: x[0], reverse=True)[:15]
        html += "<ul>"
        for score, j in top_jobs:
            title = j.get("title", "No title")
            company = j.get("company_name", "Unknown")
            location = j.get("location", "Unknown")
            link = extract_job_link(j)
            html += (
                f"<li><b>{title}</b> at {company} ({location}) "
                f"– <b>{int(score*100)}%</b> match<br>"
                f"<a href='{link}' target='_blank' "
                f"style='color:#1a73e8;text-decoration:none;'>🔗 View Job</a></li>"
            )
        html += "</ul><br>"
    html += "</body></html>"
    return html

# --- Send Email ---
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

# --- Main ---
def main():
    all_jobs = []
    for role in config["roles"]:
        role_jobs = []
        for loc in config["locations"]:
            role_jobs.extend(search_jobs(role, loc, min_jobs=10))
        # Deduplicate across locations
        seen = set()
        unique_jobs = []
        for j in role_jobs:
            key = (j.get("title", ""), j.get("company_name", ""))
            if key not in seen:
                seen.add(key)
                unique_jobs.append(j)
        print(f"{role}: Collected {len(unique_jobs)} jobs in last 24 hrs")
        all_jobs.extend(unique_jobs)

    matches = match_jobs_to_resumes(all_jobs)
    html = build_email(matches)
    send_email(
        subject=f"🧭 AI-Powered Job Matches – {datetime.now().strftime('%b %d, %Y')}",
        html_content=html
    )

if __name__ == "__main__":
    main()
