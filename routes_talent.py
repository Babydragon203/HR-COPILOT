"""Talent Development routes: competencies, frameworks, employees, skill gaps, dev plans."""
import copy
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from deps import db, get_current_user, require_perm, audit, now_iso, new_id
from ai_service import generate_learning_recommendations, career_coach_reply, generate_growth_plan

router = APIRouter(tags=["talent"])


# ---------- Global competency library (seeded constants + org-custom) ----------
DEFAULT_COMPETENCIES = [
    # Leadership
    ("Strategic Thinking", "Leadership"), ("Vision Setting", "Leadership"),
    ("People Management", "Leadership"), ("Coaching & Mentoring", "Leadership"),
    ("Change Management", "Leadership"), ("Decision Making", "Leadership"),
    ("Executive Presence", "Leadership"), ("Delegation", "Leadership"),
    # Communication
    ("Written Communication", "Communication"), ("Verbal Communication", "Communication"),
    ("Presentation Skills", "Communication"), ("Active Listening", "Communication"),
    ("Negotiation", "Communication"), ("Storytelling", "Communication"),
    # Problem Solving
    ("Analytical Thinking", "Problem Solving"), ("Critical Thinking", "Problem Solving"),
    ("Structured Problem Solving", "Problem Solving"), ("Root Cause Analysis", "Problem Solving"),
    ("Data-Driven Decision Making", "Problem Solving"),
    # Collaboration
    ("Cross-functional Collaboration", "Collaboration"), ("Stakeholder Management", "Collaboration"),
    ("Conflict Resolution", "Collaboration"), ("Team Building", "Collaboration"),
    ("Empathy", "Collaboration"),
    # Execution
    ("Ownership", "Execution"), ("Attention to Detail", "Execution"),
    ("Time Management", "Execution"), ("Prioritization", "Execution"),
    ("Results Orientation", "Execution"),
    # Adaptability
    ("Adaptability", "Adaptability"), ("Learning Agility", "Adaptability"),
    ("Resilience", "Adaptability"), ("Ambiguity Tolerance", "Adaptability"),
    # Customer
    ("Customer Orientation", "Customer"), ("Customer Empathy", "Customer"),
    ("Service Mindset", "Customer"),
    # Innovation
    ("Innovation", "Innovation"), ("Creativity", "Innovation"),
    ("Continuous Improvement", "Innovation"),
    # Technical / Product
    ("Technical Depth", "Technical"), ("System Design", "Technical"),
    ("Code Quality", "Technical"), ("Product Sense", "Technical"),
    ("Data Literacy", "Technical"),
    # Project / Program
    ("Project Management", "Project Management"), ("Program Management", "Project Management"),
    ("Risk Management", "Project Management"), ("Resource Planning", "Project Management"),
    # People Ops
    ("Performance Management", "People Ops"), ("Talent Development", "People Ops"),
    ("Hiring & Interviewing", "People Ops"),
]


@router.get("/competencies")
async def list_competencies(user: dict = Depends(get_current_user)):
    org_custom = await db.competencies.find(
        {"org_id": user["org_id"]}, {"_id": 0}
    ).to_list(500)
    globals_ = [{"id": f"g:{name}", "name": name, "category": cat, "is_global": True}
                for name, cat in DEFAULT_COMPETENCIES]
    return globals_ + org_custom


class CompetencyIn(BaseModel):
    name: str
    category: str
    description: Optional[str] = None


@router.post("/competencies")
async def create_competency(body: CompetencyIn, user: dict = Depends(get_current_user)):
    doc = {"id": new_id(), "org_id": user["org_id"], "name": body.name.strip(),
           "category": body.category, "description": body.description,
           "is_global": False, "created_at": now_iso()}
    await db.competencies.insert_one(doc)
    await audit(user, "competency.create", "competency", doc["id"], {"name": doc["name"]})
    doc.pop("_id", None)
    return doc


