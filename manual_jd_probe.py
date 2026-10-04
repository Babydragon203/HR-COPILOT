"""One-off probe: inspect raw /api/jd/generate output to RCA empty skill lists."""
import json
import sys

import requests

sys.path.insert(0, "/app/backend/tests")
from conftest import API, STD_TIMEOUT, AI_TIMEOUT  # noqa: E402

creds = {"email": "ayushchaturvedi205@gmail.com", "password": "HrCopilot@2026"}
tok = requests.post(f"{API}/auth/login", json=creds, timeout=STD_TIMEOUT).json()["token"]
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

base = {
    "company_name": "TEST Acme Corp", "industry": "Technology", "company_size": "Startup",
    "department": "Engineering", "title": "Senior Backend Engineer", "seniority": "Senior",
    "experience": "5-8 years", "employment_type": "Full-time", "work_arrangement": "Remote",
    "required_skills": ["Python", "FastAPI", "MongoDB"], "preferred_skills": ["AWS"],
}

for label, payload in [
    ("DEFAULT include_sections", base),
    ("EXPLICIT include_sections w/ skills", {**base, "include_sections": [
        "Company Overview", "Responsibilities", "Required Skills", "Preferred Skills",
        "Qualifications", "KPIs", "Benefits"]}),
]:
    r = requests.post(f"{API}/jd/generate", json=payload, headers=H, timeout=AI_TIMEOUT)
    print(f"\n=== {label} -> {r.status_code}")
    if r.status_code != 200:
        print(r.text[:400])
        continue
    c = r.json()["content"]
    for k, v in c.items():
        empty = (v == [] or v == "" or v is None)
        print(f"  {k}: {'EMPTY' if empty else json.dumps(v)[:90]}")
