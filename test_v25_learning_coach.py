"""V2.5 coverage: framework clone, employee bulk import, career path,
learning recommendations (AI), progress tracking, level-up, AI career coach."""
import uuid

import pytest

from conftest import API, AI_TIMEOUT, STD_TIMEOUT, signup_org


def _fw_payload(name):
    return {
        "name": name,
        "department": "Engineering",
        "role": "Senior Backend Engineer",
        "competencies": [
            {"name": "Technical Depth", "category": "Technical", "required_level": 5, "weight": 40},
            {"name": "System Design", "category": "Technical", "required_level": 4, "weight": 35},
            {"name": "Ownership", "category": "Execution", "required_level": 3, "weight": 25},
        ],
    }


@pytest.fixture(scope="module")
def ctx(admin_client):
    """Employee + framework + mapping with real gaps. Cleans up after module."""
    created = {"employees": [], "frameworks": [], "plans": []}
    fw = admin_client.post(f"{API}/frameworks", json=_fw_payload(f"TEST_V25_FW_{uuid.uuid4().hex[:6]}"),
                           timeout=STD_TIMEOUT)
    assert fw.status_code == 200, fw.text[:400]
    fw = fw.json()
    created["frameworks"].append(fw["id"])

    emp = admin_client.post(f"{API}/employees", json={
        "name": "TEST_V25 Employee", "email": f"test_v25_{uuid.uuid4().hex[:6]}@example.com",
        "role": "Backend Engineer", "department": "Engineering",
    }, timeout=STD_TIMEOUT)
    assert emp.status_code == 200, emp.text[:400]
    emp = emp.json()
    created["employees"].append(emp["id"])

    mp = admin_client.post(f"{API}/employee-mappings", json={
        "employee_id": emp["id"], "framework_id": fw["id"],
        "mappings": [
            {"competency": "Technical Depth", "current_level": 3, "target_level": 5},
            {"competency": "System Design", "current_level": 2, "target_level": 4},
            {"competency": "Ownership", "current_level": 3, "target_level": 3},
        ],
    }, timeout=STD_TIMEOUT)
    assert mp.status_code == 200, mp.text[:400]

    yield {"framework": fw, "employee": emp, "created": created, "client": admin_client}

    for eid in created["employees"]:
        admin_client.delete(f"{API}/employees/{eid}", timeout=STD_TIMEOUT)
    for fid in created["frameworks"]:
        admin_client.delete(f"{API}/frameworks/{fid}", timeout=STD_TIMEOUT)


