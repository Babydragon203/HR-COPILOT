from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

import os
import logging
from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware

from deps import db, hash_password, verify_password, now_iso, new_id
from routes_auth import router as auth_router, org_router
from routes_hr import router as hr_router, demo_router
from routes_talent import router as talent_router
from storage_service import init_storage

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="HR Copilot AI API")
api_router = APIRouter(prefix="/api")

@api_router.get("/")
async def root():
    return {"service": "HR Copilot AI", "status": "ok"}


api_router.include_router(auth_router)
api_router.include_router(org_router)
api_router.include_router(hr_router)
api_router.include_router(talent_router)
api_router.include_router(demo_router)
app.include_router(api_router)

_cors_env = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip() and o.strip() != "*"]
_cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if _cors_origins:
    _cors_kwargs["allow_origins"] = _cors_origins
else:
    # When no explicit origins are configured, allow any origin via regex (compatible with credentials).
    _cors_kwargs["allow_origin_regex"] = ".*"
app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.on_event("startup")
async def startup():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.organizations.create_index("id", unique=True)
    await db.jobs.create_index([("org_id", 1), ("created_at", -1)])
    await db.jobs.create_index("id", unique=True)
    await db.job_descriptions.create_index("id", unique=True)
    await db.jd_versions.create_index([("jd_id", 1), ("version", -1)])
    await db.candidates.create_index([("org_id", 1), ("created_at", -1)])
    await db.candidates.create_index("id", unique=True)
    await db.candidate_documents.create_index("id", unique=True)
    await db.interviews.create_index("id", unique=True)
    await db.audit_logs.create_index([("org_id", 1), ("created_at", -1)])
    await db.login_attempts.create_index([("key", 1), ("at", -1)])
    await db.frameworks.create_index("id", unique=True)
    await db.frameworks.create_index([("org_id", 1), ("created_at", -1)])
    await db.employees.create_index("id", unique=True)
    await db.employees.create_index([("org_id", 1), ("created_at", -1)])
    await db.employee_mappings.create_index([("org_id", 1), ("employee_id", 1), ("framework_id", 1)], unique=True)
    await db.development_plans.create_index("id", unique=True)
    await db.competencies.create_index([("org_id", 1), ("name", 1)])

    # Seed admin + default organization
    admin_email = os.environ["ADMIN_EMAIL"].lower()
    admin_password = os.environ["ADMIN_PASSWORD"]
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        org_id = new_id()
        await db.organizations.insert_one({
            "id": org_id, "name": "HR Copilot Demo Org",
            "industry": "Technology", "company_size": "Startup",
            "created_at": now_iso(), "demo_data_loaded": False,
        })
        await db.users.insert_one({
            "id": new_id(), "email": admin_email,
            "password_hash": hash_password(admin_password),
            "full_name": "Ayush Chaturvedi", "role": "org_admin",
            "org_id": org_id, "created_at": now_iso(),
        })
        logger.info(f"Seeded admin user: {admin_email}")
    else:
        if not verify_password(admin_password, existing["password_hash"]):
            await db.users.update_one(
                {"email": admin_email},
                {"$set": {"password_hash": hash_password(admin_password)}},
            )
            logger.info("Admin password updated")

    # Storage init (best-effort)
    try:
        init_storage()
    except Exception as e:
        logger.warning(f"Storage init deferred: {e}")


@app.on_event("shutdown")
async def shutdown():
    pass
