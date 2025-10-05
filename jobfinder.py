import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from serpapi import GoogleSearch
import requests
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

# --- Job Search Function ---
def search_jobs(role, location):
    params = {
        "engine": "google_jobs",
        "q": f"{role} {location}",
        "hl": "en",
        "api_key": SERPAPI_KEY,
        "date_posted": "past_24_hours"
    }
    search = GoogleSearch(params)
    results = search.get_dict().get("jobs_results", [])
    return results

# --- Format HTML Email ---
def format_email(jobs):
    if not jobs:
        return f"<p>No new jobs found today ({datetime.now().date()}).</p>"
    html = f"""
    <html>
    <body>
        <h2>🧭 Daily Job Digest ({datetime.now().strftime('%b %d, %Y')})</h2>
        <p>Here are the latest jobs found in the last 24 hours:</p>
        <ul>
    """
    for j in jobs[:20]:
        title = j.get("title", "No title")
        company = j.get("company_name", "Unknown")
        location = j.get("location", "Unknown")
        link = j.get("link", "#")
        html += f"<li><b>{title}</b> at {company} ({location})<br><a href='{link}'>View Job</a></li><br>"
    html += "</ul></body></html>"
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
        for location in config["locations"]:
            all_jobs.extend(search_jobs(role, location))
    html = format_email(all_jobs)
    send_email("🧭 Daily Job Digest", html)

if __name__ == "__main__":
    main()
