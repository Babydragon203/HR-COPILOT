"""Auth, RBAC, and database dependencies."""
import os
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Request, Depends
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGO = "HS256"

_client = AsyncIOMotorClient(MONGO_URL)
db = _client[DB_NAME]


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(user_id: str, email: str, org_id: str, role: str) -> str:
    payload = {
        "sub": user_id, "email": email, "org_id": org_id, "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        h = request.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            token = h[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.users.find_one({"id": payload["sub"]}, {"password_hash": 0, "_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


ROLE_PERMS = {
    "org_admin": {"*"},
    "hr_manager": {"jobs.*", "candidates.*", "interviews.*", "jds.*", "org.read", "team.read"},
    "recruiter": {"jobs.*", "candidates.*", "interviews.*", "jds.*"},
    "hiring_manager": {"jobs.read", "candidates.read", "interviews.read", "interviews.evaluate", "jds.read"},
    "viewer": {"jobs.read", "candidates.read", "interviews.read", "jds.read"},
}


def has_perm(role: str, perm: str) -> bool:
    perms = ROLE_PERMS.get(role, set())
    if "*" in perms:
        return True
    resource, action = perm.split(".")
    return perm in perms or f"{resource}.*" in perms


def require_perm(perm: str):
    async def _dep(user: dict = Depends(get_current_user)):
        if not has_perm(user["role"], perm):
            raise HTTPException(status_code=403, detail=f"Insufficient permissions: {perm}")
        return user
    return _dep


async def audit(user: dict, action: str, resource: str, resource_id: str = None, meta: dict = None):
    await db.audit_logs.insert_one({
        "id": str(uuid.uuid4()),
        "org_id": user["org_id"],
        "user_id": user["id"],
        "user_email": user["email"],
        "action": action,
        "resource": resource,
        "resource_id": resource_id,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())
