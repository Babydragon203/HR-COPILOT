"""Overview dashboard, demo data seed/clear, and audit logging."""
import pytest

from conftest import API, STD_TIMEOUT, signup_org

STAGES = ["applied", "screening", "shortlisted", "interview", "assessment",
          "offer", "hired", "rejected", "withdrawn"]


class TestOverviewDemoAudit:
    @pytest.fixture(scope="class")
    def org(self):
        c, user, _, _, _ = signup_org("Dash")
        return {"c": c, "user": user}

    def test_overview_empty_shape(self, org):
        r = org["c"].get(f"{API}/overview", timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        for k in ["active_jobs", "total_jobs", "candidates", "interviews",
                  "pending_interviews", "job_descriptions", "pipeline", "recent_activity"]:
            assert k in d, f"missing {k}"
        assert sorted(d["pipeline"].keys()) == sorted(STAGES)
        assert isinstance(d["recent_activity"], list)
        assert d["total_jobs"] == 0 and d["candidates"] == 0

    def test_demo_seed_and_overview(self, org):
        r = org["c"].post(f"{API}/demo/seed", timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["ok"] is True and d["jobs"] == 3 and d["candidates"] == 4

        jobs = org["c"].get(f"{API}/jobs", timeout=STD_TIMEOUT).json()
        assert len(jobs) == 3
        assert all(j["is_demo"] is True for j in jobs)
        cands = org["c"].get(f"{API}/candidates", timeout=STD_TIMEOUT).json()
        assert len(cands) == 4
        assert all(c["is_demo"] is True for c in cands)
        assert all(c["analysis"]["overall_score"] >= 0 for c in cands)

        ov = org["c"].get(f"{API}/overview", timeout=STD_TIMEOUT).json()
        assert ov["total_jobs"] == 3 and ov["active_jobs"] == 3
        assert ov["candidates"] == 4
        assert sum(ov["pipeline"].values()) == 4

    def test_demo_seed_idempotent(self, org):
        r = org["c"].post(f"{API}/demo/seed", timeout=STD_TIMEOUT)
        assert r.status_code == 200
        assert r.json().get("already_loaded") is True
        assert len(org["c"].get(f"{API}/jobs", timeout=STD_TIMEOUT).json()) == 3

    def test_audit_logs_recorded(self, org):
        """recent_activity is fed from audit_logs scoped to the org."""
        org["c"].post(f"{API}/jobs", json={"title": "TEST_Audit Job", "status": "open"},
                      timeout=STD_TIMEOUT)
        ov = org["c"].get(f"{API}/overview", timeout=STD_TIMEOUT).json()
        acts = ov["recent_activity"]
        assert len(acts) > 0, "no audit entries"
        actions = [a["action"] for a in acts]
        assert "job.create" in actions
        assert "demo.seed" in actions
        for a in acts:
            assert a["org_id"] == org["user"]["org_id"], "audit log leaked across orgs"
            assert a["user_email"] == org["user"]["email"]
            assert "_id" not in a

    def test_demo_clear_only_demo_records(self, org):
        # non-demo job created in previous test must survive
        r = org["c"].delete(f"{API}/demo/clear", timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        removed = r.json()["removed"]
        assert removed["jobs"] == 3 and removed["candidates"] == 4
        jobs = org["c"].get(f"{API}/jobs", timeout=STD_TIMEOUT).json()
        assert len(jobs) == 1 and jobs[0]["title"] == "TEST_Audit Job"
        assert org["c"].get(f"{API}/candidates", timeout=STD_TIMEOUT).json() == []
        # re-seed allowed after clear
        again = org["c"].post(f"{API}/demo/seed", timeout=STD_TIMEOUT)
        assert again.status_code == 200 and again.json().get("jobs") == 3
        org["c"].delete(f"{API}/demo/clear", timeout=STD_TIMEOUT)

    def test_overview_requires_auth(self):
        import requests
        assert requests.get(f"{API}/overview", timeout=STD_TIMEOUT).status_code == 401

    def test_org_get_and_patch(self, org):
        g = org["c"].get(f"{API}/org", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        assert "_id" not in g.json()
        p = org["c"].patch(f"{API}/org", json={"industry": "Healthcare"}, timeout=STD_TIMEOUT)
        assert p.status_code == 200
        assert p.json()["industry"] == "Healthcare"
        assert org["c"].get(f"{API}/org", timeout=STD_TIMEOUT).json()["industry"] == "Healthcare"