# ---------- Frameworks ----------
class FrameworkCompetency(BaseModel):
    name: str
    category: Optional[str] = None
    required_level: int = Field(ge=1, le=5)
    weight: int = Field(ge=0, le=100)


class FrameworkIn(BaseModel):
    name: str
    department: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    competencies: List[FrameworkCompetency]


@router.get("/frameworks")
async def list_frameworks(user: dict = Depends(get_current_user)):
    cur = db.frameworks.find({"org_id": user["org_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(500)


@router.post("/frameworks")
async def create_framework(body: FrameworkIn, user: dict = Depends(get_current_user)):
    if not body.competencies:
        raise HTTPException(status_code=400, detail="At least one competency is required")
    total = sum(c.weight for c in body.competencies)
    if total != 100:
        raise HTTPException(status_code=400, detail=f"Competency weights must sum to 100 (got {total})")
    doc = {
        "id": new_id(), "org_id": user["org_id"],
        "name": body.name.strip(), "department": body.department, "role": body.role,
        "description": body.description,
        "competencies": [c.model_dump() for c in body.competencies],
        "created_by": user["id"], "created_at": now_iso(), "updated_at": now_iso(),
        "is_demo": False,
    }
    await db.frameworks.insert_one(doc)
    await audit(user, "framework.create", "framework", doc["id"], {"name": doc["name"]})
    doc.pop("_id", None)
    return doc


@router.get("/frameworks/{fid}")
async def get_framework(fid: str, user: dict = Depends(get_current_user)):
    doc = await db.frameworks.find_one({"id": fid, "org_id": user["org_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Framework not found")
    return doc


@router.delete("/frameworks/{fid}")
async def delete_framework(fid: str, user: dict = Depends(get_current_user)):
    res = await db.frameworks.delete_one({"id": fid, "org_id": user["org_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Framework not found")
    await audit(user, "framework.delete", "framework", fid)
    return {"ok": True}


class CloneFrameworkIn(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    bump_required_level: int = Field(default=0, ge=-2, le=2)  # e.g. +1 for next seniority


@router.post("/frameworks/{fid}/clone")
async def clone_framework(fid: str, body: CloneFrameworkIn, user: dict = Depends(get_current_user)):
    src = await db.frameworks.find_one({"id": fid, "org_id": user["org_id"]}, {"_id": 0})
    if not src:
        raise HTTPException(status_code=404, detail="Framework not found")
    comps = copy.deepcopy(src["competencies"])
    for c in comps:
        lvl = int(c.get("required_level", 3)) + body.bump_required_level
        c["required_level"] = max(1, min(5, lvl))
    doc = {
        "id": new_id(), "org_id": user["org_id"],
        "name": (body.name or f"{src['name']} (copy)").strip(),
        "department": body.department if body.department is not None else src.get("department"),
        "role": body.role if body.role is not None else src.get("role"),
        "description": src.get("description"),
        "competencies": comps,
        "cloned_from": fid,
        "created_by": user["id"], "created_at": now_iso(), "updated_at": now_iso(),
        "is_demo": False,
    }
    await db.frameworks.insert_one(doc)
    await audit(user, "framework.clone", "framework", doc["id"], {"from": fid, "bump": body.bump_required_level})
    doc.pop("_id", None)
    return doc


# ---------- Employees ----------
class EmployeeIn(BaseModel):
    name: str
    email: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None


@router.get("/employees")
async def list_employees(user: dict = Depends(get_current_user)):
    cur = db.employees.find({"org_id": user["org_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(500)


@router.post("/employees")
async def create_employee(body: EmployeeIn, user: dict = Depends(get_current_user)):
    doc = {"id": new_id(), "org_id": user["org_id"], **body.model_dump(),
           "created_at": now_iso(), "created_by": user["id"], "is_demo": False}
    await db.employees.insert_one(doc)
    await audit(user, "employee.create", "employee", doc["id"], {"name": body.name})
    doc.pop("_id", None)
    return doc


class BulkEmployeeIn(BaseModel):
    employees: List[EmployeeIn]


@router.post("/employees/bulk")
async def bulk_create_employees(body: BulkEmployeeIn, user: dict = Depends(get_current_user)):
    if not body.employees:
        raise HTTPException(status_code=400, detail="No employees provided")
    if len(body.employees) > 500:
        raise HTTPException(status_code=400, detail="Max 500 employees per import")
    now = now_iso()
    docs = [
        {"id": new_id(), "org_id": user["org_id"], **e.model_dump(),
         "created_at": now, "created_by": user["id"], "is_demo": False}
        for e in body.employees
    ]
    await db.employees.insert_many(docs)
    await audit(user, "employee.bulk_import", "employee", f"batch-{len(docs)}", {"count": len(docs)})
    for d in docs:
        d.pop("_id", None)
    return {"created": len(docs), "employees": docs}


@router.delete("/employees/{eid}")
async def delete_employee(eid: str, user: dict = Depends(get_current_user)):
    res = await db.employees.delete_one({"id": eid, "org_id": user["org_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    await db.employee_mappings.delete_many({"employee_id": eid, "org_id": user["org_id"]})
    await audit(user, "employee.delete", "employee", eid)
    return {"ok": True}


# ---------- Employee ↔ Framework mapping ----------
class MappingItem(BaseModel):
    competency: str
    current_level: int = Field(ge=0, le=5)  # 0 = not assessed
    target_level: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = None


class MappingIn(BaseModel):
    employee_id: str
    framework_id: str
    mappings: List[MappingItem]


@router.get("/employee-mappings")
async def list_mappings(employee_id: Optional[str] = None, framework_id: Optional[str] = None,
                        user: dict = Depends(get_current_user)):
    q = {"org_id": user["org_id"]}
    if employee_id: q["employee_id"] = employee_id
    if framework_id: q["framework_id"] = framework_id
    cur = db.employee_mappings.find(q, {"_id": 0}).sort("updated_at", -1)
    return await cur.to_list(500)


@router.post("/employee-mappings")
async def save_mapping(body: MappingIn, user: dict = Depends(get_current_user)):
    emp = await db.employees.find_one({"id": body.employee_id, "org_id": user["org_id"]})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    fw = await db.frameworks.find_one({"id": body.framework_id, "org_id": user["org_id"]})
    if not fw:
        raise HTTPException(status_code=404, detail="Framework not found")

    existing = await db.employee_mappings.find_one(
        {"employee_id": body.employee_id, "framework_id": body.framework_id, "org_id": user["org_id"]}
    )
    doc_id = existing["id"] if existing else new_id()
    set_fields = {
        "id": doc_id,
        "org_id": user["org_id"],
        "employee_id": body.employee_id, "framework_id": body.framework_id,
        "mappings": [m.model_dump() for m in body.mappings],
        "updated_at": now_iso(),
    }
    set_on_insert = {"created_at": now_iso(), "created_by": user["id"]}
    await db.employee_mappings.update_one(
        {"employee_id": body.employee_id, "framework_id": body.framework_id, "org_id": user["org_id"]},
        {"$set": set_fields, "$setOnInsert": set_on_insert},
        upsert=True,
    )
    doc = await db.employee_mappings.find_one({"id": doc_id}, {"_id": 0})
    await audit(user, "mapping.save", "employee_mapping", doc["id"],
                {"employee_id": body.employee_id, "framework_id": body.framework_id})
    return doc


# ---------- Skill Gap Analyzer ----------
class GapIn(BaseModel):
    employee_id: str
    framework_id: str


def _priority(gap: int, weight: int, req_level: int) -> str:
    # Weighted gap severity
    if gap == 0:
        return "Met"
    score = gap * weight * (req_level / 5.0)
    if score >= 60: return "Critical"
    if score >= 30: return "High"
    if score >= 12: return "Medium"
    return "Low"


@router.post("/skill-gaps/analyze")
async def analyze_gap(body: GapIn, user: dict = Depends(get_current_user)):
    emp = await db.employees.find_one({"id": body.employee_id, "org_id": user["org_id"]}, {"_id": 0})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    fw = await db.frameworks.find_one({"id": body.framework_id, "org_id": user["org_id"]}, {"_id": 0})
    if not fw:
        raise HTTPException(status_code=404, detail="Framework not found")
    mapping = await db.employee_mappings.find_one(
        {"employee_id": body.employee_id, "framework_id": body.framework_id, "org_id": user["org_id"]},
        {"_id": 0},
    )

    curr_by_name = {m["competency"]: m for m in (mapping or {}).get("mappings", [])}
    items: List[Dict[str, Any]] = []
    total_gap_score = 0
    total_weight = 0
    for c in fw["competencies"]:
        m = curr_by_name.get(c["name"])
        current = m["current_level"] if m else 0
        assessed = bool(m) and current > 0
        gap = max(0, c["required_level"] - current) if assessed else c["required_level"]
        weight = c["weight"]
        total_weight += weight
        total_gap_score += (gap / 5.0) * weight
        items.append({
            "competency": c["name"],
            "category": c.get("category"),
            "required_level": c["required_level"],
            "current_level": current,
            "weight": weight,
            "assessed": assessed,
            "gap": gap,
            "priority": _priority(gap, weight, c["required_level"]) if assessed else "Not Assessed",
            "notes": (m or {}).get("notes"),
        })

    readiness = round(100 - (total_gap_score / max(total_weight, 1) * 100), 1)
    items.sort(key=lambda i: (
        {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Met": 4, "Not Assessed": 5}[i["priority"]],
        -i["weight"],
    ))
    result = {
        "employee": emp, "framework": {"id": fw["id"], "name": fw["name"], "role": fw.get("role")},
        "readiness_pct": max(0.0, readiness),
        "items": items,
        "generated_at": now_iso(),
    }
    await audit(user, "gap.analyze", "employee_mapping",
                (mapping or {}).get("id") or "n/a",
                {"employee_id": body.employee_id, "framework_id": body.framework_id})
    return result


# ---------- Development Plans ----------
class DevPlanItemIn(BaseModel):
    competency: str
    priority: str  # Critical | High | Medium | Low
    current_level: int
    target_level: int
    activities: List[str] = []


class DevPlanIn(BaseModel):
    name: str
    employee_id: str
    framework_id: Optional[str] = None
    start_date: Optional[str] = None
    target_date: Optional[str] = None
    items: List[DevPlanItemIn]
    status: str = "Not Started"


@router.get("/development-plans")
async def list_plans(user: dict = Depends(get_current_user)):
    cur = db.development_plans.find({"org_id": user["org_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(500)


@router.post("/development-plans")
async def create_plan(body: DevPlanIn, user: dict = Depends(get_current_user)):
    valid_status = {"Not Started", "In Progress", "Completed", "On Hold"}
    if body.status not in valid_status:
        raise HTTPException(status_code=400, detail="Invalid status")
    emp = await db.employees.find_one({"id": body.employee_id, "org_id": user["org_id"]})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if body.framework_id:
        fw = await db.frameworks.find_one({"id": body.framework_id, "org_id": user["org_id"]})
        if not fw:
            raise HTTPException(status_code=404, detail="Framework not found")
    doc = {"id": new_id(), "org_id": user["org_id"], **body.model_dump(),
           "created_at": now_iso(), "updated_at": now_iso(), "created_by": user["id"]}
    await db.development_plans.insert_one(doc)
    await audit(user, "devplan.create", "development_plan", doc["id"], {"name": body.name})
    doc.pop("_id", None)
    return doc


# ---------- Career Path ----------
@router.get("/employees/{eid}/career-path")
async def employee_career_path(eid: str, user: dict = Depends(get_current_user)):
    emp = await db.employees.find_one({"id": eid, "org_id": user["org_id"]}, {"_id": 0})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    fws = await db.frameworks.find({"org_id": user["org_id"]}, {"_id": 0}).to_list(500)
    mappings = await db.employee_mappings.find(
        {"employee_id": eid, "org_id": user["org_id"]}, {"_id": 0}
    ).to_list(500)
    mapping_by_fw = {m["framework_id"]: m for m in mappings}

    paths = []
    for fw in fws:
        m = mapping_by_fw.get(fw["id"])
        curr = {x["competency"]: x for x in (m or {}).get("mappings", [])}
        gap_score = 0
        total_weight = 0
        assessed_count = 0
        top_gaps = []
        for c in fw["competencies"]:
            mm = curr.get(c["name"])
            current = mm["current_level"] if mm else 0
            assessed = bool(mm) and current > 0
            if assessed:
                assessed_count += 1
            gap = max(0, c["required_level"] - current) if assessed else c["required_level"]
            total_weight += c["weight"]
            gap_score += (gap / 5.0) * c["weight"]
            top_gaps.append({"competency": c["name"], "gap": gap, "weight": c["weight"]})
        readiness = round(100 - (gap_score / max(total_weight, 1) * 100), 1)
        top_gaps = [g for g in top_gaps if g["gap"] > 0]
        top_gaps.sort(key=lambda g: (-g["gap"] * g["weight"], -g["weight"]))
        # relevance = role/department match
        role_match = (emp.get("role") or "").lower() == (fw.get("role") or "").lower() if fw.get("role") else False
        dept_match = (emp.get("department") or "").lower() == (fw.get("department") or "").lower() if fw.get("department") else False
        paths.append({
            "framework_id": fw["id"], "framework_name": fw["name"],
            "role": fw.get("role"), "department": fw.get("department"),
            "readiness_pct": max(0.0, readiness),
            "assessed_count": assessed_count,
            "total_competencies": len(fw["competencies"]),
            "top_gaps": top_gaps[:3],
            "role_match": role_match, "department_match": dept_match,
            "has_mapping": bool(m),
        })
    # Order: current role match first, then dept, then by readiness desc
    paths.sort(key=lambda p: (not p["role_match"], not p["department_match"], -p["readiness_pct"]))
    return {"employee": emp, "paths": paths}


# ---------- Learning Recommendations ----------
class LearningGenIn(BaseModel):
    employee_id: str
    framework_id: str
    development_plan_id: Optional[str] = None


@router.get("/learning")
async def list_learning(user: dict = Depends(get_current_user)):
    cur = db.learning_recommendations.find({"org_id": user["org_id"]}, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(200)


@router.post("/learning/generate")
async def generate_learning(body: LearningGenIn, user: dict = Depends(get_current_user)):
    emp = await db.employees.find_one({"id": body.employee_id, "org_id": user["org_id"]}, {"_id": 0})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    fw = await db.frameworks.find_one({"id": body.framework_id, "org_id": user["org_id"]}, {"_id": 0})
    if not fw:
        raise HTTPException(status_code=404, detail="Framework not found")
    mapping = await db.employee_mappings.find_one(
        {"employee_id": body.employee_id, "framework_id": body.framework_id, "org_id": user["org_id"]},
        {"_id": 0},
    )

    # Build gap payload for the AI
    curr = {x["competency"]: x for x in (mapping or {}).get("mappings", [])}
    gaps = []
    for c in fw["competencies"]:
        mm = curr.get(c["name"])
        current = mm["current_level"] if mm else 0
        if not mm or current <= 0:
            continue
        gap = max(0, c["required_level"] - current)
        if gap <= 0:
            continue
        gaps.append({
            "competency": c["name"], "category": c.get("category"),
            "current_level": current, "required_level": c["required_level"],
            "weight": c["weight"], "gap": gap,
        })
    if not gaps:
        raise HTTPException(status_code=400, detail="No assessed gaps to generate learning for. Map the employee first.")

    payload = {
        "employee": {"name": emp.get("name"), "role": emp.get("role"), "department": emp.get("department")},
        "target_role": fw.get("role"),
        "framework": fw["name"],
        "gaps": gaps,
    }
    try:
        content = await generate_learning_recommendations(payload, session_id=f"learn-{user['id']}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    doc = {
        "id": new_id(), "org_id": user["org_id"],
        "employee_id": body.employee_id, "framework_id": body.framework_id,
        "development_plan_id": body.development_plan_id,
        "content": content,
        "created_by": user["id"], "created_at": now_iso(),
    }
    await db.learning_recommendations.insert_one(doc)
    await audit(user, "learning.generate", "learning", doc["id"],
                {"employee_id": body.employee_id, "framework_id": body.framework_id})
    doc.pop("_id", None)
    return doc


# ---------- Progress Tracking ----------
class ActivityStatusIn(BaseModel):
    rec_index: int = Field(ge=0)
    act_index: int = Field(ge=0)
    status: str  # todo | in_progress | done


@router.patch("/learning/{lrid}/activity")
async def update_activity_status(lrid: str, body: ActivityStatusIn, user: dict = Depends(get_current_user)):
    if body.status not in {"todo", "in_progress", "done"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    doc = await db.learning_recommendations.find_one({"id": lrid, "org_id": user["org_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    recs = doc.get("content", {}).get("recommendations", [])
    if body.rec_index >= len(recs):
        raise HTTPException(status_code=400, detail="rec_index out of range")
    activities = recs[body.rec_index].get("activities", [])
    if body.act_index >= len(activities):
        raise HTTPException(status_code=400, detail="act_index out of range")
    prefix = f"content.recommendations.{body.rec_index}.activities.{body.act_index}"
    await db.learning_recommendations.update_one(
        {"id": lrid, "org_id": user["org_id"]},
        {"$set": {
            f"{prefix}.status": body.status,
            f"{prefix}.completed_at": now_iso() if body.status == "done" else None,
            "updated_at": now_iso(),
        }},
    )
    await audit(user, "learning.activity_status", "learning", lrid,
                {"rec": body.rec_index, "act": body.act_index, "status": body.status})
    return await db.learning_recommendations.find_one({"id": lrid, "org_id": user["org_id"]}, {"_id": 0})


class LevelUpIn(BaseModel):
    competency: str
    new_level: int = Field(ge=1, le=5)


@router.post("/learning/{lrid}/level-up")
async def confirm_level_up(lrid: str, body: LevelUpIn, user: dict = Depends(get_current_user)):
    doc = await db.learning_recommendations.find_one({"id": lrid, "org_id": user["org_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    mapping = await db.employee_mappings.find_one(
        {"employee_id": doc["employee_id"], "framework_id": doc["framework_id"], "org_id": user["org_id"]},
        {"_id": 0},
    )
    if not mapping:
        raise HTTPException(status_code=404, detail="Employee mapping not found")
    found = False
    for m in mapping["mappings"]:
        if m["competency"] == body.competency:
            m["current_level"] = body.new_level
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Competency not present in mapping")
    await db.employee_mappings.update_one(
        {"id": mapping["id"]},
        {"$set": {"mappings": mapping["mappings"], "updated_at": now_iso()}},
    )
    await audit(user, "mapping.level_up", "employee_mapping", mapping["id"],
                {"competency": body.competency, "new_level": body.new_level, "via_learning": lrid})
    return await db.employee_mappings.find_one({"id": mapping["id"]}, {"_id": 0})


# ---------- AI Career Coach ----------
class CoachChatIn(BaseModel):
    employee_id: str
    history: List[Dict[str, Any]] = []  # [{role: "user"|"assistant", content}]
    message: str


@router.post("/coach/chat")
async def coach_chat(body: CoachChatIn, user: dict = Depends(get_current_user)):
    emp = await db.employees.find_one({"id": body.employee_id, "org_id": user["org_id"]}, {"_id": 0})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        reply = await career_coach_reply(emp, body.history, body.message, f"coach-{user['id']}-{body.employee_id}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI failed: {e}")
    return {"reply": reply}


class CoachPlanIn(BaseModel):
    employee_id: str
    history: List[Dict[str, Any]]


@router.post("/coach/plan")
async def coach_plan(body: CoachPlanIn, user: dict = Depends(get_current_user)):
    emp = await db.employees.find_one({"id": body.employee_id, "org_id": user["org_id"]}, {"_id": 0})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not body.history:
        raise HTTPException(status_code=400, detail="Have a short conversation with the coach first.")
    try:
        plan = await generate_growth_plan(emp, body.history, f"coach-plan-{user['id']}-{body.employee_id}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI failed: {e}")
    doc = {
        "id": new_id(), "org_id": user["org_id"], "employee_id": body.employee_id,
        "content": plan, "history": body.history,
        "created_by": user["id"], "created_at": now_iso(),
    }
    await db.growth_plans.insert_one(doc)
    await audit(user, "coach.plan", "growth_plan", doc["id"])
    doc.pop("_id", None)
    return doc


@router.get("/coach/plans")
async def list_growth_plans(employee_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    q = {"org_id": user["org_id"]}
    if employee_id:
        q["employee_id"] = employee_id
    cur = db.growth_plans.find(q, {"_id": 0}).sort("created_at", -1)
    return await cur.to_list(200)


# ---------- Candidate Comparison ----------
class CompareIn(BaseModel):
    candidate_ids: List[str]


@router.post("/candidates/compare")
async def compare_candidates(body: CompareIn, user: dict = Depends(require_perm("candidates.read"))):
    # Dedup preserving order
    seen = set()
    ids = [i for i in body.candidate_ids if not (i in seen or seen.add(i))]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="Select at least 2 candidates to compare")
    if len(ids) > 6:
        raise HTTPException(status_code=400, detail="Compare up to 6 candidates at a time")
    cur = db.candidates.find(
        {"id": {"$in": ids}, "org_id": user["org_id"]}, {"_id": 0}
    )
    docs = await cur.to_list(10)
    if len(docs) != len(ids):
        raise HTTPException(status_code=404, detail="One or more candidates not found in your organization")

    # Build union sets of skills for a matrix view
    all_matched: set = set()
    all_missing: set = set()
    for c in docs:
        a = c.get("analysis") or {}
        all_matched.update(a.get("matched_skills") or [])
        all_missing.update(a.get("missing_skills") or [])
    union = sorted(all_matched | all_missing)

    rows = []
    for skill in union:
        row = {"skill": skill, "candidates": {}}
        for c in docs:
            a = c.get("analysis") or {}
            m = set(a.get("matched_skills") or [])
            miss = set(a.get("missing_skills") or [])
            if skill in m:
                row["candidates"][c["id"]] = "match"
            elif skill in miss:
                row["candidates"][c["id"]] = "gap"
            else:
                row["candidates"][c["id"]] = "unknown"
        rows.append(row)

    return {
        "candidates": [
            {
                "id": c["id"], "name": c["name"], "email": c.get("email"),
                "stage": c.get("stage"), "job_id": c.get("job_id"),
                "is_demo": c.get("is_demo", False),
                "score": (c.get("analysis") or {}).get("overall_score"),
                "skill_pct": (c.get("analysis") or {}).get("skill_match_pct"),
                "experience_pct": (c.get("analysis") or {}).get("experience_match_pct"),
                "qualification_pct": (c.get("analysis") or {}).get("qualification_match_pct"),
                "other_pct": (c.get("analysis") or {}).get("other_match_pct"),
                "recommendation": (c.get("analysis") or {}).get("recommendation"),
                "summary": (c.get("analysis") or {}).get("summary"),
                "strengths": (c.get("analysis") or {}).get("strengths") or [],
                "gaps": (c.get("analysis") or {}).get("gaps") or [],
            }
            for c in docs
        ],
        "skill_matrix": rows,
    }
