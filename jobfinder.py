import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from serpapi import GoogleSearch
import yaml
from datetime import datetime

# --- Load config ---
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# --- Secrets from environment (GitHub Actions) ---
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

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

# --- Build Combined HTML ---
def build_combined_html(all_results):
    today = datetime.now().strftime('%b %d, %Y')
    html = f"<html><body><h2>🧭 Daily Job Digest ({today})</h2>"
    if not any(all_results.values()):
        html += "<p>No new jobs found today.</p></body></html>"
        return html

    for role, jobs in all_results.items():
        html += f"<h3>🔹 {role}</h3>"
        if not jobs:
            html += "<p>No new postings found for this role today.</p>"
            continue
        html += "<ul>"
        for j in jobs[:15]:
            title = j.get("title", "No title")
            company = j.get("company_name", "Unknown")
            location = j.get("location", "Unknown")
            link = j.get("link", "#")
            html += f"<li><b>{title}</b> at {company} ({location})<br><a href='{link}'>View Job</a></li>"
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
    all_results = {}
    for role in config["roles"]:
        jobs = []
        for loc in config["locations"]:
            jobs.extend(search_jobs(role, loc))
        all_results[role] = jobs

    html = build_combined_html(all_results)
    send_email(
        subject=f"🧭 Daily Job Digest – {datetime.now().strftime('%b %d, %Y')}",
        html_content=html
    )

if __name__ == "__main__":
    main()
