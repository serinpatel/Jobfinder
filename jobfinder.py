import os
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
resumes = {
    "Data Analyst": open("Data Analyst.txt", "r", encoding="utf-8").read(),
    "Application Support Analyst": open("Application Support Analyst.txt", "r", encoding="utf-8").read()
}

# --- Embedding model ---
model = SentenceTransformer("all-MiniLM-L6-v2")

# --- Job Search ---
def search_jobs(role, location):
    params = {
        "engine": "google_jobs",
        "q": f"{role} {location}",
        "hl": "en",
        "api_key": SERPAPI_KEY,
        "date_posted": "past_24_hours"
    }
    search = GoogleSearch(params)
    return search.get_dict().get("jobs_results", [])

# --- Similarity Scoring ---
def match_jobs_to_resumes(jobs):
    matches = {name: [] for name in resumes}
    for j in jobs:
        desc = " ".join([j.get("title", ""), j.get("company_name", ""), j.get("description", "")])
        if not desc.strip():
            continue
        job_emb = model.encode(desc, convert_to_tensor=True)
        for name, text in resumes.items():
            score = float(util.cos_sim(model.encode(text, convert_to_tensor=True), job_emb))
            matches[name].append((score, j))
    return matches
    
def extract_job_link(job):
    # Try multiple possible link sources in order of reliability
    if job.get("apply_options"):
        # Sometimes multiple apply links; pick the first
        link = job["apply_options"][0].get("link")
        if link:
            return link
    if job.get("related_links"):
        link = job["related_links"][0].get("link")
        if link:
            return link
    if job.get("link"):
        return job["link"]
    return "#"  # fallback if none found

# --- Email Formatting ---
def build_email(matches):
    today = datetime.now().strftime('%b %d, %Y')
    html = f"<html><body><h2>🧭 Daily Job Digest ({today})</h2>"

    for name, job_list in matches.items():
        html += f"<h3>👤 {name}</h3>"
        top_jobs = sorted(job_list, key=lambda x: x[0], reverse=True)[:5]
        if not top_jobs:
            html += "<p>No matches today.</p>"
            continue
        html += "<ul>"
        for score, j in top_jobs:
            title = j.get("title", "No title")
            company = j.get("company_name", "Unknown")
            location = j.get("location", "Unknown")
            link = extract_job_link(j)
            html += f"<li><b>{title}</b> at {company} ({location})<br>"
            html += f"<a href='{link}' target='_blank'>🔗 View Job</a></li>"
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
        for loc in config["locations"]:
            all_jobs.extend(search_jobs(role, loc))
    print(f"Fetched {len(all_jobs)} jobs")

    matches = match_jobs_to_resumes(all_jobs)
    html = build_email(matches)
    send_email(
        subject=f"🧭 AI-Powered Job Matches – {datetime.now().strftime('%b %d, %Y')}",
        html_content=html
    )

if __name__ == "__main__":
    main()
