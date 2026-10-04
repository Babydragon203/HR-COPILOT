"""RBAC probe: are V2 talent-module writes gated by role? (recruitment writes are.)"""
import uuid
import requests
import pytest

from conftest import API, STD_TIMEOUT, signup_org, client_with


@pytest.fixture(scope="module")
def viewer():
    """Fresh org + a 'viewer' member invited by its org_admin."""
    admin, _, _, _, _ = signup_org("RbacTalent")
    email = f"test_viewer_{uuid.uuid4().hex[:8]}@example.com"
    r = admin.post(f"{API}/org/team", json={
        "email": email, "full_name": "TEST Viewer", "password": "TestPass@2026", "role": "viewer"},
        timeout=STD_TIMEOUT)
    assert r.status_code == 200, f"add member failed {r.status_code}: {r.text[:300]}"
    lg = requests.post(f"{API}/auth/login", json={"email": email, "password": "TestPass@2026"},
                       timeout=STD_TIMEOUT)
    assert lg.status_code == 200, lg.text[:300]
    return client_with(lg.json()["token"]), admin


class TestTalentRBAC:
    def test_viewer_cannot_create_job(self, viewer):
        v, _ = viewer
        r = v.post(f"{API}/jobs", json={"title": "TEST_RBAC Job", "department": "Eng"}, timeout=STD_TIMEOUT)
        assert r.status_code == 403, f"expected 403, got {r.status_code}"

    def test_viewer_framework_create(self, viewer):
        v, _ = viewer
        r = v.post(f"{API}/frameworks", json={
            "name": "TEST_RBAC FW", "competencies": [
                {"name": "Ownership", "required_level": 3, "weight": 100}]}, timeout=STD_TIMEOUT)
        print("VIEWER POST /frameworks ->", r.status_code)
        assert r.status_code == 403, f"RBAC GAP: viewer created a framework ({r.status_code})"

    def test_viewer_employee_create(self, viewer):
        v, _ = viewer
        r = v.post(f"{API}/employees", json={"name": "TEST_RBAC Emp"}, timeout=STD_TIMEOUT)
        print("VIEWER POST /employees ->", r.status_code)
        assert r.status_code == 403, f"RBAC GAP: viewer created an employee ({r.status_code})"

    def test_viewer_bulk_import(self, viewer):
        v, _ = viewer
        r = v.post(f"{API}/employees/bulk", json={"employees": [{"name": "TEST_RBAC Bulk"}]},
                   timeout=STD_TIMEOUT)
        print("VIEWER POST /employees/bulk ->", r.status_code)
        assert r.status_code == 403, f"RBAC GAP: viewer bulk-imported employees ({r.status_code})"

    def test_viewer_learning_generate_ai(self, viewer):
        """Viewer should not be able to burn AI credits."""
        v, admin = viewer
        r = v.post(f"{API}/learning/generate", json={
            "employee_id": uuid.uuid4().hex, "framework_id": uuid.uuid4().hex}, timeout=STD_TIMEOUT)
        print("VIEWER POST /learning/generate ->", r.status_code, r.text[:120])
        assert r.status_code == 403, f"RBAC GAP: viewer reached AI learning endpoint ({r.status_code})"

    def test_viewer_coach_chat_ai(self, viewer):
        v, _ = viewer
        r = v.post(f"{API}/coach/chat", json={
            "employee_id": uuid.uuid4().hex, "history": [], "message": "hi"}, timeout=STD_TIMEOUT)
        print("VIEWER POST /coach/chat ->", r.status_code, r.text[:120])
        assert r.status_code == 403, f"RBAC GAP: viewer reached AI coach endpoint ({r.status_code})"
