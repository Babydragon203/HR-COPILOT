"""Claude Sonnet 5 AI service via Emergent Universal Key."""
import os
import json
import re
import logging
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ["EMERGENT_LLM_KEY"]
MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-5"


def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from a model response."""
    if not text:
        raise ValueError("Empty AI response")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in AI response")
    return json.loads(text[start : end + 1])


async def _chat(system: str, user: str, session_id: str) -> str:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=system,
    ).with_model(MODEL_PROVIDER, MODEL_NAME)
    resp = await chat.send_message(UserMessage(text=user))
    return resp if isinstance(resp, str) else str(resp)


async def generate_job_description(payload: dict, session_id: str) -> dict:
    system = (
        "You are an expert HR content writer creating professional, inclusive job descriptions. "
        "Output STRICT JSON matching the requested schema. Do not invent salaries or benefits not provided. "
        "Be specific and role-relevant, avoid discriminatory language."
    )
    schema = {
        "job_title": "string",
        "company_overview": "string",
        "job_summary": "string",
        "responsibilities": ["string"],
        "required_skills": ["string"],
        "preferred_skills": ["string"],
        "qualifications": ["string"],
        "experience": "string",
        "reporting_structure": "string",
        "kpis": ["string"],
        "benefits": ["string"],
        "career_growth": "string",
        "diversity_statement": "string",
    }
    # Core fields must ALWAYS be populated. Only these optional sections are gated by include_sections.
    OPTIONAL = {"Company Overview": "company_overview", "KPIs": "kpis", "Benefits": "benefits",
                "Career Growth": "career_growth", "Diversity Statement": "diversity_statement"}
    include = set(payload.get("include_sections") or list(OPTIONAL.keys()))
    excluded_keys = [k for name, k in OPTIONAL.items() if name not in include]
    prompt = (
        f"Create a job description for the following role. Return ONLY a JSON object matching this schema (no prose):\n"
        f"SCHEMA: {json.dumps(schema)}\n\nROLE INPUT:\n{json.dumps(payload, indent=2)}\n\n"
        f"Style: {payload.get('style', 'Professional')}. Length: {payload.get('length', 'Standard')}. "
        f"CORE FIELDS (always populate with substantive content): job_title, job_summary, responsibilities, "
        f"required_skills, preferred_skills, qualifications, experience, reporting_structure. "
        f"OPTIONAL fields to LEAVE EMPTY (empty string or empty list): {excluded_keys or 'none'}. "
        f"Do not leave any core field empty; use the ROLE INPUT skills/experience/etc. as ground truth."
    )
    text = await _chat(system, prompt, session_id)
    return _extract_json(text)


async def analyze_resume(resume_text: str, job: dict, weights: dict, session_id: str) -> dict:
    system = (
        "You are an expert HR resume analyst. Extract structured candidate data and compare to a job. "
        "Never invent missing information — use 'Not detected' if absent. Return STRICT JSON only. "
        "Score is heuristic, not a hiring decision."
    )
    schema = {
        "candidate": {
            "name": "string", "email": "string", "phone": "string",
            "current_title": "string", "years_experience": "number",
            "education": [{"degree": "string", "institution": "string", "year": "string"}],
            "experience": [{"company": "string", "title": "string", "duration": "string", "highlights": ["string"]}],
            "skills": ["string"],
            "certifications": ["string"],
            "languages": ["string"],
        },
        "analysis": {
            "skill_match_pct": "0-100",
            "experience_match_pct": "0-100",
            "qualification_match_pct": "0-100",
            "other_match_pct": "0-100",
            "overall_score": "0-100 weighted",
            "matched_skills": ["string"],
            "missing_skills": ["string"],
            "strengths": ["string"],
            "gaps": ["string"],
            "summary": "string (2-3 sentences)",
            "recommendation": "One of: Strong Fit / Fit / Partial Fit / Weak Fit",
        },
    }
    prompt = (
        f"Analyze this resume against the job. Return ONLY JSON matching the schema.\n"
        f"SCHEMA: {json.dumps(schema)}\n"
        f"WEIGHTS: {json.dumps(weights)}\n"
        f"JOB: {json.dumps({k: job.get(k) for k in ['title', 'department', 'seniority', 'required_skills', 'preferred_skills', 'experience', 'qualifications']})}\n\n"
        f"RESUME TEXT:\n{resume_text[:12000]}"
    )
    text = await _chat(system, prompt, session_id)
    data = _extract_json(text)
    # Weighted score recompute for consistency
    a = data.get("analysis", {})
    w = weights
    total_w = sum(w.values()) or 100
    try:
        overall = (
            a.get("skill_match_pct", 0) * w.get("skills", 40)
            + a.get("experience_match_pct", 0) * w.get("experience", 30)
            + a.get("qualification_match_pct", 0) * w.get("qualifications", 20)
            + a.get("other_match_pct", 0) * w.get("other", 10)
        ) / total_w
        a["overall_score"] = round(overall, 1)
        data["analysis"] = a
    except Exception:
        pass
    return data


async def generate_interview_kit(payload: dict, session_id: str) -> dict:
    system = (
        "You are an expert interview designer. Create structured, role-specific interview kits. "
        "Avoid generic questions when job context is provided. Return STRICT JSON only."
    )
    schema = {
        "plan": {"summary": "string", "duration_minutes": "number", "sections": [{"name": "string", "minutes": "number"}]},
        "questions": [
            {
                "id": "string",
                "category": "Behavioral|Technical|Situational|Role-specific|Leadership|Communication|Problem Solving|Culture",
                "question": "string",
                "follow_ups": ["string"],
                "what_good_looks_like": "string",
                "red_flags": ["string"],
            }
        ],
        "competencies": [{"name": "string", "description": "string"}],
        "scoring_rubric": {
            "1": "Poor", "2": "Needs Development", "3": "Meets Expectations", "4": "Strong", "5": "Exceptional"
        },
        "candidate_notes_prompts": ["string"],
    }
    prompt = (
        f"Create an interview kit for this role. Return ONLY JSON matching schema.\n"
        f"SCHEMA: {json.dumps(schema)}\n\nINPUT:\n{json.dumps(payload, indent=2)}\n\n"
        f"Generate 8-12 questions across relevant categories for interview type '{payload.get('interview_type')}'. "
        f"Include role/seniority-specific technical depth where applicable."
    )
    text = await _chat(system, prompt, session_id)
    return _extract_json(text)


async def refine_jd_section(section_name: str, current_text: str, action: str, session_id: str) -> str:
    system = "You are an expert HR writer. Return only the improved section text, no preamble."
    prompt = (
        f"Refine the following JD section '{section_name}'. Action: {action}.\n\n"
        f"CURRENT:\n{current_text}\n\nReturn only the new text."
    )
    return (await _chat(system, prompt, session_id)).strip()


async def generate_learning_recommendations(payload: dict, session_id: str) -> dict:
    system = (
        "You are an expert L&D advisor. Given an employee's skill gaps, produce actionable, "
        "role-appropriate learning recommendations across multiple modalities. "
        "Do NOT invent external course providers, URLs, prices, or specific certifications — "
        "keep suggestions generic and applicable (e.g., 'a structured stakeholder management course'). "
        "Return STRICT JSON only."
    )
    schema = {
        "overview": "string (2-3 sentences summarizing the growth plan)",
        "recommendations": [
            {
                "competency": "string",
                "priority": "Critical|High|Medium|Low",
                "learning_objective": "string",
                "activities": [
                    {
                        "category": "Course|Certification|Project|Mentoring|Coaching|Workshop|Reading|On-the-job",
                        "title": "string",
                        "reason": "string",
                        "expected_outcome": "string",
                        "estimated_effort": "string (e.g. '4 weeks · 3 hrs/week')",
                    }
                ],
            }
        ],
    }
    prompt = (
        f"Create a learning plan for an employee. Return ONLY JSON matching the schema.\n"
        f"SCHEMA: {json.dumps(schema)}\n\nINPUT:\n{json.dumps(payload, indent=2)}\n\n"
        f"Generate 3-6 activities per gap across a mix of modalities. Keep language concrete and generic."
    )
    text = await _chat(system, prompt, session_id)
    data = _extract_json(text)
    # Initialize activity status for progress tracking
    for r in data.get("recommendations", []):
        for a in r.get("activities", []):
            a.setdefault("status", "todo")
            a.setdefault("completed_at", None)
    return data


async def career_coach_reply(employee: dict, history, message: str, session_id: str) -> str:
    system = (
        f"You are an expert career coach helping {employee.get('name') or 'an employee'} "
        f"(current role: {employee.get('role') or 'unknown'}, department: {employee.get('department') or 'unknown'}). "
        "Ask thoughtful, open-ended questions to understand their goals, motivations, strengths, and constraints. "
        "Keep replies concise (2-4 sentences), warm but professional. Do NOT draft a full plan in chat — "
        "when you have gathered enough context (usually 4-6 exchanges), suggest the user click 'Draft 90-day plan'."
    )
    convo = "\n".join(f"[{t.get('role','user').upper()}] {t.get('content','')}" for t in (history or [])[-10:])
    prompt = f"Conversation so far:\n{convo}\n[USER] {message}\n\nRespond as the coach."
    return (await _chat(system, prompt, session_id)).strip()


async def generate_growth_plan(employee: dict, history, session_id: str) -> dict:
    system = (
        "You are a career coach synthesizing a conversation into a personalized 90-day growth plan. "
        "Return STRICT JSON only. Base every phase on what the employee actually shared; if the conversation is "
        "thin, still produce a reasonable plan but keep it generic to what was said."
    )
    schema = {
        "north_star": "string — the employee's stated career direction",
        "focus_areas": ["string"],
        "weeks_1_4": {"theme": "string", "activities": [{"title": "string", "why": "string", "effort": "string"}], "milestones": ["string"]},
        "weeks_5_8": {"theme": "string", "activities": [{"title": "string", "why": "string", "effort": "string"}], "milestones": ["string"]},
        "weeks_9_12": {"theme": "string", "activities": [{"title": "string", "why": "string", "effort": "string"}], "milestones": ["string"]},
        "success_metrics": ["string"],
        "check_ins": ["string"],
    }
    convo = "\n".join(f"[{t.get('role','user').upper()}] {t.get('content','')}" for t in (history or [])[-30:])
    prompt = (
        f"Employee: {json.dumps({k: employee.get(k) for k in ('name','role','department','email')})}\n"
        f"Conversation:\n{convo}\n\nReturn JSON matching:\n{json.dumps(schema)}"
    )
    text = await _chat(system, prompt, session_id)
    return _extract_json(text)
