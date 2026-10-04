"""AI-backed endpoints: JD generate/save/version/refine, resume upload+analyze, interview kit+scorecard."""
import io
import time

import pytest
import requests

from conftest import API, STD_TIMEOUT, AI_TIMEOUT, client_with, signup_org, unique_email

JD_INPUT = {
    "company_name": "TEST Acme Corp", "industry": "Technology", "company_size": "Startup",
    "department": "Engineering", "title": "Senior Backend Engineer", "seniority": "Senior",
    "experience": "5-8 years", "employment_type": "Full-time", "work_arrangement": "Remote",
    "location": "Remote", "salary_currency": "USD", "salary_min": 120000, "salary_max": 160000,
    "priority": "High", "timeline": "Immediate",
    "required_skills": ["Python", "FastAPI", "MongoDB"], "preferred_skills": ["AWS", "Kubernetes"],
    "benefits": ["Health Insurance", "Remote Stipend"], "style": "Professional", "length": "Standard",
}

RESUME_TEXT = """Jane Q. Tester
Email: jane.tester@example.com | Phone: +1-555-0100
Senior Backend Engineer with 7 years of experience building distributed systems.

EXPERIENCE
Acme Cloud (2020-2025) - Senior Backend Engineer
- Built Python/FastAPI microservices handling 20k RPS
- Designed MongoDB schemas and Redis caching layers
- Led migration to AWS EKS (Kubernetes)
Beta Systems (2018-2020) - Backend Engineer
- Django REST APIs, PostgreSQL, CI/CD with GitHub Actions

EDUCATION
B.S. Computer Science, State University, 2018

SKILLS
Python, FastAPI, Django, MongoDB, PostgreSQL, Redis, AWS, Kubernetes, Docker, pytest

CERTIFICATIONS
AWS Certified Solutions Architect

LANGUAGES
English, Spanish
"""


def post_with_retry(client, url, json_body, timeout=AI_TIMEOUT, retries=1):
    """AI endpoints can return transient 502 - retry once."""
    r = client.post(url, json=json_body, timeout=timeout)
    attempt = 0
    while r.status_code == 502 and attempt < retries:
        time.sleep(3)
        r = client.post(url, json=json_body, timeout=timeout)
        attempt += 1
    return r


