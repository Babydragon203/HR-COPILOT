"""Cleanup helper: removes TEST_ artifacts created by the backend test suite."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main():
    cl = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cl[os.environ["DB_NAME"]]
    test_orgs = [o["id"] async for o in db.organizations.find({"name": {"$regex": "^TEST_"}}, {"id": 1})]
    counts = {}
    for coll in ["jobs", "candidates", "job_descriptions", "jd_versions",
                 "interviews", "candidate_documents", "audit_logs", "users",
                 "frameworks", "employees", "employee_mappings", "development_plans",
                 "learning_recommendations", "growth_plans", "competencies"]:
        r = await db[coll].delete_many({"org_id": {"$in": test_orgs}})
        counts[coll] = r.deleted_count
    counts["organizations"] = (await db.organizations.delete_many({"id": {"$in": test_orgs}})).deleted_count
    counts["test_users_other_orgs"] = (await db.users.delete_many({"email": {"$regex": "^test_"}})).deleted_count
    # TEST_ / QAUI_ artifacts inside the seeded admin org
    NAMED = "^(TEST_|QAUI )"
    counts["seed_org_jobs"] = (await db.jobs.delete_many({"title": {"$regex": "^TEST_"}})).deleted_count
    counts["seed_org_jds"] = (await db.job_descriptions.delete_many({"title": {"$regex": "^TEST_"}})).deleted_count
    counts["seed_org_cands"] = (await db.candidates.delete_many({"name": {"$regex": "^TEST_"}})).deleted_count
    counts["seed_org_frameworks"] = (await db.frameworks.delete_many({"name": {"$regex": NAMED}})).deleted_count
    # learning/growth plans + mappings for named test employees, then the employees themselves
    named_emps = [e["id"] async for e in db.employees.find({"name": {"$regex": NAMED}}, {"id": 1})]
    for coll in ["employee_mappings", "development_plans", "learning_recommendations", "growth_plans"]:
        counts[f"seed_org_{coll}"] = (await db[coll].delete_many(
            {"employee_id": {"$in": named_emps}})).deleted_count
    counts["seed_org_employees"] = (await db.employees.delete_many(
        {"id": {"$in": named_emps}})).deleted_count
    counts["login_attempts"] = (await db.login_attempts.delete_many({})).deleted_count
    print(f"test orgs removed: {len(test_orgs)}")
    print(counts)
    cl.close()


asyncio.run(main())
