"""Manual probe: dump JD content, public-URL CORS headers, bcrypt hash format."""
import asyncio
import json
import os
import sys

import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")
FE = dotenv_values("/app/frontend/.env")
BASE = FE["REACT_APP_BACKEND_URL"].rstrip("/")
API = BASE + "/api"
INTERNAL = "http://localhost:8001"
CREDS = {"email": "ayushchaturvedi205@gmail.com", "password": "HrCopilot@2026"}

JD = {
    "company_name": "TEST Acme Corp", "industry": "Technology", "company_size": "Startup",
    "department": "Engineering", "title": "Senior Backend Engineer", "seniority": "Senior",
    "experience": "5-8 years", "employment_type": "Full-time", "work_arrangement": "Remote",
    "location": "Remote", "required_skills": ["Python", "FastAPI", "MongoDB"],
    "preferred_skills": ["AWS", "Kubernetes"], "benefits": ["Health Insurance"],
    "include_sections": ["Company Overview"],
}


def main():
    tok = requests.post(f"{API}/auth/login", json=CREDS, timeout=60).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    r = requests.post(f"{API}/jd/generate", json=JD, headers=H, timeout=240)
    print("JD status", r.status_code)
    c = r.json()["content"]
    for k, v in c.items():
        print(f"  {k}: {json.dumps(v)[:220]}")

    for label, url in (("PUBLIC", API), ("INTERNAL", INTERNAL + "/api")):
        rr = requests.post(f"{url}/auth/login", json={"email": "x@y.com", "password": "bad"},
                           headers={"Origin": "https://foo.example.com"}, timeout=60)
        h = {k.lower(): v for k, v in rr.headers.items()}
        print(label, "ACAO=", h.get("access-control-allow-origin"), "ACAC=",
              h.get("access-control-allow-credentials"), "vary=", h.get("vary"), "status=", rr.status_code)


async def check_hash():
    from motor.motor_asyncio import AsyncIOMotorClient
    env = dotenv_values("/app/backend/.env")
    cl = AsyncIOMotorClient(env["MONGO_URL"])
    u = await cl[env["DB_NAME"]].users.find_one({"email": CREDS["email"]})
    print("bcrypt prefix:", u["password_hash"][:7], "len:", len(u["password_hash"]))


main()
asyncio.run(check_hash())
