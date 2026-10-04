"""Shared fixtures for HR Copilot AI backend tests."""
import os
import re
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
_base = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not _base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing from env and /app/frontend/.env")
BASE_URL = _base.rstrip("/")
API = BASE_URL + "/api"

AI_TIMEOUT = 240
STD_TIMEOUT = 60


def unique_email(prefix="test"):
    return f"TEST_{prefix}_{uuid.uuid4().hex[:10]}@example.com".lower()


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def test_credentials():
    p = Path("/app/memory/test_credentials.md")
    if not p.exists():
        pytest.skip("Missing /app/memory/test_credentials.md")
    content = p.read_text(encoding="utf-8")
    em = re.search(r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?email(?:\*\*)?\s*:\s*`?([^`\s]+)", content)
    pw = re.search(r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?password(?:\*\*)?\s*:\s*`?([^`\s]+)", content)
    if not em or not pw:
        pytest.skip("No credentials found in test_credentials.md")
    return {"email": em.group(1), "password": pw.group(1)}


@pytest.fixture(scope="session")
def admin_token(api, test_credentials):
    r = api.post(f"{API}/auth/login", json=test_credentials, timeout=STD_TIMEOUT)
    if r.status_code != 200:
        pytest.fail(f"Seeded admin login failed {r.status_code}: {r.text[:400]}")
    tok = r.json().get("token")
    if not tok:
        pytest.fail("Login response missing token")
    return tok


def client_with(token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    return s


def signup_org(name_prefix="Org"):
    """Create a fresh org + org_admin; returns (session, user, token, email, password)."""
    email = unique_email(name_prefix)
    password = "TestPass@2026"
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password, "full_name": f"{name_prefix} Admin",
        "organization_name": f"TEST_{name_prefix}_{uuid.uuid4().hex[:6]}",
        "industry": "Technology", "company_size": "Startup",
    }, timeout=STD_TIMEOUT)
    assert r.status_code == 200, f"signup failed {r.status_code}: {r.text[:300]}"
    data = r.json()
    return client_with(data["token"]), data["user"], data["token"], email, password


@pytest.fixture(scope="session")
def admin_client(admin_token):
    return client_with(admin_token)
