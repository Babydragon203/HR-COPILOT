"""Authentication + Organization routes."""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, Response, Request
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from deps import (
    db, hash_password, verify_password, create_access_token,
    get_current_user, audit, now_iso, new_id, require_perm,
)

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_WINDOW_MIN = 15

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    organization_name: str
    industry: Optional[str] = None
    company_size: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _set_cookies(response: Response, token: str):
    response.set_cookie(
        key="access_token", value=token, httponly=True, secure=True,
        samesite="none", max_age=7 * 24 * 3600, path="/",
    )


def _public_user(user: dict, org: dict) -> dict:
    return {
        "id": user["id"], "email": user["email"], "full_name": user["full_name"],
        "role": user["role"], "org_id": user["org_id"],
        "organization": {"id": org["id"], "name": org["name"], "industry": org.get("industry"), "size": org.get("company_size")},
    }


@router.post("/signup")
async def signup(body: SignupIn, response: Response):
    email = body.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")

    org = {
        "id": new_id(),
        "name": body.organization_name.strip(),
        "industry": body.industry,
        "company_size": body.company_size,
        "created_at": now_iso(),
        "demo_data_loaded": False,
    }
    await db.organizations.insert_one(org)

    user = {
        "id": new_id(),
        "email": email,
        "password_hash": hash_password(body.password),
        "full_name": body.full_name,
        "role": "org_admin",
        "org_id": org["id"],
        "created_at": now_iso(),
    }
    await db.users.insert_one(user)

    token = create_access_token(user["id"], user["email"], org["id"], user["role"])
    _set_cookies(response, token)
    return {"user": _public_user(user, org), "token": token}


@router.post("/login")
async def login(body: LoginIn, response: Response, request: Request):
    email = body.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{email}"
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=LOCKOUT_WINDOW_MIN)

    # Lockout check
    recent_fails = await db.login_attempts.count_documents(
        {"key": key, "success": False, "at": {"$gte": window_start.isoformat()}}
    )
    if recent_fails >= LOCKOUT_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        await db.login_attempts.insert_one({"key": key, "success": False, "at": now.isoformat()})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Clear failed attempts on success
    await db.login_attempts.delete_many({"key": key, "success": False})
    org = await db.organizations.find_one({"id": user["org_id"]}, {"_id": 0})
    if not org:
        raise HTTPException(status_code=500, detail="Organization missing")
    token = create_access_token(user["id"], user["email"], user["org_id"], user["role"])
    _set_cookies(response, token)
    return {"user": _public_user(user, org), "token": token}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    org = await db.organizations.find_one({"id": user["org_id"]}, {"_id": 0})
    return _public_user(user, org or {})


# --- Organization / team ---
org_router = APIRouter(prefix="/org", tags=["organization"])


class UpdateOrgIn(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None


@org_router.get("")
async def get_org(user: dict = Depends(get_current_user)):
    org = await db.organizations.find_one({"id": user["org_id"]}, {"_id": 0})
    return org


@org_router.patch("")
async def update_org(body: UpdateOrgIn, user: dict = Depends(require_perm("org.read"))):
    if user["role"] != "org_admin":
        raise HTTPException(status_code=403, detail="Only Org Admin can update organization")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if patch:
        await db.organizations.update_one({"id": user["org_id"]}, {"$set": patch})
    await audit(user, "org.update", "organization", user["org_id"], patch)
    org = await db.organizations.find_one({"id": user["org_id"]}, {"_id": 0})
    return org


class InviteIn(BaseModel):
    email: EmailStr
    full_name: str
    role: str  # org_admin | hr_manager | recruiter | hiring_manager | viewer
    password: str = Field(min_length=8)


@org_router.get("/team")
async def list_team(user: dict = Depends(get_current_user)):
    cur = db.users.find({"org_id": user["org_id"]}, {"_id": 0, "password_hash": 0})
    return await cur.to_list(500)


@org_router.post("/team")
async def add_member(body: InviteIn, user: dict = Depends(get_current_user)):
    if user["role"] != "org_admin":
        raise HTTPException(status_code=403, detail="Only Org Admin can add members")
    email = body.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    valid_roles = {"org_admin", "hr_manager", "recruiter", "hiring_manager", "viewer"}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail="Invalid role")
    new_user = {
        "id": new_id(),
        "email": email,
        "password_hash": hash_password(body.password),
        "full_name": body.full_name,
        "role": body.role,
        "org_id": user["org_id"],
        "created_at": now_iso(),
    }
    await db.users.insert_one(new_user)
    await audit(user, "team.add", "user", new_user["id"], {"email": email, "role": body.role})
    return {k: v for k, v in new_user.items() if k not in ("_id", "password_hash")}
