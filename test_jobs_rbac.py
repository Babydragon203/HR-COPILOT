"""Jobs CRUD, RBAC role matrix, and multi-tenant isolation."""
import requests
import pytest

from conftest import API, STD_TIMEOUT, unique_email, client_with, signup_org


def add_member(admin, role, prefix):
    email = unique_email(prefix)
    r = admin.post(f"{API}/org/team", json={
        "email": email, "full_name": f"{role} user", "role": role,
        "password": "TestPass@2026"}, timeout=STD_TIMEOUT)
    assert r.status_code == 200, f"add {role} failed: {r.text[:200]}"
    lr = requests.post(f"{API}/auth/login", json={"email": email, "password": "TestPass@2026"},
                       timeout=STD_TIMEOUT)
    assert lr.status_code == 200, f"{role} login failed: {lr.text[:200]}"
    assert lr.json()["user"]["role"] == role
    return client_with(lr.json()["token"])


JOB_PAYLOAD = {
    "title": "TEST_Backend Engineer", "department": "Engineering", "seniority": "Senior",
    "experience": "5-8 years", "employment_type": "Full-time", "work_arrangement": "Remote",
    "location": "Remote", "salary_currency": "USD", "salary_min": 100000, "salary_max": 150000,
    "priority": "High", "timeline": "Immediate",
    "required_skills": ["Python", "FastAPI", "MongoDB"], "preferred_skills": ["AWS"],
    "qualifications": ["BS in CS"], "status": "open",
}


