"""V2 Talent Development tests: competencies, frameworks, employees, mappings,
skill-gap analyzer, development plans, candidate comparison."""
import uuid

import pytest
import requests

from conftest import API, STD_TIMEOUT, AI_TIMEOUT, signup_org, client_with


# ---------------- module: competencies ----------------
class TestCompetencies:
    def test_list_defaults(self, admin_client):
        r = admin_client.get(f"{API}/competencies", timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert isinstance(data, list)
        globals_ = [c for c in data if c.get("is_global")]
        assert len(globals_) >= 50, f"expected >=50 global competencies, got {len(globals_)}"
        sample = globals_[0]
        for k in ("id", "name", "category", "is_global"):
            assert k in sample
        # no mongo _id leaked
        assert all("_id" not in c for c in data)

    def test_create_custom_competency(self, admin_client):
        name = f"TEST_Comp_{uuid.uuid4().hex[:6]}"
        r = admin_client.post(f"{API}/competencies",
                              json={"name": name, "category": "TEST_Cat", "description": "d"},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        doc = r.json()
        assert doc["name"] == name
        assert doc["category"] == "TEST_Cat"
        assert doc["is_global"] is False
        assert "_id" not in doc
        # verify persisted via GET
        lst = admin_client.get(f"{API}/competencies", timeout=STD_TIMEOUT).json()
        assert any(c["name"] == name and not c.get("is_global") for c in lst)

    def test_requires_auth(self):
        # fresh session (no cookies/headers) -> must be rejected
        r = requests.get(f"{API}/competencies", timeout=STD_TIMEOUT)
        assert r.status_code in (401, 403), r.status_code


# ---------------- module: frameworks ----------------
FW_OK = {
    "name": "TEST_FW_Engineer",
    "department": "Engineering",
    "role": "Senior Engineer",
    "competencies": [
        {"name": "Technical Depth", "category": "Technical", "required_level": 5, "weight": 40},
        {"name": "System Design", "category": "Technical", "required_level": 4, "weight": 35},
        {"name": "Ownership", "category": "Execution", "required_level": 4, "weight": 25},
    ],
}


@pytest.fixture(scope="class")
def created(admin_client):
    """Track ids for cleanup."""
    bag = {"frameworks": [], "employees": []}
    yield bag
    for fid in bag["frameworks"]:
        admin_client.delete(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT)
    for eid in bag["employees"]:
        admin_client.delete(f"{API}/employees/{eid}", timeout=STD_TIMEOUT)


class TestFrameworks:
    def test_create_and_get(self, admin_client, created):
        r = admin_client.post(f"{API}/frameworks", json=FW_OK, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        doc = r.json()
        created["frameworks"].append(doc["id"])
        assert doc["name"] == FW_OK["name"]
        assert len(doc["competencies"]) == 3
        assert sum(c["weight"] for c in doc["competencies"]) == 100
        assert "_id" not in doc
        g = admin_client.get(f"{API}/frameworks/{doc['id']}", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        assert g.json()["name"] == FW_OK["name"]
        lst = admin_client.get(f"{API}/frameworks", timeout=STD_TIMEOUT)
        assert lst.status_code == 200
        assert any(f["id"] == doc["id"] for f in lst.json())

    def test_weights_not_100(self, admin_client):
        bad = dict(FW_OK)
        bad = {**FW_OK, "competencies": [
            {"name": "A", "required_level": 3, "weight": 50},
            {"name": "B", "required_level": 3, "weight": 30}]}
        r = admin_client.post(f"{API}/frameworks", json=bad, timeout=STD_TIMEOUT)
        assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"
        assert "100" in r.json()["detail"]

    def test_empty_competencies(self, admin_client):
        r = admin_client.post(f"{API}/frameworks", json={**FW_OK, "competencies": []},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("lvl", [0, 6, -1])
    def test_required_level_out_of_range(self, admin_client, lvl):
        r = admin_client.post(f"{API}/frameworks", json={**FW_OK, "competencies": [
            {"name": "A", "required_level": lvl, "weight": 100}]}, timeout=STD_TIMEOUT)
        assert r.status_code == 422, f"{r.status_code} {r.text[:200]}"

    def test_delete_and_404(self, admin_client):
        r = admin_client.post(f"{API}/frameworks", json={**FW_OK, "name": "TEST_FW_Del"},
                              timeout=STD_TIMEOUT)
        fid = r.json()["id"]
        d = admin_client.delete(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT)
        assert d.status_code == 200 and d.json().get("ok") is True
        assert admin_client.get(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT).status_code == 404
        assert admin_client.delete(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT).status_code == 404


class TestOrgIsolation:
    def test_framework_and_employee_isolation(self, admin_client, created):
        a = admin_client.post(f"{API}/frameworks", json={**FW_OK, "name": "TEST_FW_OrgA"},
                              timeout=STD_TIMEOUT)
        assert a.status_code == 200
        fid = a.json()["id"]
        created["frameworks"].append(fid)
        ea = admin_client.post(f"{API}/employees", json={"name": "TEST_EmpA"}, timeout=STD_TIMEOUT)
        assert ea.status_code == 200
        eid = ea.json()["id"]
        created["employees"].append(eid)

        b_client, _, _, _, _ = signup_org("OrgB")
        assert b_client.get(f"{API}/frameworks", timeout=STD_TIMEOUT).json() == []
        assert b_client.get(f"{API}/employees", timeout=STD_TIMEOUT).json() == []
        assert b_client.get(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT).status_code == 404
        assert b_client.delete(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT).status_code == 404
        assert b_client.delete(f"{API}/employees/{eid}", timeout=STD_TIMEOUT).status_code == 404
        # OrgA framework still exists after OrgB delete attempt
        assert admin_client.get(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT).status_code == 200
        # OrgB custom competency invisible to OrgA
        cb = b_client.post(f"{API}/competencies",
                           json={"name": "TEST_OrgB_Comp", "category": "X"}, timeout=STD_TIMEOUT)
        assert cb.status_code == 200
        a_comps = admin_client.get(f"{API}/competencies", timeout=STD_TIMEOUT).json()
        assert not any(c["name"] == "TEST_OrgB_Comp" for c in a_comps)
        # cross-org mapping attempt
        mb = b_client.post(f"{API}/employee-mappings", json={
            "employee_id": eid, "framework_id": fid,
            "mappings": [{"competency": "Technical Depth", "current_level": 3}]},
            timeout=STD_TIMEOUT)
        assert mb.status_code == 404


# ---------------- module: employees ----------------
class TestEmployees:
    def test_create_list_delete(self, admin_client):
        r = admin_client.post(f"{API}/employees", json={
            "name": "TEST_Emp_CRUD", "email": "test_emp@example.com",
            "role": "Engineer", "department": "Engineering"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        doc = r.json()
        assert doc["name"] == "TEST_Emp_CRUD"
        assert doc["role"] == "Engineer"
        assert "_id" not in doc
        lst = admin_client.get(f"{API}/employees", timeout=STD_TIMEOUT).json()
        assert any(e["id"] == doc["id"] for e in lst)
        d = admin_client.delete(f"{API}/employees/{doc['id']}", timeout=STD_TIMEOUT)
        assert d.status_code == 200
        lst2 = admin_client.get(f"{API}/employees", timeout=STD_TIMEOUT).json()
        assert not any(e["id"] == doc["id"] for e in lst2)
        assert admin_client.delete(f"{API}/employees/{doc['id']}",
                                   timeout=STD_TIMEOUT).status_code == 404


# ---------------- module: mappings + skill gaps + dev plans ----------------
@pytest.fixture(scope="class")
def gap_ctx(admin_client):
    fw = admin_client.post(f"{API}/frameworks", json={
        "name": "TEST_FW_Gap", "role": "EM",
        "competencies": [
            {"name": "Technical Depth", "category": "Technical", "required_level": 5, "weight": 40},
            {"name": "People Management", "category": "Leadership", "required_level": 4, "weight": 35},
            {"name": "Ownership", "category": "Execution", "required_level": 3, "weight": 25},
        ]}, timeout=STD_TIMEOUT)
    assert fw.status_code == 200, fw.text[:300]
    emp = admin_client.post(f"{API}/employees", json={"name": "TEST_Emp_Gap", "role": "TL"},
                            timeout=STD_TIMEOUT)
    assert emp.status_code == 200, emp.text[:300]
    ctx = {"fw_id": fw.json()["id"], "emp_id": emp.json()["id"]}
    yield ctx
    admin_client.delete(f"{API}/employees/{ctx['emp_id']}", timeout=STD_TIMEOUT)
    admin_client.delete(f"{API}/frameworks/{ctx['fw_id']}", timeout=STD_TIMEOUT)


class TestMappingsAndGaps:
    def test_mapping_upsert(self, admin_client, gap_ctx):
        payload = {
            "employee_id": gap_ctx["emp_id"], "framework_id": gap_ctx["fw_id"],
            "mappings": [
                {"competency": "Technical Depth", "current_level": 2, "target_level": 5},
                {"competency": "People Management", "current_level": 3, "target_level": 4},
            ],
        }
        r1 = admin_client.post(f"{API}/employee-mappings", json=payload, timeout=STD_TIMEOUT)
        assert r1.status_code == 200, r1.text[:300]
        doc1 = r1.json()
        assert doc1["employee_id"] == gap_ctx["emp_id"]
        assert len(doc1["mappings"]) == 2
        assert "_id" not in doc1

        payload["mappings"][0]["current_level"] = 4
        r2 = admin_client.post(f"{API}/employee-mappings", json=payload, timeout=STD_TIMEOUT)
        assert r2.status_code == 200
        doc2 = r2.json()
        assert doc2["id"] == doc1["id"], "upsert must reuse the same doc id"

        lst = admin_client.get(f"{API}/employee-mappings",
                               params={"employee_id": gap_ctx["emp_id"],
                                       "framework_id": gap_ctx["fw_id"]},
                               timeout=STD_TIMEOUT).json()
        assert len(lst) == 1, f"expected exactly 1 mapping doc, got {len(lst)}"
        td = [m for m in lst[0]["mappings"] if m["competency"] == "Technical Depth"][0]
        assert td["current_level"] == 4, "update not persisted"

        # restore level 2 for the gap analysis test
        payload["mappings"][0]["current_level"] = 2
        admin_client.post(f"{API}/employee-mappings", json=payload, timeout=STD_TIMEOUT)

    def test_mapping_missing_refs(self, admin_client, gap_ctx):
        bad_emp = admin_client.post(f"{API}/employee-mappings", json={
            "employee_id": "nope", "framework_id": gap_ctx["fw_id"], "mappings": []},
            timeout=STD_TIMEOUT)
        assert bad_emp.status_code == 404
        bad_fw = admin_client.post(f"{API}/employee-mappings", json={
            "employee_id": gap_ctx["emp_id"], "framework_id": "nope", "mappings": []},
            timeout=STD_TIMEOUT)
        assert bad_fw.status_code == 404

    def test_analyze(self, admin_client, gap_ctx):
        r = admin_client.post(f"{API}/skill-gaps/analyze", json={
            "employee_id": gap_ctx["emp_id"], "framework_id": gap_ctx["fw_id"]},
            timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert 0 <= data["readiness_pct"] <= 100
        items = data["items"]
        assert len(items) == 3
        for it in items:
            for k in ("competency", "required_level", "current_level", "gap", "weight", "priority"):
                assert k in it, f"missing {k}"
            assert it["priority"] in ("Critical", "High", "Medium", "Low", "Not Assessed")

        by_name = {i["competency"]: i for i in items}
        # Technical Depth: req 5, curr 2 -> gap 3
        assert by_name["Technical Depth"]["gap"] == 3
        # People Management: req 4, curr 3 -> gap 1
        assert by_name["People Management"]["gap"] == 1
        # Ownership: unmapped -> Not Assessed, gap = required_level
        assert by_name["Ownership"]["priority"] == "Not Assessed"
        assert by_name["Ownership"]["current_level"] == 0
        assert by_name["Ownership"]["gap"] == 3

        # expected readiness: gap_score = (3/5*40)+(1/5*35)+(3/5*25) = 24+7+15 = 46
        assert data["readiness_pct"] == pytest.approx(54.0, abs=0.2)

        order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Not Assessed": 4}
        ranks = [order[i["priority"]] for i in items]
        assert ranks == sorted(ranks), f"items not sorted by priority: {ranks}"

    def test_analyze_no_mapping_all_not_assessed(self, admin_client, gap_ctx):
        emp = admin_client.post(f"{API}/employees", json={"name": "TEST_Emp_NoMap"},
                                timeout=STD_TIMEOUT).json()
        try:
            r = admin_client.post(f"{API}/skill-gaps/analyze", json={
                "employee_id": emp["id"], "framework_id": gap_ctx["fw_id"]}, timeout=STD_TIMEOUT)
            assert r.status_code == 200
            data = r.json()
            assert all(i["priority"] == "Not Assessed" for i in data["items"])
            assert data["readiness_pct"] == pytest.approx(17.0, abs=0.2)  # gaps 5/4/3 w 40/35/25
        finally:
            admin_client.delete(f"{API}/employees/{emp['id']}", timeout=STD_TIMEOUT)

    def test_analyze_404s(self, admin_client, gap_ctx):
        r1 = admin_client.post(f"{API}/skill-gaps/analyze", json={
            "employee_id": "missing", "framework_id": gap_ctx["fw_id"]}, timeout=STD_TIMEOUT)
        assert r1.status_code == 404
        r2 = admin_client.post(f"{API}/skill-gaps/analyze", json={
            "employee_id": gap_ctx["emp_id"], "framework_id": "missing"}, timeout=STD_TIMEOUT)
        assert r2.status_code == 404

    def test_delete_employee_cascades_mapping(self, admin_client, gap_ctx):
        emp = admin_client.post(f"{API}/employees", json={"name": "TEST_Emp_Cascade"},
                                timeout=STD_TIMEOUT).json()
        m = admin_client.post(f"{API}/employee-mappings", json={
            "employee_id": emp["id"], "framework_id": gap_ctx["fw_id"],
            "mappings": [{"competency": "Ownership", "current_level": 2}]}, timeout=STD_TIMEOUT)
        assert m.status_code == 200
        admin_client.delete(f"{API}/employees/{emp['id']}", timeout=STD_TIMEOUT)
        left = admin_client.get(f"{API}/employee-mappings",
                                params={"employee_id": emp["id"]}, timeout=STD_TIMEOUT).json()
        assert left == [], "mappings should be removed with employee"


class TestDevPlans:
    def test_create_plan(self, admin_client, gap_ctx):
        body = {
            "name": "TEST_Plan_1", "employee_id": gap_ctx["emp_id"],
            "framework_id": gap_ctx["fw_id"], "status": "In Progress",
            "items": [{"competency": "Technical Depth", "priority": "Critical",
                       "current_level": 2, "target_level": 5,
                       "activities": ["Course", "Mentoring"]}],
        }
        r = admin_client.post(f"{API}/development-plans", json=body, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        doc = r.json()
        assert doc["status"] == "In Progress"
        assert doc["items"][0]["competency"] == "Technical Depth"
        assert "_id" not in doc
        lst = admin_client.get(f"{API}/development-plans", timeout=STD_TIMEOUT).json()
        assert any(p["id"] == doc["id"] for p in lst)

    def test_invalid_status(self, admin_client, gap_ctx):
        r = admin_client.post(f"{API}/development-plans", json={
            "name": "TEST_Plan_Bad", "employee_id": gap_ctx["emp_id"],
            "status": "Wibble", "items": []}, timeout=STD_TIMEOUT)
        assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("st", ["Not Started", "In Progress", "Completed", "On Hold"])
    def test_valid_statuses(self, admin_client, gap_ctx, st):
        r = admin_client.post(f"{API}/development-plans", json={
            "name": f"TEST_Plan_{st}", "employee_id": gap_ctx["emp_id"],
            "status": st, "items": []}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:200]

    def test_plans_org_isolated(self, admin_client):
        b_client, _, _, _, _ = signup_org("OrgC")
        assert b_client.get(f"{API}/development-plans", timeout=STD_TIMEOUT).json() == []


# ---------------- module: candidate comparison ----------------
@pytest.fixture(scope="class")
def cand_ids(admin_client):
    r = admin_client.get(f"{API}/candidates", timeout=STD_TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("candidates") or []
    if len(items) < 2:
        seed = admin_client.post(f"{API}/demo/seed", timeout=AI_TIMEOUT)
        assert seed.status_code == 200, f"demo seed failed {seed.status_code}: {seed.text[:300]}"
        items = admin_client.get(f"{API}/candidates", timeout=STD_TIMEOUT).json()
        if isinstance(items, dict):
            items = items.get("items") or items.get("candidates") or []
    assert len(items) >= 2, "need at least 2 candidates"
    return [c["id"] for c in items]


class TestCandidateCompare:
    def test_compare_two(self, admin_client, cand_ids):
        ids = cand_ids[:2]
        r = admin_client.post(f"{API}/candidates/compare", json={"candidate_ids": ids},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert {c["id"] for c in data["candidates"]} == set(ids)
        for c in data["candidates"]:
            for k in ("name", "score", "skill_pct", "experience_pct",
                      "qualification_pct", "summary"):
                assert k in c, f"missing {k}"
        for row in data["skill_matrix"]:
            assert set(row["candidates"].keys()) == set(ids)
            assert all(v in ("match", "gap", "unknown") for v in row["candidates"].values())

    def test_compare_max_six(self, admin_client, cand_ids):
        ids = cand_ids[:6]
        if len(ids) < 6:
            pytest.skip("fewer than 6 candidates seeded")
        r = admin_client.post(f"{API}/candidates/compare", json={"candidate_ids": ids},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert len(r.json()["candidates"]) == 6

    def test_compare_one_is_400(self, admin_client, cand_ids):
        r = admin_client.post(f"{API}/candidates/compare",
                              json={"candidate_ids": cand_ids[:1]}, timeout=STD_TIMEOUT)
        assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"

    def test_compare_seven_is_400(self, admin_client, cand_ids):
        ids = (cand_ids * 4)[:7]
        r = admin_client.post(f"{API}/candidates/compare", json={"candidate_ids": ids},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"

    def test_compare_missing_id_404(self, admin_client, cand_ids):
        r = admin_client.post(f"{API}/candidates/compare",
                              json={"candidate_ids": [cand_ids[0], "does-not-exist"]},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 404, f"{r.status_code} {r.text[:200]}"

    def test_compare_cross_org_404(self, admin_client, cand_ids):
        b_client, _, _, _, _ = signup_org("OrgD")
        r = b_client.post(f"{API}/candidates/compare",
                          json={"candidate_ids": cand_ids[:2]}, timeout=STD_TIMEOUT)
        assert r.status_code == 404, f"{r.status_code} {r.text[:200]}"

    def test_compare_unanalyzed_candidates(self, admin_client):
        """Candidates without analysis must still compare (empty matrix / null scores)."""
        jobs = admin_client.get(f"{API}/jobs", timeout=STD_TIMEOUT).json()
        if isinstance(jobs, dict):
            jobs = jobs.get("items") or []
        if not jobs:
            pytest.skip("no jobs available")
        job_id = jobs[0]["id"]
        made = []
        for i in range(2):
            c = admin_client.post(f"{API}/candidates", json={
                "name": f"TEST_NoAnalysis_{i}_{uuid.uuid4().hex[:5]}",
                "email": f"test_noan_{i}_{uuid.uuid4().hex[:5]}@example.com",
                "job_id": job_id, "resume_text": "Some resume text"}, timeout=STD_TIMEOUT)
            if c.status_code not in (200, 201):
                pytest.skip(f"cannot create candidate directly: {c.status_code} {c.text[:200]}")
            made.append(c.json()["id"])
        try:
            r = admin_client.post(f"{API}/candidates/compare",
                                  json={"candidate_ids": made}, timeout=STD_TIMEOUT)
            assert r.status_code == 200, r.text[:300]
            data = r.json()
            assert len(data["candidates"]) == 2
            assert all(c["score"] is None for c in data["candidates"])
            assert data["skill_matrix"] == []
        finally:
            for cid in made:
                admin_client.delete(f"{API}/candidates/{cid}", timeout=STD_TIMEOUT)

    def test_requires_auth(self, cand_ids):
        r = requests.post(f"{API}/candidates/compare", json={"candidate_ids": cand_ids[:2]},
                          timeout=STD_TIMEOUT)
        assert r.status_code in (401, 403)
