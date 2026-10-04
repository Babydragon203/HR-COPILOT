# HR Copilot AI — Tools & Technology Report

## 1. Frontend Stack
| Layer | Technology | Version | Purpose |
|---|---|---|---|
| Framework | React | 19.0.0 | Single-page application shell |
| Build | Create React App + CRACO | react-scripts 5.0.1, @craco/craco 7.1.0 | Build/dev tooling with custom config |
| Routing | react-router-dom | 7.15.0 | Client-side routing (public + `/dashboard/*` protected routes) |
| Styling | Tailwind CSS | 3.4.17 | Utility-first styling |
| Design system | Shadcn UI (Radix primitives) | Radix 1.x/2.x | Accessible dropdowns, dialogs, popovers, forms |
| Icons | lucide-react | 0.516.0 | All icons (no emoji) |
| Fonts | Google Fonts (self-served) | — | Outfit (display), Manrope (body), IBM Plex Mono (data) |
| Toasts | sonner | 2.0.3 | Bottom-right dark-themed notifications |
| Charts | recharts | 3.6.0 | Talent readiness bars (progressive; ready for graphs) |
| HTTP | axios | 1.18.0 | REST client with Bearer + `withCredentials` |
| State | React hooks + @tanstack/react-query | 5.56.2 | Data fetching, form state |
| Forms | react-hook-form + zod + @hookform/resolvers | 7.56 / 3.24 / 5.0 | Client-side validation |
| Motion | framer-motion | 11.18.0 | Subtle transitions (available; not overused) |
| Package manager | yarn | 1.22.22 | Deterministic installs |

**Theme:** Dark Luxury Enterprise — slate-950 base, indigo/violet/cyan accents, glass panels with subtle white/10 borders.

---