# ---------- Framework clone ----------
class TestFrameworkClone:
    def test_clone_bumps_levels_and_caps_at_5(self, admin_client, ctx):
        src = ctx["framework"]
        r = admin_client.post(f"{API}/frameworks/{src['id']}/clone",
                              json={"name": "TEST_V25_FW_CLONE", "bump_required_level": 1,
                                    "role": "Staff Backend Engineer"},
                              timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        c = r.json()
        ctx["created"]["frameworks"].append(c["id"])
        assert c["id"] != src["id"]
        assert c["name"] == "TEST_V25_FW_CLONE"
        assert c["role"] == "Staff Backend Engineer"
        assert c["cloned_from"] == src["id"]
        by_name = {x["name"]: x for x in c["competencies"]}
        assert by_name["Technical Depth"]["required_level"] == 5  # capped
        assert by_name["System Design"]["required_level"] == 5
        assert by_name["Ownership"]["required_level"] == 4
        # weights preserved
        assert sum(x["weight"] for x in c["competencies"]) == 100
        # persisted
        g = admin_client.get(f"{API}/frameworks/{c['id']}", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        assert g.json()["cloned_from"] == src["id"]

    def test_clone_default_name_and_no_bump(self, admin_client, ctx):
        src = ctx["framework"]
        r = admin_client.post(f"{API}/frameworks/{src['id']}/clone", json={}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        c = r.json()
        ctx["created"]["frameworks"].append(c["id"])
        assert c["name"] == f"{src['name']} (copy)"
        assert {x["name"]: x["required_level"] for x in c["competencies"]} == \
               {x["name"]: x["required_level"] for x in src["competencies"]}

    def test_clone_bad_id_404(self, admin_client):
        r = admin_client.post(f"{API}/frameworks/{uuid.uuid4().hex}/clone", json={}, timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_clone_bump_out_of_range_422(self, admin_client, ctx):
        r = admin_client.post(f"{API}/frameworks/{ctx['framework']['id']}/clone",
                              json={"bump_required_level": 5}, timeout=STD_TIMEOUT)
        assert r.status_code == 422

    def test_clone_cross_org_404(self, ctx):
        other, _, _, _, _ = signup_org("CloneIso")
        r = other.post(f"{API}/frameworks/{ctx['framework']['id']}/clone", json={}, timeout=STD_TIMEOUT)
        assert r.status_code == 404


# ---------- Bulk employee import ----------
class TestEmployeeBulk:
    def test_bulk_import_counts_and_persists(self, admin_client):
        rows = [{"name": f"TEST_BULK_{i}", "email": f"test_bulk_{uuid.uuid4().hex[:6]}@example.com",
                 "role": "Engineer", "department": "Engineering"} for i in range(5)]
        r = admin_client.post(f"{API}/employees/bulk", json={"employees": rows}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        assert data["created"] == len(rows)
        assert len(data["employees"]) == len(rows)
        ids = [e["id"] for e in data["employees"]]
        assert len(set(ids)) == len(rows), "duplicate ids generated"
        assert all("_id" not in e for e in data["employees"])
        listed = {e["id"] for e in admin_client.get(f"{API}/employees", timeout=STD_TIMEOUT).json()}
        assert set(ids).issubset(listed)
        for eid in ids:
            assert admin_client.delete(f"{API}/employees/{eid}", timeout=STD_TIMEOUT).status_code == 200

    def test_bulk_empty_400(self, admin_client):
        r = admin_client.post(f"{API}/employees/bulk", json={"employees": []}, timeout=STD_TIMEOUT)
        assert r.status_code == 400
        assert "No employees" in r.json()["detail"]

    def test_bulk_over_limit_400(self, admin_client):
        rows = [{"name": f"TEST_BULK_OVER_{i}"} for i in range(501)]
        r = admin_client.post(f"{API}/employees/bulk", json={"employees": rows}, timeout=STD_TIMEOUT)
        assert r.status_code == 400, r.text[:300]
        assert "500" in r.json()["detail"]

    def test_bulk_requires_auth(self):
        import requests
        r = requests.post(f"{API}/employees/bulk", json={"employees": [{"name": "x"}]}, timeout=STD_TIMEOUT)
        assert r.status_code in (401, 403)


# ---------- Career path ----------
class TestCareerPath:
    def test_career_path_ordering_and_top_gaps(self, admin_client, ctx):
        eid = ctx["employee"]["id"]
        r = admin_client.get(f"{API}/employees/{eid}/career-path", timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        assert data["employee"]["id"] == eid
        paths = data["paths"]
        assert len(paths) >= 1
        for p in paths:
            assert len(p["top_gaps"]) <= 3
            assert 0 <= p["readiness_pct"] <= 100
            assert p["total_competencies"] >= 1
            assert set(["framework_id", "framework_name", "role_match", "department_match",
                        "has_mapping", "assessed_count"]).issubset(p.keys())
        # ordering: role_match first, then department_match, then readiness desc
        keys = [(not p["role_match"], not p["department_match"], -p["readiness_pct"]) for p in paths]
        assert keys == sorted(keys), f"paths not ordered: {keys}"
        # our mapped framework must show a computed readiness (not 0/unmapped)
        mine = [p for p in paths if p["framework_id"] == ctx["framework"]["id"]][0]
        assert mine["has_mapping"] is True
        assert mine["assessed_count"] == 3
        # 100 - ((2/5*40)+(2/5*35)+(0)*25)/100*100 = 70.0
        assert mine["readiness_pct"] == pytest.approx(70.0, abs=0.2)
        assert [g["competency"] for g in mine["top_gaps"]] == ["Technical Depth", "System Design"]

    def test_career_path_404(self, admin_client):
        r = admin_client.get(f"{API}/employees/{uuid.uuid4().hex}/career-path", timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_career_path_cross_org_404(self, ctx):
        other, _, _, _, _ = signup_org("PathIso")
        r = other.get(f"{API}/employees/{ctx['employee']['id']}/career-path", timeout=STD_TIMEOUT)
        assert r.status_code == 404


# ---------- Skill gap 'Met' priority + dev plan validation (regression) ----------
class TestGapAndPlanRegression:
    def test_met_priority_for_zero_gap(self, admin_client, ctx):
        r = admin_client.post(f"{API}/skill-gaps/analyze", json={
            "employee_id": ctx["employee"]["id"], "framework_id": ctx["framework"]["id"]}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        data = r.json()
        by = {i["competency"]: i for i in data["items"]}
        assert by["Ownership"]["gap"] == 0
        assert by["Ownership"]["priority"] == "Met"
        order = ["Critical", "High", "Medium", "Low", "Met", "Not Assessed"]
        idx = [order.index(i["priority"]) for i in data["items"]]
        assert idx == sorted(idx)
        assert data["readiness_pct"] == pytest.approx(70.0, abs=0.2)

    def test_dev_plan_bogus_employee_404(self, admin_client, ctx):
        r = admin_client.post(f"{API}/development-plans", json={
            "name": "TEST_V25 Plan", "employee_id": uuid.uuid4().hex, "items": []}, timeout=STD_TIMEOUT)
        assert r.status_code == 404
        assert "Employee" in r.json()["detail"]

    def test_dev_plan_bogus_framework_404(self, admin_client, ctx):
        r = admin_client.post(f"{API}/development-plans", json={
            "name": "TEST_V25 Plan", "employee_id": ctx["employee"]["id"],
            "framework_id": uuid.uuid4().hex, "items": []}, timeout=STD_TIMEOUT)
        assert r.status_code == 404
        assert "Framework" in r.json()["detail"]


# ---------- Learning recommendations (AI) + progress tracking ----------
@pytest.fixture(scope="module")
def learning(ctx):
    client = ctx["client"]
    last = None
    for _ in range(2):
        r = client.post(f"{API}/learning/generate", json={
            "employee_id": ctx["employee"]["id"], "framework_id": ctx["framework"]["id"]},
            timeout=AI_TIMEOUT)
        last = r
        if r.status_code == 200:
            return r.json()
    pytest.fail(f"/learning/generate failed {last.status_code}: {last.text[:500]}")


class TestLearningGenerate:
    def test_structure(self, learning):
        assert "_id" not in learning
        c = learning["content"]
        assert isinstance(c.get("overview"), str) and len(c["overview"].strip()) > 20
        recs = c.get("recommendations")
        assert isinstance(recs, list) and len(recs) >= 1
        for r in recs:
            assert r.get("competency")
            assert r.get("priority") in ("Critical", "High", "Medium", "Low")
            assert isinstance(r.get("activities"), list) and len(r["activities"]) >= 1
            for a in r["activities"]:
                assert a.get("title")
                assert a.get("status") == "todo", f"default status not todo: {a}"
                assert a.get("completed_at") is None

    def test_only_gap_competencies_recommended(self, learning):
        names = {r["competency"] for r in learning["content"]["recommendations"]}
        assert "Ownership" not in names, "met competency should not be recommended"

    def test_no_invented_urls(self, learning):
        import json as _json
        blob = _json.dumps(learning["content"])
        assert "http://" not in blob and "https://" not in blob, "AI invented URLs"

    def test_listed_and_org_scoped(self, ctx, learning):
        lst = ctx["client"].get(f"{API}/learning", timeout=STD_TIMEOUT).json()
        assert learning["id"] in [x["id"] for x in lst]
        other, _, _, _, _ = signup_org("LearnIso")
        assert other.get(f"{API}/learning", timeout=STD_TIMEOUT).json() == []
        r = other.patch(f"{API}/learning/{learning['id']}/activity",
                        json={"rec_index": 0, "act_index": 0, "status": "done"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_no_assessed_gaps_400(self, admin_client):
        fwp = _fw_payload(f"TEST_V25_NOGAP_{uuid.uuid4().hex[:6]}")
        fw = admin_client.post(f"{API}/frameworks", json=fwp, timeout=STD_TIMEOUT).json()
        emp = admin_client.post(f"{API}/employees", json={"name": "TEST_V25 NoGap"}, timeout=STD_TIMEOUT).json()
        try:
            r = admin_client.post(f"{API}/learning/generate", json={
                "employee_id": emp["id"], "framework_id": fw["id"]}, timeout=AI_TIMEOUT)
            assert r.status_code == 400, r.text[:300]
            assert "No assessed gaps" in r.json()["detail"]
        finally:
            admin_client.delete(f"{API}/employees/{emp['id']}", timeout=STD_TIMEOUT)
            admin_client.delete(f"{API}/frameworks/{fw['id']}", timeout=STD_TIMEOUT)

    def test_generate_404s(self, admin_client, ctx):
        r = admin_client.post(f"{API}/learning/generate", json={
            "employee_id": uuid.uuid4().hex, "framework_id": ctx["framework"]["id"]}, timeout=STD_TIMEOUT)
        assert r.status_code == 404
        r = admin_client.post(f"{API}/learning/generate", json={
            "employee_id": ctx["employee"]["id"], "framework_id": uuid.uuid4().hex}, timeout=STD_TIMEOUT)
        assert r.status_code == 404


class TestProgressTracking:
    def test_mark_in_progress_then_done(self, ctx, learning):
        lid = learning["id"]
        c = ctx["client"]
        r = c.patch(f"{API}/learning/{lid}/activity",
                    json={"rec_index": 0, "act_index": 0, "status": "in_progress"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        a = r.json()["content"]["recommendations"][0]["activities"][0]
        assert a["status"] == "in_progress"
        assert a["completed_at"] is None

        r = c.patch(f"{API}/learning/{lid}/activity",
                    json={"rec_index": 0, "act_index": 0, "status": "done"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200
        a = r.json()["content"]["recommendations"][0]["activities"][0]
        assert a["status"] == "done"
        assert a["completed_at"], "completed_at not set on done"

        # persisted via GET list
        lst = c.get(f"{API}/learning", timeout=STD_TIMEOUT).json()
        doc = [x for x in lst if x["id"] == lid][0]
        assert doc["content"]["recommendations"][0]["activities"][0]["status"] == "done"

        # back to todo clears completed_at
        r = c.patch(f"{API}/learning/{lid}/activity",
                    json={"rec_index": 0, "act_index": 0, "status": "todo"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200
        a = r.json()["content"]["recommendations"][0]["activities"][0]
        assert a["status"] == "todo" and a["completed_at"] is None

    def test_invalid_status_400(self, ctx, learning):
        r = ctx["client"].patch(f"{API}/learning/{learning['id']}/activity",
                                json={"rec_index": 0, "act_index": 0, "status": "finished"},
                                timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_out_of_range_indices_400(self, ctx, learning):
        c = ctx["client"]
        r = c.patch(f"{API}/learning/{learning['id']}/activity",
                    json={"rec_index": 999, "act_index": 0, "status": "done"}, timeout=STD_TIMEOUT)
        assert r.status_code == 400 and "rec_index" in r.json()["detail"]
        r = c.patch(f"{API}/learning/{learning['id']}/activity",
                    json={"rec_index": 0, "act_index": 999, "status": "done"}, timeout=STD_TIMEOUT)
        assert r.status_code == 400 and "act_index" in r.json()["detail"]

    def test_negative_index_422(self, ctx, learning):
        r = ctx["client"].patch(f"{API}/learning/{learning['id']}/activity",
                                json={"rec_index": -1, "act_index": 0, "status": "done"}, timeout=STD_TIMEOUT)
        assert r.status_code == 422

    def test_activity_bad_plan_404(self, admin_client):
        r = admin_client.patch(f"{API}/learning/{uuid.uuid4().hex}/activity",
                               json={"rec_index": 0, "act_index": 0, "status": "done"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404


class TestLevelUp:
    def test_level_up_bumps_mapping_and_readiness(self, ctx, learning):
        c = ctx["client"]
        before = c.post(f"{API}/skill-gaps/analyze", json={
            "employee_id": ctx["employee"]["id"], "framework_id": ctx["framework"]["id"]},
            timeout=STD_TIMEOUT).json()["readiness_pct"]

        r = c.post(f"{API}/learning/{learning['id']}/level-up",
                   json={"competency": "System Design", "new_level": 4}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        mapping = r.json()
        assert "_id" not in mapping
        lvl = {m["competency"]: m["current_level"] for m in mapping["mappings"]}
        assert lvl["System Design"] == 4
        assert lvl["Technical Depth"] == 3, "other competencies must be untouched"

        after = c.post(f"{API}/skill-gaps/analyze", json={
            "employee_id": ctx["employee"]["id"], "framework_id": ctx["framework"]["id"]},
            timeout=STD_TIMEOUT).json()
        assert after["readiness_pct"] > before, f"{after['readiness_pct']} !> {before}"
        by = {i["competency"]: i for i in after["items"]}
        assert by["System Design"]["current_level"] == 4
        assert by["System Design"]["priority"] == "Met"

    def test_level_up_unknown_competency_404(self, ctx, learning):
        r = ctx["client"].post(f"{API}/learning/{learning['id']}/level-up",
                               json={"competency": "Underwater Basket Weaving", "new_level": 3},
                               timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_level_up_out_of_range_422(self, ctx, learning):
        r = ctx["client"].post(f"{API}/learning/{learning['id']}/level-up",
                               json={"competency": "System Design", "new_level": 9}, timeout=STD_TIMEOUT)
        assert r.status_code == 422

    def test_level_up_bad_plan_404(self, admin_client):
        r = admin_client.post(f"{API}/learning/{uuid.uuid4().hex}/level-up",
                              json={"competency": "X", "new_level": 3}, timeout=STD_TIMEOUT)
        assert r.status_code == 404


# ---------- AI Career Coach ----------
@pytest.fixture(scope="module")
def coach_ctx(admin_client):
    emp = admin_client.post(f"{API}/employees", json={
        "name": "TEST_COACH Employee", "role": "Product Analyst", "department": "Product",
    }, timeout=STD_TIMEOUT).json()
    yield emp
    admin_client.delete(f"{API}/employees/{emp['id']}", timeout=STD_TIMEOUT)


class TestCareerCoach:
    def test_chat_reply(self, admin_client, coach_ctx):
        last = None
        for _ in range(2):
            r = admin_client.post(f"{API}/coach/chat", json={
                "employee_id": coach_ctx["id"], "history": [],
                "message": "I want to move into product management within a year. Where do I start?",
            }, timeout=AI_TIMEOUT)
            last = r
            if r.status_code == 200:
                break
        assert last.status_code == 200, f"{last.status_code}: {last.text[:500]}"
        reply = last.json().get("reply")
        assert isinstance(reply, str) and len(reply.strip()) > 20, reply

    def test_chat_empty_message_400(self, admin_client, coach_ctx):
        r = admin_client.post(f"{API}/coach/chat", json={
            "employee_id": coach_ctx["id"], "history": [], "message": "   "}, timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_chat_bad_employee_404(self, admin_client):
        r = admin_client.post(f"{API}/coach/chat", json={
            "employee_id": uuid.uuid4().hex, "history": [], "message": "hi"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_plan_empty_history_400(self, admin_client, coach_ctx):
        r = admin_client.post(f"{API}/coach/plan", json={
            "employee_id": coach_ctx["id"], "history": []}, timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_plan_structure_and_listing(self, admin_client, coach_ctx):
        history = [
            {"role": "user", "content": "I want to move into product management within a year."},
            {"role": "assistant", "content": "What draws you to product management?"},
            {"role": "user", "content": "I enjoy customer discovery and shaping roadmaps. I have 3 years in analytics."},
            {"role": "assistant", "content": "What is your biggest constraint?"},
            {"role": "user", "content": "Time - I can spend about 5 hours a week."},
        ]
        last = None
        for _ in range(2):
            r = admin_client.post(f"{API}/coach/plan", json={
                "employee_id": coach_ctx["id"], "history": history}, timeout=AI_TIMEOUT)
            last = r
            if r.status_code == 200:
                break
        assert last.status_code == 200, f"{last.status_code}: {last.text[:500]}"
        doc = last.json()
        assert "_id" not in doc
        assert doc["employee_id"] == coach_ctx["id"]
        p = doc["content"]
        assert isinstance(p.get("north_star"), str) and p["north_star"].strip()
        assert isinstance(p.get("focus_areas"), list) and p["focus_areas"]
        for phase in ("weeks_1_4", "weeks_5_8", "weeks_9_12"):
            ph = p.get(phase)
            assert isinstance(ph, dict), f"missing phase {phase}"
            assert ph.get("theme")
            assert isinstance(ph.get("activities"), list) and ph["activities"]
            assert all(a.get("title") for a in ph["activities"])
            assert isinstance(ph.get("milestones"), list) and ph["milestones"]
        assert isinstance(p.get("success_metrics"), list) and p["success_metrics"]
        assert isinstance(p.get("check_ins"), list) and p["check_ins"]

        lst = admin_client.get(f"{API}/coach/plans", params={"employee_id": coach_ctx["id"]},
                              timeout=STD_TIMEOUT)
        assert lst.status_code == 200
        assert doc["id"] in [x["id"] for x in lst.json()]

        # org isolation
        other, _, _, _, _ = signup_org("CoachIso")
        assert other.get(f"{API}/coach/plans", timeout=STD_TIMEOUT).json() == []

    def test_plans_requires_auth(self):
        import requests
        r = requests.get(f"{API}/coach/plans", timeout=STD_TIMEOUT)
        assert r.status_code in (401, 403)