class TestJobsCRUD:
    def test_create_validation_salary(self, admin_client):
        bad = {**JOB_PAYLOAD, "salary_min": 200000, "salary_max": 100000}
        r = admin_client.post(f"{API}/jobs", json=bad, timeout=STD_TIMEOUT)
        assert r.status_code == 400
        assert "salary" in r.json()["detail"].lower()

    def test_full_crud_lifecycle(self, admin_client):
        # CREATE
        r = admin_client.post(f"{API}/jobs", json=JOB_PAYLOAD, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        job = r.json()
        assert job["is_demo"] is False
        assert job["title"] == JOB_PAYLOAD["title"]
        assert job["required_skills"] == JOB_PAYLOAD["required_skills"]
        assert "_id" not in job
        assert isinstance(job["id"], str)
        jid = job["id"]

        # GET single
        g = admin_client.get(f"{API}/jobs/{jid}", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        assert g.json()["title"] == JOB_PAYLOAD["title"]
        assert "_id" not in g.json()

        # LIST
        l = admin_client.get(f"{API}/jobs", timeout=STD_TIMEOUT)
        assert l.status_code == 200
        assert jid in [j["id"] for j in l.json()]

        # PATCH status + stage
        p = admin_client.patch(f"{API}/jobs/{jid}", json={"status": "on_hold", "stage": "sourcing"},
                               timeout=STD_TIMEOUT)
        assert p.status_code == 200
        assert p.json()["status"] == "on_hold"
        assert p.json()["stage"] == "sourcing"
        g2 = admin_client.get(f"{API}/jobs/{jid}", timeout=STD_TIMEOUT)
        assert g2.json()["status"] == "on_hold"

        # DELETE + verify
        d = admin_client.delete(f"{API}/jobs/{jid}", timeout=STD_TIMEOUT)
        assert d.status_code == 200 and d.json()["ok"] is True
        assert admin_client.get(f"{API}/jobs/{jid}", timeout=STD_TIMEOUT).status_code == 404

    def test_get_nonexistent_404(self, admin_client):
        assert admin_client.get(f"{API}/jobs/does-not-exist", timeout=STD_TIMEOUT).status_code == 404

    def test_patch_nonexistent_404(self, admin_client):
        r = admin_client.patch(f"{API}/jobs/does-not-exist", json={"status": "open"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_delete_nonexistent_404(self, admin_client):
        assert admin_client.delete(f"{API}/jobs/does-not-exist", timeout=STD_TIMEOUT).status_code == 404

    def test_jobs_requires_auth(self):
        assert requests.get(f"{API}/jobs", timeout=STD_TIMEOUT).status_code == 401

    def test_missing_title_422(self, admin_client):
        r = admin_client.post(f"{API}/jobs", json={"department": "Eng"}, timeout=STD_TIMEOUT)
        assert r.status_code == 422


class TestRBAC:
    @pytest.fixture(scope="class")
    def org(self):
        adm, user, token, email, pw = signup_org("Rbac")
        return {"admin": adm, "user": user}

    def test_recruiter_can_create_job(self, org):
        rc = add_member(org["admin"], "recruiter", "rbacrec")
        r = rc.post(f"{API}/jobs", json={**JOB_PAYLOAD, "title": "TEST_Recruiter Job"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["title"] == "TEST_Recruiter Job"

    def test_viewer_cannot_create_job(self, org):
        vc = add_member(org["admin"], "viewer", "rbacview")
        r = vc.post(f"{API}/jobs", json=JOB_PAYLOAD, timeout=STD_TIMEOUT)
        assert r.status_code == 403, f"viewer got {r.status_code}"
        # but can read
        assert vc.get(f"{API}/jobs", timeout=STD_TIMEOUT).status_code == 200

    def test_hiring_manager_cannot_create_job(self, org):
        hc = add_member(org["admin"], "hiring_manager", "rbachm")
        r = hc.post(f"{API}/jobs", json=JOB_PAYLOAD, timeout=STD_TIMEOUT)
        assert r.status_code == 403, f"hiring_manager got {r.status_code}"
        assert hc.get(f"{API}/jobs", timeout=STD_TIMEOUT).status_code == 200

    def test_viewer_cannot_delete_or_update(self, org):
        vc = add_member(org["admin"], "viewer", "rbacview2")
        created = org["admin"].post(f"{API}/jobs", json=JOB_PAYLOAD, timeout=STD_TIMEOUT).json()
        assert vc.patch(f"{API}/jobs/{created['id']}", json={"status": "closed"},
                        timeout=STD_TIMEOUT).status_code == 403
        assert vc.delete(f"{API}/jobs/{created['id']}", timeout=STD_TIMEOUT).status_code == 403

    def test_viewer_cannot_create_candidate_or_upload(self, org):
        vc = add_member(org["admin"], "viewer", "rbacview3")
        r = vc.post(f"{API}/candidates", json={"name": "TEST_X"}, timeout=STD_TIMEOUT)
        assert r.status_code == 403

    def test_non_admin_cannot_seed_or_clear_demo(self, org):
        rc = add_member(org["admin"], "recruiter", "rbacdemo")
        assert rc.post(f"{API}/demo/seed", timeout=STD_TIMEOUT).status_code == 403
        assert rc.delete(f"{API}/demo/clear", timeout=STD_TIMEOUT).status_code == 403


class TestTenantIsolation:
    @pytest.fixture(scope="class")
    def orgs(self):
        a_client, a_user, _, _, _ = signup_org("IsoA")
        b_client, b_user, _, _, _ = signup_org("IsoB")
        assert a_user["org_id"] != b_user["org_id"]
        job = a_client.post(f"{API}/jobs", json={**JOB_PAYLOAD, "title": "TEST_OrgA Job"},
                            timeout=STD_TIMEOUT)
        assert job.status_code == 200, job.text[:200]
        cand = a_client.post(f"{API}/candidates", json={
            "name": "TEST_OrgA Candidate", "email": "orga.cand@example.com",
            "job_id": job.json()["id"], "stage": "applied"}, timeout=STD_TIMEOUT)
        assert cand.status_code == 200, cand.text[:200]
        return {"a": a_client, "b": b_client, "job_id": job.json()["id"],
                "cand_id": cand.json()["id"]}

    def test_orgb_jobs_list_empty(self, orgs):
        r = orgs["b"].get(f"{API}/jobs", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        assert r.json() == [], f"OrgB sees OrgA data: {r.json()}"

    def test_orgb_cannot_get_orga_job(self, orgs):
        r = orgs["b"].get(f"{API}/jobs/{orgs['job_id']}", timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_orgb_cannot_patch_or_delete_orga_job(self, orgs):
        assert orgs["b"].patch(f"{API}/jobs/{orgs['job_id']}", json={"status": "closed"},
                               timeout=STD_TIMEOUT).status_code == 404
        assert orgs["b"].delete(f"{API}/jobs/{orgs['job_id']}",
                                timeout=STD_TIMEOUT).status_code == 404
        # OrgA job still intact
        assert orgs["a"].get(f"{API}/jobs/{orgs['job_id']}", timeout=STD_TIMEOUT).status_code == 200

    def test_orgb_cannot_get_orga_candidate(self, orgs):
        r = orgs["b"].get(f"{API}/candidates/{orgs['cand_id']}", timeout=STD_TIMEOUT)
        assert r.status_code == 404
        assert orgs["b"].get(f"{API}/candidates", timeout=STD_TIMEOUT).json() == []

    def test_orgb_overview_isolated(self, orgs):
        r = orgs["b"].get(f"{API}/overview", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        assert r.json()["total_jobs"] == 0
        assert r.json()["candidates"] == 0

    def test_orgb_team_isolated(self, orgs):
        r = orgs["b"].get(f"{API}/org/team", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestCandidates:
    @pytest.fixture(scope="class")
    def setup(self, admin_token):
        c = client_with(admin_token)
        job = c.post(f"{API}/jobs", json={**JOB_PAYLOAD, "title": "TEST_Cand Job"},
                     timeout=STD_TIMEOUT).json()
        cand = c.post(f"{API}/candidates", json={
            "name": "TEST_Jane Doe", "email": "jane.test@example.com",
            "job_id": job["id"], "stage": "applied"}, timeout=STD_TIMEOUT)
        assert cand.status_code == 200, cand.text[:200]
        yield {"client": c, "job": job, "cand": cand.json()}
        c.delete(f"{API}/candidates/{cand.json()['id']}", timeout=STD_TIMEOUT)
        c.delete(f"{API}/jobs/{job['id']}", timeout=STD_TIMEOUT)

    def test_create_invalid_stage_400(self, setup):
        r = setup["client"].post(f"{API}/candidates", json={"name": "TEST_Bad", "stage": "nonsense"},
                                 timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_valid_stage_transitions(self, setup):
        c, cid = setup["client"], setup["cand"]["id"]
        for stage in ["screening", "shortlisted", "interview", "offer", "hired"]:
            r = c.patch(f"{API}/candidates/{cid}", json={"stage": stage}, timeout=STD_TIMEOUT)
            assert r.status_code == 200, r.text[:200]
            assert r.json()["stage"] == stage
        g = c.get(f"{API}/candidates/{cid}", timeout=STD_TIMEOUT)
        assert g.json()["stage"] == "hired"

    def test_patch_invalid_stage_400(self, setup):
        r = setup["client"].patch(f"{API}/candidates/{setup['cand']['id']}",
                                  json={"stage": "bogus"}, timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_list_filter_by_job(self, setup):
        r = setup["client"].get(f"{API}/candidates?job_id={setup['job']['id']}", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        assert all(c["job_id"] == setup["job"]["id"] for c in r.json())
        assert setup["cand"]["id"] in [c["id"] for c in r.json()]

    def test_patch_nonexistent_404(self, setup):
        r = setup["client"].patch(f"{API}/candidates/nope", json={"stage": "applied"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404