## 2. Backend Stack
| Layer | Technology | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11+ | Backend runtime |
| Framework | FastAPI | 0.110.1 | Async REST API |
| ASGI server | Uvicorn (via supervisor) | 0.25.0 | Managed by supervisor at 0.0.0.0:8001 |
| Database | MongoDB (via motor) | motor 3.3.1 | Multi-tenant document store |
| Validation | Pydantic v2 | ≥2.6.4 | Request/response schemas |
| Auth | PyJWT + bcrypt | 2.13 / 4.1.3 | JWT (Bearer + httpOnly cookie), password hashing |
| CORS | Starlette CORSMiddleware | — | `allow_origin_regex` compatible with credentials |
| File uploads | python-multipart | ≥0.0.9 | Multipart resume uploads |
| PDF text | pypdf | ≥4.0 | Resume text extraction (.pdf) |
| DOCX text | python-docx | ≥1.0 | Resume text extraction (.docx) |
| AI client | emergentintegrations | 0.2.0 | Unified access to Claude Sonnet 5 |
| Env config | python-dotenv | ≥1.0 | Loads `/app/backend/.env` first |
| Env vars | `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `EMERGENT_LLM_KEY`, `INTEGRATION_PROXY_URL`, `APP_NAME`, `CORS_ORIGINS` | — | All secrets in env (no hardcoding) |

---

## 3. AI & External Services
| Service | Where used | How |
|---|---|---|
| **Claude Sonnet 5** (Anthropic, via Emergent Universal Key) | JD Generator, Resume Analyzer, Interview Assistant, Learning Recommendations, AI Career Coach chat + 90-day plan | `LlmChat(...).with_model("anthropic","claude-sonnet-5")`; strict JSON output validated via `_extract_json` |
| **Emergent Object Storage** (S3-compatible) | Resume file storage | `/objstore/api/v1/storage` — org-scoped path `hr-copilot/orgs/{org_id}/resumes/{uuid}.{ext}` |

**AI oversight:** every AI-generated screen displays *"AI-generated content. Review before making HR decisions."* Resume score is labeled *"Internal Job Match Score · heuristic only, not a hiring decision."*

---

## 4. Feature ↔ Technology Matrix

### V1 — Recruitment
| Feature | Backend endpoints | Frontend page | AI/services |
|---|---|---|---|
| Auth / signup / login / me | `/api/auth/{signup,login,logout,me}` — bcrypt + JWT + brute-force lockout (5/15min → 429) | `/login`, `/signup` (`Auth.jsx`) | — |
| Organization + Team + RBAC | `/api/org`, `/api/org/team` — 5 roles: org_admin, hr_manager, recruiter, hiring_manager, viewer | `Settings.jsx` | — |
| Jobs CRUD | `/api/jobs` | `Jobs.jsx` | — |
| **JD Generator** | `/api/jd/generate` + `/api/jd` (versioned) + `/api/jd/refine` | `JDGenerator.jsx` | Claude Sonnet 5 |
| **Resume Analyzer** | `/api/resumes/upload` + `/api/resumes/analyze` (weighted heuristic) | `ResumeAnalyzer.jsx` | Emergent Object Storage + pypdf/python-docx + Claude Sonnet 5 |
| Candidates + pipeline | `/api/candidates` (9 stages) | `Candidates.jsx` | — |
| **Interview Assistant** | `/api/interviews/generate`, `/api/interviews/{id}/scorecard` | `InterviewAssistant.jsx` | Claude Sonnet 5 |
| Dashboard overview | `/api/overview` — real counts + pipeline + activity | `Overview.jsx` | — |
| Demo Data | `/api/demo/{seed,clear}` (records tagged `is_demo:true`) | Settings | — |

### V2 — Talent Development + Comparison
| Feature | Backend endpoints | Frontend page | AI/services |
|---|---|---|---|
| **Competency Library** (50+ defaults + org custom) | `/api/competencies` | `CompetencyMapping.jsx` | — |
| **Frameworks + Cloning** | `/api/frameworks`, `POST /api/frameworks/{id}/clone` (`bump_required_level` ±2) | `CompetencyMapping.jsx` | — |
| **Employees + Bulk CSV Import** | `/api/employees`, `POST /api/employees/bulk` (≤500 rows) | `CompetencyMapping.jsx` (Employees tab) | — |
| **Employee ↔ Framework mapping** (current vs target) | `/api/employee-mappings` (upsert; `$setOnInsert` preserves author) | `CompetencyMapping.jsx` (Map tab) | — |
| **Skill Gap Analyzer** | `/api/skill-gaps/analyze` — readiness % + priority (Critical → High → Medium → Low → Met → Not Assessed) | `SkillGapAnalyzer.jsx` | — |
| Development Plans | `/api/development-plans` (validates employee + framework) | `SkillGapAnalyzer.jsx` | — |
| **Candidate Comparison** | `POST /api/candidates/compare` (2–6, dedups, union skill matrix) | `Candidates.jsx` (ComparisonPanel) | — |
| **Career Path** | `GET /api/employees/{id}/career-path` — readiness across every framework, ordered role_match → dept_match → readiness | `CareerPath.jsx` | — |
| **Learning Recommendations** | `POST /api/learning/generate`, `GET /api/learning` | `Learning.jsx` | Claude Sonnet 5 (no fake URLs/providers) |
| **Progress Tracking** | `PATCH /api/learning/{id}/activity` (todo/in_progress/done) + `POST /api/learning/{id}/level-up` (bumps `current_level` → readiness moves live) | `Learning.jsx` | — |
| **AI Career Coach** | `POST /api/coach/chat` + `POST /api/coach/plan` (structured 90-day plan) + `GET /api/coach/plans` | `CareerCoach.jsx` | Claude Sonnet 5 |
| Audit logging | `db.audit_logs` — every write recorded org-scoped | — | — |

---

## 5. Security Posture
- Multi-tenant isolation — **every** business collection is `org_id`-scoped and cross-org access returns 404, not empty.
- Passwords: bcrypt (12-round salt).
- JWT: HS256, 7-day expiry, delivered via httpOnly `Secure` `SameSite=None` cookie + `Authorization: Bearer` header fallback.
- Brute-force: 5 failed logins per `ip:email` within 15 min → 429.
- File uploads: extension whitelist (pdf/docx/txt), 10 MB cap, MIME type stored, executed content not opened.
- CORS: `allow_credentials=True` with `allow_origin_regex` (echoes the actual Origin — no wildcard when credentials are used).
- Env-only secrets: `JWT_SECRET`, `EMERGENT_LLM_KEY`, `MONGO_URL`, `ADMIN_PASSWORD` — nothing hardcoded, nothing in frontend.
- AI: keys server-side only; frontend never touches Anthropic/Emergent proxy directly.

---

## 6. Deployment & Runtime
- Managed by **supervisor** (`sudo supervisorctl status`): `backend` on `0.0.0.0:8001`, `frontend` on `3000`.
- Frontend uses `REACT_APP_BACKEND_URL` for every API call.
- All backend routes are prefixed with `/api` (Kubernetes ingress routes `/api/*` → 8001).
- MongoDB indexes created on startup: unique `users.email`, unique ids on every domain collection, TTL-ready `login_attempts`.
- Admin seeded on startup: `ayushchaturvedi205@gmail.com` (from `ADMIN_EMAIL`).
- Startup best-effort initializes Emergent Object Storage session key.
