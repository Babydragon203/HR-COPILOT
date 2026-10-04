"""HR routes: Jobs, JDs, Candidates, Resume Analyzer, Interview Assistant."""
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field

from deps import db, get_current_user, require_perm, audit, now_iso, new_id
from ai_service import generate_job_description, analyze_resume, generate_interview_kit, refine_jd_section
from storage_service import put_object, get_object, extract_text

APP_NAME = os.environ.get("APP_NAME", "hr-copilot")

router = APIRouter(tags=["hr"])


# ============ JOBS ============
class JobIn(BaseModel):
    title: str
    department: Optional[str] = None
    seniority: Optional[str] = None
    experience: Optional[str] = None
    employment_type: Optional[str] = None
    work_arrangement: Optional[str] = None
    location: Optional[str] = None
    salary_currency: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    priority: Optional[str] = None
    timeline: Optional[str] = None
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    qualifications: List[str] = []
    status: str = "draft"


@router.get("/jobs")
async def list_jobs(user: dict = Depends(require_perm("jobs.read"))):
    cur = db.jobs.find({"org_id": user["org_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(500)


@router.post("/jobs")
async def create_job(body: JobIn, user: dict = Depends(require_perm("jobs.create"))):
    if body.salary_min and body.salary_max and body.salary_min > body.salary_max:
        raise HTTPException(status_code=400, detail="salary_min cannot exceed salary_max")
    job = body.model_dump()
    job.update({
        "id": new_id(), "org_id": user["org_id"], "created_by": user["id"],
        "created_at": now_iso(), "updated_at": now_iso(),
        "is_demo": False,
    })
    await db.jobs.insert_one(job)
    await audit(user, "job.create", "job", job["id"], {"title": job["title"]})
    job.pop("_id", None)
    return job


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(require_perm("jobs.read"))):
    job = await db.jobs.find_one({"id": job_id, "org_id": user["org_id"]}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/jobs/{job_id}")
async def update_job(job_id: str, body: Dict[str, Any], user: dict = Depends(require_perm("jobs.update"))):
    body["updated_at"] = now_iso()
    body.pop("id", None); body.pop("org_id", None)
    res = await db.jobs.update_one({"id": job_id, "org_id": user["org_id"]}, {"$set": body})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    await audit(user, "job.update", "job", job_id, {"fields": list(body.keys())})
    return await db.jobs.find_one({"id": job_id}, {"_id": 0})


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, user: dict = Depends(require_perm("jobs.delete"))):
    res = await db.jobs.delete_one({"id": job_id, "org_id": user["org_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    await audit(user, "job.delete", "job", job_id)
    return {"ok": True}


# ============ JD GENERATOR ============
class JDGenerateIn(BaseModel):
    job_id: Optional[str] = None
    company_name: str
    industry: Optional[str] = None
    company_size: Optional[str] = None
    department: str
    title: str
    seniority: str
    experience: str
    employment_type: str
    work_arrangement: str
    location: Optional[str] = None
    salary_currency: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    priority: Optional[str] = None
    timeline: Optional[str] = None
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    certifications: List[str] = []
    languages: List[str] = []
    benefits: List[str] = []
    style: str = "Professional"
    length: str = "Standard"
    include_sections: List[str] = ["Company Overview", "Responsibilities", "KPIs", "Benefits", "Career Growth", "Diversity Statement"]


@router.post("/jd/generate")
async def jd_generate(body: JDGenerateIn, user: dict = Depends(require_perm("jds.create"))):
    if body.salary_min and body.salary_max and body.salary_min > body.salary_max:
        raise HTTPException(status_code=400, detail="salary_min cannot exceed salary_max")
    try:
        content = await generate_job_description(body.model_dump(), session_id=f"jd-{user['id']}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")
    return {"content": content, "input": body.model_dump()}


class JDSaveIn(BaseModel):
    job_id: Optional[str] = None
    title: str
    input: Dict[str, Any]
    content: Dict[str, Any]
    status: str = "draft"  # draft|review|approved|published|archived


@router.post("/jd")
async def save_jd(body: JDSaveIn, user: dict = Depends(require_perm("jds.create"))):
    existing = None
    if body.job_id:
        existing = await db.job_descriptions.find_one(
            {"job_id": body.job_id, "org_id": user["org_id"]}, {"_id": 0}
        )
    jd_id = existing["id"] if existing else new_id()
    version_num = (existing.get("current_version", 0) if existing else 0) + 1
    version_doc = {
        "id": new_id(), "jd_id": jd_id, "org_id": user["org_id"],
        "version": version_num, "content": body.content, "input": body.input,
        "created_by": user["id"], "created_at": now_iso(),
    }
    await db.jd_versions.insert_one(version_doc)

    doc = {
        "id": jd_id, "org_id": user["org_id"], "job_id": body.job_id,
        "title": body.title, "current_version": version_num,
        "content": body.content, "input": body.input, "status": body.status,
        "updated_at": now_iso(),
        "created_at": existing["created_at"] if existing else now_iso(),
        "created_by": user["id"], "is_demo": False,
    }
    await db.job_descriptions.update_one({"id": jd_id}, {"$set": doc}, upsert=True)
    await audit(user, "jd.save", "job_description", jd_id, {"version": version_num, "status": body.status})
    doc.pop("_id", None)
    return doc


@router.get("/jd")
async def list_jds(user: dict = Depends(require_perm("jds.read"))):
    cur = db.job_descriptions.find({"org_id": user["org_id"]}, {"_id": 0}).sort("updated_at", -1)
    return await cur.to_list(500)


@router.get("/jd/{jd_id}")
async def get_jd(jd_id: str, user: dict = Depends(require_perm("jds.read"))):
    jd = await db.job_descriptions.find_one({"id": jd_id, "org_id": user["org_id"]}, {"_id": 0})
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    versions = await db.jd_versions.find(
        {"jd_id": jd_id, "org_id": user["org_id"]}, {"_id": 0}
    ).sort("version", -1).to_list(50)
    jd["versions"] = versions
    return jd


class JDRefineIn(BaseModel):
    section: str
    current_text: str
    action: str  # improve|expand|shorten|professional|concise


@router.post("/jd/refine")
async def jd_refine(body: JDRefineIn, user: dict = Depends(require_perm("jds.update"))):
    try:
        new_text = await refine_jd_section(body.section, body.current_text, body.action, f"jd-refine-{user['id']}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI refine failed: {e}")
    return {"section": body.section, "text": new_text}


# ============ CANDIDATES + RESUME ANALYZER ============
CANDIDATE_STAGES = ["applied", "screening", "shortlisted", "interview", "assessment", "offer", "hired", "rejected", "withdrawn"]


class CandidateIn(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    job_id: Optional[str] = None
    stage: str = "applied"
    notes: Optional[str] = None


@router.get("/candidates")
async def list_candidates(job_id: Optional[str] = None, user: dict = Depends(require_perm("candidates.read"))):
    q = {"org_id": user["org_id"]}
    if job_id:
        q["job_id"] = job_id
    cur = db.candidates.find(q, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(500)


@router.post("/candidates")
async def create_candidate(body: CandidateIn, user: dict = Depends(require_perm("candidates.create"))):
    if body.stage not in CANDIDATE_STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage")
    c = body.model_dump()
    c.update({"id": new_id(), "org_id": user["org_id"], "created_at": now_iso(),
              "updated_at": now_iso(), "created_by": user["id"], "is_demo": False,
              "resume": None, "analysis": None})
    await db.candidates.insert_one(c)
    await audit(user, "candidate.create", "candidate", c["id"])
    c.pop("_id", None)
    return c


@router.get("/candidates/{cid}")
async def get_candidate(cid: str, user: dict = Depends(require_perm("candidates.read"))):
    c = await db.candidates.find_one({"id": cid, "org_id": user["org_id"]}, {"_id": 0})
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return c


@router.patch("/candidates/{cid}")
async def update_candidate(cid: str, body: Dict[str, Any], user: dict = Depends(require_perm("candidates.update"))):
    body["updated_at"] = now_iso()
    body.pop("id", None); body.pop("org_id", None)
    if "stage" in body and body["stage"] not in CANDIDATE_STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage")
    res = await db.candidates.update_one({"id": cid, "org_id": user["org_id"]}, {"$set": body})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Candidate not found")
    await audit(user, "candidate.update", "candidate", cid, {"fields": list(body.keys())})
    return await db.candidates.find_one({"id": cid}, {"_id": 0})


@router.delete("/candidates/{cid}")
async def delete_candidate(cid: str, user: dict = Depends(require_perm("candidates.delete"))):
    res = await db.candidates.delete_one({"id": cid, "org_id": user["org_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Candidate not found")
    await audit(user, "candidate.delete", "candidate", cid)
    return {"ok": True}


ALLOWED_RESUME_EXTS = {"pdf", "docx", "txt"}
MAX_RESUME_MB = 10


@router.post("/resumes/upload")
async def upload_resume(
    file: UploadFile = File(...),
    job_id: Optional[str] = Form(None),
    candidate_name: Optional[str] = Form(None),
    user: dict = Depends(require_perm("candidates.create")),
):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_RESUME_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}. Allowed: pdf, docx, txt")
    data = await file.read()
    if len(data) > MAX_RESUME_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_RESUME_MB}MB")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    path = f"{APP_NAME}/orgs/{user['org_id']}/resumes/{new_id()}.{ext}"
    try:
        result = put_object(path, data, file.content_type or "application/octet-stream")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Storage upload failed: {e}")

    text = extract_text(data, file.filename)

    doc = {
        "id": new_id(), "org_id": user["org_id"],
        "storage_path": result["path"], "original_filename": file.filename,
        "content_type": file.content_type, "size": result.get("size", len(data)),
        "extracted_text": text[:50000],
        "candidate_id": None, "job_id": job_id, "candidate_name_hint": candidate_name,
        "uploaded_by": user["id"], "created_at": now_iso(), "is_deleted": False,
    }
    await db.candidate_documents.insert_one(doc)
    await audit(user, "resume.upload", "resume", doc["id"], {"filename": file.filename})
    return {k: v for k, v in doc.items() if k != "_id"}


class AnalyzeIn(BaseModel):
    resume_id: str
    job_id: str
    weights: Dict[str, int] = {"skills": 40, "experience": 30, "qualifications": 20, "other": 10}


@router.post("/resumes/analyze")
async def analyze(body: AnalyzeIn, user: dict = Depends(require_perm("candidates.create"))):
    resume = await db.candidate_documents.find_one({"id": body.resume_id, "org_id": user["org_id"]}, {"_id": 0})
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    job = await db.jobs.find_one({"id": body.job_id, "org_id": user["org_id"]}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if sum(body.weights.values()) != 100:
        raise HTTPException(status_code=400, detail="Weights must sum to 100")

    text = resume.get("extracted_text") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from resume")

    try:
        data = await analyze_resume(text, job, body.weights, f"resume-{user['id']}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {e}")

    # Create/update candidate
    c = data.get("candidate", {})
    candidate_name = c.get("name") or resume.get("candidate_name_hint") or "Unnamed Candidate"
    candidate = {
        "id": new_id(), "org_id": user["org_id"],
        "name": candidate_name, "email": c.get("email"), "phone": c.get("phone"),
        "job_id": body.job_id, "stage": "screening",
        "resume": {"resume_id": resume["id"], "filename": resume["original_filename"]},
        "analysis": data.get("analysis", {}),
        "extracted": c,
        "created_at": now_iso(), "updated_at": now_iso(), "created_by": user["id"], "is_demo": False,
    }
    await db.candidates.insert_one(candidate)
    await db.candidate_documents.update_one({"id": resume["id"]}, {"$set": {"candidate_id": candidate["id"]}})
    await audit(user, "resume.analyze", "candidate", candidate["id"], {"job_id": body.job_id})
    candidate.pop("_id", None)
    return candidate


# ============ INTERVIEW ASSISTANT ============
class InterviewGenIn(BaseModel):
    job_id: Optional[str] = None
    candidate_id: Optional[str] = None
    interview_type: str  # HR|Technical|Behavioral|Managerial|Case Study|Situational|Culture & Values
    duration_minutes: int = 60
    seniority: Optional[str] = None
    required_skills: List[str] = []
    competencies: List[str] = []
    focus_notes: Optional[str] = None


@router.post("/interviews/generate")
async def interview_generate(body: InterviewGenIn, user: dict = Depends(require_perm("interviews.create"))):
    job = None
    if body.job_id:
        job = await db.jobs.find_one({"id": body.job_id, "org_id": user["org_id"]}, {"_id": 0})
    candidate = None
    if body.candidate_id:
        candidate = await db.candidates.find_one({"id": body.candidate_id, "org_id": user["org_id"]}, {"_id": 0})

    payload = body.model_dump()
    payload["job"] = {k: job.get(k) for k in ["title", "department", "seniority", "required_skills", "preferred_skills"]} if job else None
    payload["candidate"] = {k: candidate.get(k) for k in ["name", "extracted"]} if candidate else None

    try:
        kit = await generate_interview_kit(payload, f"interview-{user['id']}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    doc = {
        "id": new_id(), "org_id": user["org_id"],
        "job_id": body.job_id, "candidate_id": body.candidate_id,
        "interview_type": body.interview_type, "duration_minutes": body.duration_minutes,
        "kit": kit, "scorecard": None, "recommendation": None,
        "created_by": user["id"], "created_at": now_iso(), "updated_at": now_iso(),
        "is_demo": False,
    }
    await db.interviews.insert_one(doc)
    await audit(user, "interview.generate", "interview", doc["id"])
    doc.pop("_id", None)
    return doc


@router.get("/interviews")
async def list_interviews(user: dict = Depends(require_perm("interviews.read"))):
    cur = db.interviews.find({"org_id": user["org_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(500)


@router.get("/interviews/{iid}")
async def get_interview(iid: str, user: dict = Depends(require_perm("interviews.read"))):
    doc = await db.interviews.find_one({"id": iid, "org_id": user["org_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Interview not found")
    return doc


class ScorecardIn(BaseModel):
    competency_scores: List[Dict[str, Any]]  # [{competency, rating (1-5), evidence, notes}]
    strengths: Optional[str] = None
    concerns: Optional[str] = None
    recommendation: str  # Strong Hire | Hire | Lean Hire | Lean No Hire | No Hire


@router.post("/interviews/{iid}/scorecard")
async def submit_scorecard(iid: str, body: ScorecardIn, user: dict = Depends(require_perm("interviews.evaluate"))):
    valid = {"Strong Hire", "Hire", "Lean Hire", "Lean No Hire", "No Hire"}
    if body.recommendation not in valid:
        raise HTTPException(status_code=400, detail="Invalid recommendation")
    for s in body.competency_scores:
        if not (1 <= int(s.get("rating", 0)) <= 5):
            raise HTTPException(status_code=400, detail="Rating must be 1-5")
    patch = {
        "scorecard": body.model_dump(),
        "recommendation": body.recommendation,
        "submitted_by": user["id"],
        "submitted_at": now_iso(),
        "updated_at": now_iso(),
    }
    res = await db.interviews.update_one({"id": iid, "org_id": user["org_id"]}, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Interview not found")
    await audit(user, "interview.evaluate", "interview", iid, {"recommendation": body.recommendation})
    return await db.interviews.find_one({"id": iid}, {"_id": 0})


# ============ DASHBOARD OVERVIEW ============
@router.get("/overview")
async def overview(user: dict = Depends(get_current_user)):
    org = user["org_id"]
    active_jobs = await db.jobs.count_documents({"org_id": org, "status": {"$in": ["open", "draft"]}})
    total_jobs = await db.jobs.count_documents({"org_id": org})
    candidates = await db.candidates.count_documents({"org_id": org})
    interviews = await db.interviews.count_documents({"org_id": org})
    scheduled = await db.interviews.count_documents({"org_id": org, "scorecard": None})
    jds = await db.job_descriptions.count_documents({"org_id": org})

    recent_activity = await db.audit_logs.find({"org_id": org}, {"_id": 0}).sort("created_at", -1).limit(10).to_list(10)
    pipeline: Dict[str, int] = {s: 0 for s in CANDIDATE_STAGES}
    async for c in db.candidates.find({"org_id": org}, {"stage": 1, "_id": 0}):
        s = c.get("stage") or "applied"
        pipeline[s] = pipeline.get(s, 0) + 1

    return {
        "active_jobs": active_jobs, "total_jobs": total_jobs,
        "candidates": candidates, "interviews": interviews,
        "pending_interviews": scheduled, "job_descriptions": jds,
        "pipeline": pipeline, "recent_activity": recent_activity,
    }


# ============ DEMO DATA ============
demo_router = APIRouter(prefix="/demo", tags=["demo"])


DEMO_JOBS = [
    {"title": "Senior Product Manager", "department": "Product Management", "seniority": "Senior", "experience": "5-8 years",
     "employment_type": "Full-time", "work_arrangement": "Hybrid", "location": "New York, NY", "status": "open",
     "required_skills": ["Product Strategy", "Roadmapping", "Stakeholder Management", "Analytics"],
     "preferred_skills": ["SQL", "Figma"], "priority": "High", "timeline": "Within 30 Days"},
    {"title": "Talent Acquisition Manager", "department": "Talent Acquisition", "seniority": "Manager", "experience": "5-8 years",
     "employment_type": "Full-time", "work_arrangement": "On-site", "location": "London, UK", "status": "open",
     "required_skills": ["Recruitment", "Interviewing", "Employer Branding", "ATS"],
     "preferred_skills": ["LinkedIn Recruiter"], "priority": "Medium", "timeline": "Within 60 Days"},
    {"title": "Data Engineer", "department": "Data Analytics", "seniority": "Mid Level", "experience": "3-5 years",
     "employment_type": "Full-time", "work_arrangement": "Remote", "location": "Remote", "status": "open",
     "required_skills": ["Python", "SQL", "Airflow", "AWS"], "preferred_skills": ["dbt", "Snowflake"],
     "priority": "Critical", "timeline": "Immediate"},
]

DEMO_CANDIDATES = [
    {"name": "Priya Nair", "email": "priya.demo@example.com", "stage": "shortlisted"},
    {"name": "Marcus Reid", "email": "marcus.demo@example.com", "stage": "interview"},
    {"name": "Aisha Osei", "email": "aisha.demo@example.com", "stage": "screening"},
    {"name": "Diego Alvarez", "email": "diego.demo@example.com", "stage": "assessment"},
]


@demo_router.post("/seed")
async def seed_demo(user: dict = Depends(get_current_user)):
    if user["role"] != "org_admin":
        raise HTTPException(status_code=403, detail="Only Org Admin can seed demo data")
    org_id = user["org_id"]
    existing = await db.organizations.find_one({"id": org_id}, {"_id": 0})
    if existing and existing.get("demo_data_loaded"):
        return {"ok": True, "already_loaded": True}

    created_jobs = []
    for j in DEMO_JOBS:
        job = {**j, "id": new_id(), "org_id": org_id, "created_by": user["id"],
               "created_at": now_iso(), "updated_at": now_iso(), "is_demo": True}
        await db.jobs.insert_one(job)
        created_jobs.append(job)

    for i, c in enumerate(DEMO_CANDIDATES):
        cand = {
            **c, "id": new_id(), "org_id": org_id,
            "job_id": created_jobs[i % len(created_jobs)]["id"],
            "resume": None, "analysis": {
                "overall_score": 65 + i * 5,
                "skill_match_pct": 70, "experience_match_pct": 60,
                "qualification_match_pct": 65, "other_match_pct": 55,
                "matched_skills": ["Communication", "Analytics"],
                "missing_skills": ["Advanced SQL"], "strengths": ["Cross-functional experience"],
                "gaps": ["Limited industry exposure"],
                "summary": "DEMO analysis for showcase purposes only.",
                "recommendation": "Fit",
            },
            "created_at": now_iso(), "updated_at": now_iso(),
            "created_by": user["id"], "is_demo": True,
        }
        await db.candidates.insert_one(cand)

    await db.organizations.update_one({"id": org_id}, {"$set": {"demo_data_loaded": True}})
    await audit(user, "demo.seed", "organization", org_id)
    return {"ok": True, "jobs": len(DEMO_JOBS), "candidates": len(DEMO_CANDIDATES)}


@demo_router.delete("/clear")
async def clear_demo(user: dict = Depends(get_current_user)):
    if user["role"] != "org_admin":
        raise HTTPException(status_code=403, detail="Only Org Admin can clear demo data")
    org_id = user["org_id"]
    r1 = await db.jobs.delete_many({"org_id": org_id, "is_demo": True})
    r2 = await db.candidates.delete_many({"org_id": org_id, "is_demo": True})
    r3 = await db.interviews.delete_many({"org_id": org_id, "is_demo": True})
    r4 = await db.job_descriptions.delete_many({"org_id": org_id, "is_demo": True})
    await db.organizations.update_one({"id": org_id}, {"$set": {"demo_data_loaded": False}})
    await audit(user, "demo.clear", "organization", org_id)
    return {"ok": True, "removed": {"jobs": r1.deleted_count, "candidates": r2.deleted_count,
                                     "interviews": r3.deleted_count, "job_descriptions": r4.deleted_count}}
