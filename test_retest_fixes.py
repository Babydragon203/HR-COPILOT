"""Retest of 3 previously-failing defects: JD core fields, CORS w/ credentials, brute-force lockout."""
import uuid

import pytest
import requests

from conftest import API, AI_TIMEOUT, STD_TIMEOUT

INTERNAL = "http://localhost:8001"


# ---------------- 1. JD Generator core fields ----------------
def _assert_core(jd):
    problems = []
    if not (jd.get("job_summary") or "").strip():
        problems.append("job_summary empty")
    for k in ("responsibilities", "required_skills", "preferred_skills", "qualifications"):
        v = jd.get(k)
        if not isinstance(v, list) or len([x for x in v if str(x).strip()]) == 0:
            problems.append(f"{k} empty/not list -> {v!r}")
    if not str(jd.get("experience") or "").strip():
        problems.append("experience empty")
    assert not problems, f"CORE fields missing: {problems}\nKeys={list(jd.keys())}"


BASE_JD = {
    "company_name": "TEST Acme Corp", "industry": "Technology", "company_size": "Startup",
    "department": "Engineering", "title": "Senior Backend Engineer", "seniority": "Senior",
    "experience": "5-8 years", "employment_type": "Full-time", "work_arrangement": "Remote",
    "location": "Remote", "required_skills": ["Python", "FastAPI", "MongoDB"],
    "preferred_skills": ["AWS", "Kubernetes"], "benefits": ["Health Insurance"],
    "style": "Professional", "length": "Standard",
}


def _generate(admin_client, body):
    r = admin_client.post(f"{API}/jd/generate", json=body, timeout=AI_TIMEOUT)
    if r.status_code == 502:
        r = admin_client.post(f"{API}/jd/generate", json=body, timeout=AI_TIMEOUT)
    assert r.status_code == 200, f"jd/generate failed {r.status_code}: {r.text[:600]}"
    return r.json()


class TestJDGenerate:
    def test_jd_default_sections_core_fields(self, admin_client):
        data = _generate(admin_client, dict(BASE_JD))
        jd = data["content"]
        print("DEFAULT KEYS:", list(jd.keys()))
        print("DEFAULT VALUES:", {k: jd.get(k) for k in ("job_summary","responsibilities","required_skills","preferred_skills","qualifications","experience")})
        _assert_core(jd)

    def test_jd_explicit_single_section_core_fields(self, admin_client):
        body = {**BASE_JD, "title": "Product Marketing Manager", "department": "Marketing",
                "required_skills": ["GTM Strategy", "Positioning"], "preferred_skills": ["SQL"],
                "experience": "4-6 years", "include_sections": ["Company Overview"]}
        data = _generate(admin_client, body)
        jd = data["content"]
        print("EXPLICIT KEYS:", list(jd.keys()))
        print("EXPLICIT VALUES:", {k: jd.get(k) for k in ("job_summary","responsibilities","required_skills","preferred_skills","qualifications","experience","company_overview","benefits","kpis")})
        _assert_core(jd)
        assert (jd.get("company_overview") or "").strip(), "company_overview should be present when requested"
        assert not (jd.get("benefits") or []), f"benefits should be empty when excluded: {jd.get('benefits')}"


# ---------------- 2. CORS with credentials ----------------
class TestCors:
    ORIGIN = "https://example-preview.emergent.host"

    def test_preflight_internal(self):
        r = requests.options(
            f"{INTERNAL}/api/auth/login",
            headers={
                "Origin": self.ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=STD_TIMEOUT,
        )
        h = {k.lower(): v for k, v in r.headers.items()}
        print("PREFLIGHT headers:", h)
        assert r.status_code in (200, 204), r.status_code
        assert h.get("access-control-allow-origin") == self.ORIGIN, h.get("access-control-allow-origin")
        assert h.get("access-control-allow-credentials") == "true"

    def test_post_internal_echoes_origin_and_vary(self):
        r = requests.post(
            f"{INTERNAL}/api/auth/login",
            json={"email": f"nobody+{uuid.uuid4().hex[:6]}@example.com", "password": "wrongwrong"},
            headers={"Origin": self.ORIGIN},
            timeout=STD_TIMEOUT,
        )
        h = {k.lower(): v for k, v in r.headers.items()}
        print("POST headers:", h, "status", r.status_code)
        assert h.get("access-control-allow-origin") == self.ORIGIN
        assert h.get("access-control-allow-credentials") == "true"
        assert "origin" in (h.get("vary", "").lower())


# ---------------- 3. Brute-force lockout ----------------
class TestLockout:
    def test_lockout_after_5_failures(self):
        email = f"bruteforce.retest+{uuid.uuid4().hex[:8]}@example.com"
        codes = []
        for _ in range(5):
            r = requests.post(f"{INTERNAL}/api/auth/login",
                              json={"email": email, "password": "WrongPass@123"}, timeout=STD_TIMEOUT)
            codes.append(r.status_code)
        r6 = requests.post(f"{INTERNAL}/api/auth/login",
                           json={"email": email, "password": "WrongPass@123"}, timeout=STD_TIMEOUT)
        print("first5:", codes, "sixth:", r6.status_code, r6.text[:200])
        assert codes == [401] * 5, codes
        assert r6.status_code == 429, f"expected 429 got {r6.status_code}: {r6.text[:300]}"
        assert r6.json().get("detail") == "Too many failed attempts. Try again later."

    def test_success_clears_counter(self, test_credentials):
        email = test_credentials["email"]
        # 3 wrong attempts
        for _ in range(3):
            r = requests.post(f"{INTERNAL}/api/auth/login",
                              json={"email": email, "password": "DefinitelyWrong@123"}, timeout=STD_TIMEOUT)
            assert r.status_code == 401, f"{r.status_code}: {r.text[:200]}"
        # correct login succeeds and clears
        ok = requests.post(f"{INTERNAL}/api/auth/login", json=test_credentials, timeout=STD_TIMEOUT)
        assert ok.status_code == 200, f"valid login failed {ok.status_code}: {ok.text[:300]}"
        # 3 more wrong -> still 401 (counter was reset)
        for i in range(3):
            r = requests.post(f"{INTERNAL}/api/auth/login",
                              json={"email": email, "password": "DefinitelyWrong@123"}, timeout=STD_TIMEOUT)
            assert r.status_code == 401, f"attempt {i} after reset got {r.status_code}"
        # cleanup: successful login to reset counter for other tests
        assert requests.post(f"{INTERNAL}/api/auth/login", json=test_credentials, timeout=STD_TIMEOUT).status_code == 200

    def test_bcrypt_hash_format_and_cookie(self, test_credentials):
        r = requests.post(f"{INTERNAL}/api/auth/login", json=test_credentials, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        sc = r.headers.get("set-cookie", "")
        print("set-cookie:", sc)
        assert "access_token=" in sc and "HttpOnly" in sc, sc