class TestJDGenerator:
    @pytest.fixture(scope="class")
    def ctx(self, admin_token):
        c = client_with(admin_token)
        job = c.post(f"{API}/jobs", json={"title": "TEST_JD Job", "department": "Engineering",
                                          "required_skills": ["Python"], "status": "open"},
                     timeout=STD_TIMEOUT).json()
        return {"c": c, "job_id": job["id"]}

    def test_generate_salary_validation(self, ctx):
        r = ctx["c"].post(f"{API}/jd/generate",
                          json={**JD_INPUT, "salary_min": 200000, "salary_max": 100000},
                          timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_generate_jd(self, ctx):
        r = post_with_retry(ctx["c"], f"{API}/jd/generate", JD_INPUT)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
        d = r.json()
        assert "content" in d and "input" in d
        content = d["content"]
        for k in ["job_title", "company_overview", "job_summary", "responsibilities",
                  "required_skills", "preferred_skills", "qualifications"]:
            assert k in content, f"missing key {k}: {list(content.keys())}"
        assert isinstance(content["responsibilities"], list) and len(content["responsibilities"]) > 0
        # BUG: ai_service prompt says "Include only these sections: <include_sections>
        # (fill others with empty string/list)". Default include_sections omits
        # Job Summary / Required Skills / Preferred Skills / Qualifications / Experience,
        # so Claude blanks those schema fields even though the caller supplied skills.
        empty = [k for k in ["job_summary", "required_skills", "preferred_skills",
                             "qualifications", "experience"]
                 if not content.get(k)]
        assert not empty, (
            f"JD generated with empty core fields {empty} using backend default "
            f"include_sections; supplied required_skills={JD_INPUT['required_skills']}")
        pytest.jd_content = content

    def test_save_and_version_bump(self, ctx):
        content = getattr(pytest, "jd_content", None) or {
            "job_title": "Senior Backend Engineer", "job_summary": "Build APIs.",
            "responsibilities": ["Ship code"], "required_skills": ["Python"]}
        body = {"job_id": ctx["job_id"], "title": "TEST_Senior Backend Engineer JD",
                "input": JD_INPUT, "content": content, "status": "draft"}
        r1 = ctx["c"].post(f"{API}/jd", json=body, timeout=STD_TIMEOUT)
        assert r1.status_code == 200, r1.text[:300]
        d1 = r1.json()
        assert d1["current_version"] == 1
        jd_id = d1["id"]

        r2 = ctx["c"].post(f"{API}/jd", json={**body, "status": "review"}, timeout=STD_TIMEOUT)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["id"] == jd_id, "second save created a new JD instead of a version"
        assert d2["current_version"] == 2, f"expected v2, got {d2['current_version']}"
        assert d2["status"] == "review"

        g = ctx["c"].get(f"{API}/jd/{jd_id}", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        gd = g.json()
        assert "versions" in gd and len(gd["versions"]) == 2
        assert [v["version"] for v in gd["versions"]] == [2, 1]

        lst = ctx["c"].get(f"{API}/jd", timeout=STD_TIMEOUT)
        assert lst.status_code == 200
        assert jd_id in [x["id"] for x in lst.json()]

    def test_save_jd_without_job_id(self, ctx):
        r = ctx["c"].post(f"{API}/jd", json={
            "job_id": None, "title": "TEST_Standalone JD", "input": {},
            "content": {"job_title": "X", "job_summary": "Y"}}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["current_version"] == 1

    def test_get_jd_404(self, ctx):
        assert ctx["c"].get(f"{API}/jd/no-such-jd", timeout=STD_TIMEOUT).status_code == 404

    def test_refine_section(self, ctx):
        r = post_with_retry(ctx["c"], f"{API}/jd/refine", {
            "section": "job_summary",
            "current_text": "We need a backend dev to do backend stuff with python.",
            "action": "improve"})
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        d = r.json()
        assert d["section"] == "job_summary"
        assert isinstance(d["text"], str) and len(d["text"].strip()) > 20


class TestResumeAnalyzer:
    @pytest.fixture(scope="class")
    def ctx(self, admin_token):
        c = client_with(admin_token)
        job = c.post(f"{API}/jobs", json={
            "title": "TEST_Resume Match Job", "department": "Engineering", "seniority": "Senior",
            "experience": "5-8 years", "required_skills": ["Python", "FastAPI", "MongoDB", "Go"],
            "preferred_skills": ["AWS"], "qualifications": ["BS in CS"], "status": "open"},
            timeout=STD_TIMEOUT).json()
        return {"c": c, "job_id": job["id"]}

    def _upload(self, ctx, filename, data, content_type="text/plain"):
        token = ctx["c"].headers["Authorization"]
        return requests.post(
            f"{API}/resumes/upload",
            headers={"Authorization": token},
            files={"file": (filename, io.BytesIO(data), content_type)},
            data={"job_id": ctx["job_id"], "candidate_name": "Jane Q. Tester"},
            timeout=180,
        )

    def test_upload_txt_resume(self, ctx):
        r = self._upload(ctx, "TEST_jane_resume.txt", RESUME_TEXT.encode())
        assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
        d = r.json()
        assert isinstance(d["id"], str)
        assert d["storage_path"], "storage_path missing"
        assert "Jane Q. Tester" in d["extracted_text"]
        assert d["original_filename"] == "TEST_jane_resume.txt"
        assert "_id" not in d
        pytest.resume_id = d["id"]

    def test_reject_unsupported_extension(self, ctx):
        r = self._upload(ctx, "malware.exe", b"MZ\x00binary", "application/octet-stream")
        assert r.status_code == 400
        assert "unsupported" in r.json()["detail"].lower()

    def test_reject_empty_file(self, ctx):
        r = self._upload(ctx, "empty.txt", b"")
        assert r.status_code == 400

    def test_reject_oversize_file(self, ctx):
        big = b"a" * (10 * 1024 * 1024 + 1024)
        r = self._upload(ctx, "TEST_big.txt", big)
        assert r.status_code == 400, f"expected 400 for >10MB, got {r.status_code}"
        assert "10mb" in r.json()["detail"].lower() or "exceed" in r.json()["detail"].lower()

    def test_analyze_missing_resume_404(self, ctx):
        r = ctx["c"].post(f"{API}/resumes/analyze", json={
            "resume_id": "no-such-resume", "job_id": ctx["job_id"],
            "weights": {"skills": 40, "experience": 30, "qualifications": 20, "other": 10}},
            timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_analyze_bad_weights_400(self, ctx):
        rid = getattr(pytest, "resume_id", None)
        if not rid:
            pytest.skip("upload failed, no resume id")
        r = ctx["c"].post(f"{API}/resumes/analyze", json={
            "resume_id": rid, "job_id": ctx["job_id"],
            "weights": {"skills": 50, "experience": 30, "qualifications": 20, "other": 10}},
            timeout=STD_TIMEOUT)
        assert r.status_code == 400
        assert "100" in r.json()["detail"]

    def test_analyze_bad_job_404(self, ctx):
        rid = getattr(pytest, "resume_id", None)
        if not rid:
            pytest.skip("upload failed, no resume id")
        r = ctx["c"].post(f"{API}/resumes/analyze", json={
            "resume_id": rid, "job_id": "no-such-job"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_analyze_success(self, ctx):
        rid = getattr(pytest, "resume_id", None)
        if not rid:
            pytest.skip("upload failed, no resume id")
        r = post_with_retry(ctx["c"], f"{API}/resumes/analyze", {
            "resume_id": rid, "job_id": ctx["job_id"],
            "weights": {"skills": 40, "experience": 30, "qualifications": 20, "other": 10}})
        assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
        cand = r.json()
        assert cand["stage"] == "screening"
        assert cand["job_id"] == ctx["job_id"]
        assert cand["is_demo"] is False
        a = cand["analysis"]
        assert 0 <= float(a["overall_score"]) <= 100, a["overall_score"]
        assert isinstance(a["matched_skills"], list) and len(a["matched_skills"]) > 0
        assert "missing_skills" in a and isinstance(a["missing_skills"], list)
        assert isinstance(a["summary"], str) and len(a["summary"]) > 20
        # persistence check
        g = ctx["c"].get(f"{API}/candidates/{cand['id']}", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        assert g.json()["analysis"]["overall_score"] == a["overall_score"]


class TestInterviews:
    @pytest.fixture(scope="class")
    def ctx(self, admin_token):
        c = client_with(admin_token)
        job = c.post(f"{API}/jobs", json={
            "title": "TEST_Interview Job", "department": "Engineering", "seniority": "Senior",
            "required_skills": ["Python", "System Design"], "status": "open"},
            timeout=STD_TIMEOUT).json()
        return {"c": c, "job_id": job["id"]}

    def test_generate_kit(self, ctx):
        r = post_with_retry(ctx["c"], f"{API}/interviews/generate", {
            "job_id": ctx["job_id"], "interview_type": "Behavioral",
            "duration_minutes": 60, "seniority": "Senior"})
        assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
        d = r.json()
        kit = d["kit"]
        assert "plan" in kit and isinstance(kit["plan"], dict)
        qs = kit["questions"]
        assert isinstance(qs, list), type(qs)
        assert 8 <= len(qs) <= 12, f"expected 8-12 questions, got {len(qs)}"
        for q in qs:
            assert q.get("question"), q
            assert q.get("category"), q
        assert isinstance(kit["competencies"], list) and len(kit["competencies"]) > 0
        # persisted
        g = ctx["c"].get(f"{API}/interviews/{d['id']}", timeout=STD_TIMEOUT)
        assert g.status_code == 200
        assert len(g.json()["kit"]["questions"]) == len(qs)
        lst = ctx["c"].get(f"{API}/interviews", timeout=STD_TIMEOUT)
        assert d["id"] in [i["id"] for i in lst.json()]
        pytest.interview_id = d["id"]

    def test_scorecard_invalid_rating(self, ctx):
        iid = getattr(pytest, "interview_id", None)
        if not iid:
            pytest.skip("no interview generated")
        r = ctx["c"].post(f"{API}/interviews/{iid}/scorecard", json={
            "competency_scores": [{"competency": "Ownership", "rating": 6, "evidence": "x"}],
            "recommendation": "Hire"}, timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_scorecard_invalid_recommendation(self, ctx):
        iid = getattr(pytest, "interview_id", None)
        if not iid:
            pytest.skip("no interview generated")
        r = ctx["c"].post(f"{API}/interviews/{iid}/scorecard", json={
            "competency_scores": [{"competency": "Ownership", "rating": 4, "evidence": "x"}],
            "recommendation": "Maybe"}, timeout=STD_TIMEOUT)
        assert r.status_code == 400

    def test_scorecard_success(self, ctx):
        iid = getattr(pytest, "interview_id", None)
        if not iid:
            pytest.skip("no interview generated")
        r = ctx["c"].post(f"{API}/interviews/{iid}/scorecard", json={
            "competency_scores": [
                {"competency": "Ownership", "rating": 4, "evidence": "Led migration"},
                {"competency": "Communication", "rating": 5, "evidence": "Clear answers"}],
            "strengths": "Strong ownership", "concerns": "Limited scale exposure",
            "recommendation": "Strong Hire"}, timeout=STD_TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["recommendation"] == "Strong Hire"
        assert len(d["scorecard"]["competency_scores"]) == 2
        g = ctx["c"].get(f"{API}/interviews/{iid}", timeout=STD_TIMEOUT)
        assert g.json()["recommendation"] == "Strong Hire"

    def test_scorecard_404(self, ctx):
        r = ctx["c"].post(f"{API}/interviews/nope/scorecard", json={
            "competency_scores": [{"competency": "X", "rating": 3}],
            "recommendation": "Hire"}, timeout=STD_TIMEOUT)
        assert r.status_code == 404

    def test_get_interview_404(self, ctx):
        assert ctx["c"].get(f"{API}/interviews/nope", timeout=STD_TIMEOUT).status_code == 404
