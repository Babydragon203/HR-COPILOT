"""Health, signup, login, /auth/me, bcrypt/cookie/CORS checks."""
import requests
import pytest

from conftest import API, STD_TIMEOUT, unique_email, client_with, signup_org


# ---- health ----
class TestHealth:
    def test_root(self, api):
        r = api.get(f"{API}/", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["service"] == "HR Copilot AI"
        assert d["status"] == "ok"


# ---- auth ----
class TestAuth:
    def test_signup_creates_org_and_admin(self):
        email = unique_email("signup")
        r = requests.post(f"{API}/auth/signup", json={
            "email": email, "password": "TestPass@2026", "full_name": "Signup User",
            "organization_name": "TEST_SignupOrg", "industry": "Tech", "company_size": "SMB",
        }, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert isinstance(d["token"], str) and len(d["token"]) > 20
        u = d["user"]
        assert u["email"] == email
        assert u["role"] == "org_admin"
        assert u["organization"]["name"] == "TEST_SignupOrg"
        assert u["organization"]["id"] == u["org_id"]
        # httpOnly cookie set
        ck = r.cookies.get("access_token")
        assert ck, "access_token cookie not set on signup"
        raw = r.headers.get("set-cookie", "")
        assert "httponly" in raw.lower(), f"cookie not httpOnly: {raw}"

    def test_signup_duplicate_email_400(self):
        email = unique_email("dup")
        payload = {"email": email, "password": "TestPass@2026", "full_name": "Dup",
                   "organization_name": "TEST_DupOrg"}
        r1 = requests.post(f"{API}/auth/signup", json=payload, timeout=STD_TIMEOUT)
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/auth/signup", json=payload, timeout=STD_TIMEOUT)
        assert r2.status_code == 400
        assert "already" in r2.json()["detail"].lower()

    def test_signup_short_password_422(self):
        r = requests.post(f"{API}/auth/signup", json={
            "email": unique_email("short"), "password": "abc", "full_name": "S",
            "organization_name": "TEST_Short"}, timeout=STD_TIMEOUT)
        assert r.status_code == 422

    def test_login_seeded_admin(self, api, test_credentials):
        r = api.post(f"{API}/auth/login", json=test_credentials, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["user"]["email"] == test_credentials["email"].lower()
        assert d["user"]["role"] == "org_admin"
        assert d["user"]["organization"]["name"]
        assert isinstance(d["token"], str)
        assert "httponly" in r.headers.get("set-cookie", "").lower()

    def test_login_wrong_password_401(self, api, test_credentials):
        r = api.post(f"{API}/auth/login", json={
            "email": test_credentials["email"], "password": "WrongPass@123"}, timeout=STD_TIMEOUT)
        assert r.status_code == 401
        assert "invalid" in r.json()["detail"].lower()

    def test_login_unknown_email_401(self, api):
        r = api.post(f"{API}/auth/login", json={
            "email": unique_email("nouser"), "password": "WhateverPass1"}, timeout=STD_TIMEOUT)
        assert r.status_code == 401

    def test_me_with_bearer(self, admin_client, test_credentials):
        r = admin_client.get(f"{API}/auth/me", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["email"] == test_credentials["email"].lower()
        assert "organization" in d and d["organization"]["id"]
        assert "password_hash" not in d

    def test_me_without_token_401(self):
        r = requests.get(f"{API}/auth/me", timeout=STD_TIMEOUT)
        assert r.status_code == 401

    def test_me_with_bad_token_401(self):
        r = requests.get(f"{API}/auth/me",
                         headers={"Authorization": "Bearer not.a.jwt"}, timeout=STD_TIMEOUT)
        assert r.status_code == 401

    def test_me_with_cookie_only(self, api, test_credentials):
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json=test_credentials, timeout=STD_TIMEOUT)
        assert r.status_code == 200
        r2 = s.get(f"{API}/auth/me", timeout=STD_TIMEOUT)
        assert r2.status_code == 200, "cookie-based auth failed"

    def test_logout(self, api, test_credentials):
        s = requests.Session()
        s.post(f"{API}/auth/login", json=test_credentials, timeout=STD_TIMEOUT)
        r = s.post(f"{API}/auth/logout", timeout=STD_TIMEOUT)
        assert r.status_code == 200 and r.json()["ok"] is True

    def test_bcrypt_hash_format(self):
        """Password hash stored must be bcrypt $2b$."""
        import asyncio, os, sys
        sys.path.insert(0, "/app/backend")
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        from motor.motor_asyncio import AsyncIOMotorClient

        async def _check():
            cl = AsyncIOMotorClient(os.environ["MONGO_URL"])
            u = await cl[os.environ["DB_NAME"]].users.find_one({"email": os.environ["ADMIN_EMAIL"].lower()})
            cl.close()
            return u
        u = asyncio.run(_check())
        assert u is not None, "seeded admin not found in DB"
        assert u["password_hash"].startswith("$2b$"), f"unexpected hash prefix: {u['password_hash'][:6]}"

    def test_cors_allows_credentials(self, test_credentials):
        origin = "https://ai-hr-workflows.preview.emergentagent.com"
        r = requests.post(f"{API}/auth/login", json=test_credentials,
                          headers={"Origin": origin}, timeout=STD_TIMEOUT)
        acao = r.headers.get("access-control-allow-origin")
        acac = r.headers.get("access-control-allow-credentials")
        assert acac == "true", f"allow-credentials missing: {dict(r.headers)}"
        assert acao != "*", (
            "CORS uses wildcard origin with allow_credentials=True — browsers reject "
            f"credentialed requests. allow-origin={acao}"
        )

    def test_brute_force_lockout(self, test_credentials):
        """6 wrong-password attempts should trigger 423/429 lockout per playbook."""
        email = test_credentials["email"]
        codes = []
        for _ in range(6):
            r = requests.post(f"{API}/auth/login",
                              json={"email": email, "password": "DefinitelyWrong@1"},
                              timeout=STD_TIMEOUT)
            codes.append(r.status_code)
        assert any(c in (423, 429) for c in codes), (
            f"No brute-force lockout after 6 failed logins; codes={codes}")


# ---- team management / RBAC gating on org routes ----
class TestTeam:
    def test_admin_can_add_member_and_list(self):
        adm, user, _, _, _ = signup_org("TeamA")
        email = unique_email("rec")
        r = adm.post(f"{API}/org/team", json={
            "email": email, "full_name": "Rec One", "role": "recruiter",
            "password": "TestPass@2026"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["role"] == "recruiter" and d["email"] == email
        assert "password_hash" not in d and "_id" not in d
        lst = adm.get(f"{API}/org/team", timeout=STD_TIMEOUT)
        assert lst.status_code == 200
        emails = [m["email"] for m in lst.json()]
        assert email in emails
        for m in lst.json():
            assert "password_hash" not in m

    def test_invalid_role_rejected(self):
        adm, _, _, _, _ = signup_org("TeamB")
        r = adm.post(f"{API}/org/team", json={
            "email": unique_email("badrole"), "full_name": "X", "role": "superuser",
            "password": "TestPass@2026"}, timeout=STD_TIMEOUT)
        assert r.status_code == 400, f"expected 400 for invalid role, got {r.status_code}"

    def test_non_admin_cannot_add_member(self):
        adm, _, _, _, _ = signup_org("TeamC")
        rec_email = unique_email("rec2")
        adm.post(f"{API}/org/team", json={
            "email": rec_email, "full_name": "Rec", "role": "recruiter",
            "password": "TestPass@2026"}, timeout=STD_TIMEOUT)
        lr = requests.post(f"{API}/auth/login", json={"email": rec_email, "password": "TestPass@2026"},
                           timeout=STD_TIMEOUT)
        assert lr.status_code == 200
        rc = client_with(lr.json()["token"])
        r = rc.post(f"{API}/org/team", json={
            "email": unique_email("x"), "full_name": "X", "role": "viewer",
            "password": "TestPass@2026"}, timeout=STD_TIMEOUT)
        assert r.status_code == 403
