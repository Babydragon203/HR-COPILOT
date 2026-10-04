# HR Copilot AI — Product Requirements Document

## Problem statement
Build the Recruitment portion of "HR Copilot AI", a production-oriented B2B SaaS platform. Tagline: "AI-powered HR workflows, from hiring to talent development." V1 ships 3 recruitment modules; V2 will add Talent Development.

## User personas
- **Organization Admin** (Ayush @ HR Copilot Demo Org): configures org, seeds/clears demo, invites team, full permissions.
- **HR Manager / Recruiter**: creates jobs & JDs, uploads/analyzes resumes, moves candidates through pipeline, generates interview kits.
- **Hiring Manager**: reviews candidates, submits interview scorecards & recommendations.
- **Viewer**: read-only stakeholder.

## Core requirements
- Multi-tenant (organization_id on every business record; strict isolation).
- JWT email/password auth (Bearer token + httpOnly cookie fallback).
- RBAC: `org_admin`, `hr_manager`, `recruiter`, `hiring_manager`, `viewer` — enforced in backend deps.
- Emergent Object Storage for resumes (PDF/DOCX/TXT, ≤10 MB), text extracted server-side (pypdf, python-docx).
- Claude Sonnet 5 via Emergent Universal Key for JD generation, resume analysis, interview kits.
- Structured JSON outputs, validated before persistence.
- "AI-generated content. Review before making HR decisions." banner on every AI output.
- Internal Job Match Score labeled as heuristic — never presented as a hiring decision.
- Clearly labeled DEMO DATA (seed / clear from Settings).
- No fake production statistics anywhere.

## Architecture
- **Backend** (FastAPI + MongoDB): `deps.py`, `ai_service.py`, `storage_service.py`, `routes_auth.py`, `routes_hr.py`, `server.py`.
- **Frontend** (React 19 + Tailwind + Shadcn): pages/Landing, Auth, DashboardLayout, Overview, Jobs, JDGenerator, Candidates, ResumeAnalyzer, InterviewAssistant, Settings, ComingSoon.
- Fonts: Outfit (display), Manrope (body), IBM Plex Mono (data). Dark luxury slate-950 theme.

## Implemented (V1 + V2, updated 2026-02)
### V1 — Recruitment
- Auth: /api/auth/signup, /login, /logout, /me. Bcrypt hashing. Cookie + Bearer. Login brute-force lockout (5 fails / 15 min → 429).
- Org & team: /api/org (GET/PATCH), /api/org/team (GET/POST). Admin-only member add.
- Jobs CRUD: /api/jobs with RBAC + org scope.
- JD Generator: /api/jd/generate (Claude Sonnet 5), /api/jd (save + versioning), /api/jd/{id}, /api/jd/refine. Core JD fields always populated; optional sections gated by include_sections.
- Resume Analyzer: /api/resumes/upload (Emergent Object Storage, text extract), /api/resumes/analyze (weighted heuristic + candidate creation).
- Candidates: /api/candidates CRUD + stage transitions. Filter by job.
- Interview Assistant: /api/interviews/generate, list/get, /api/interviews/{id}/scorecard.
- Dashboard overview: /api/overview.
- Demo data: /api/demo/seed, /api/demo/clear.

### V2 — Talent Development + Comparison
- Global competency library (50+ defaults) + org-custom: /api/competencies (GET/POST).
- Frameworks CRUD + clone: /api/frameworks; POST /api/frameworks/{id}/clone with `bump_required_level` (±2) for next-seniority variants.
- Employees CRUD + bulk import: /api/employees, POST /api/employees/bulk (up to 500 rows).
- Employee ↔ Framework mapping: /api/employee-mappings (upsert; `$setOnInsert` preserves original author).
- Skill Gap Analyzer: POST /api/skill-gaps/analyze — readiness_pct + prioritized gap items.
- Development Plans: /api/development-plans (validates employee_id + framework_id).
- Candidate Comparison: POST /api/candidates/compare (2-6, dedups, union skill matrix).
- Career Path: GET /api/employees/{id}/career-path.
- Learning Recommendations: POST /api/learning/generate (Claude Sonnet 5). GET /api/learning list.
- Progress Tracking: PATCH /api/learning/{id}/activity {rec_index, act_index, status: todo|in_progress|done}. POST /api/learning/{id}/level-up {competency, new_level 1-5} — bumps `current_level` in employee_mappings so readiness score moves live across Career Path + Skill Gap.
- AI Career Coach: POST /api/coach/chat (Claude Sonnet 5 conversational reply), POST /api/coach/plan (structured 90-day plan: north_star, focus_areas, weeks_1_4/5_8/9_12 with theme + activities + milestones, success_metrics, check_ins). GET /api/coach/plans list.
- Frontend: Competency Mapping (Bulk Import CSV + Clone-for-next-level), Skill Gap Analyzer, Career Path, Learning Recs (activity status cycling + per-competency progress bar + "Confirm level up" once 100%), **Career Coach** (chat panel with Draft-90-day-plan button + 3-phase plan renderer). Candidates page multi-select comparison.

## Prioritized backlog
- P1 — JD version restore UI + version history diff.
- P1 — Resume download endpoint.
- P1 — RBAC perms on talent writes (currently any authenticated org member).
- P2 — Global search, notifications, /dashboard/documents.
- P2 — Password reset flow.
- P2 — Cascade delete for frameworks (mappings + dev plans referencing them).
- P2 — Attach learning plans directly to development plans (currently separate but linked via optional development_plan_id).

## Known limitations
- Resume download endpoint not exposed to frontend (files stored securely; view not yet built).
- No password reset flow (V1 admin-seeded / signup only).
- Interview kit questions are AI-generated; regenerate-single-question UI deferred.

## Seed credentials
- Admin: `ayushchaturvedi205@gmail.com` / `HrCopilot@2026` — Organization Admin of "HR Copilot Demo Org".
