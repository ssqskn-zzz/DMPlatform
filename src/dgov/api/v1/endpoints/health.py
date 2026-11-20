from fastapi import APIRouter
from sqlalchemy import text

from dgov.api.deps import EsDep, GraphDep, SessionDep
from dgov.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@router.get("/health/infra")
async def infra_health(session: SessionDep, es: EsDep, driver: GraphDep) -> dict:
    dependencies: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        dependencies["mysql"] = "ok"
    except Exception as exc:
        dependencies["mysql"] = f"error: {exc}"

    try:
        dependencies["elasticsearch"] = "ok" if await es.ping() else "unavailable"
    except Exception as exc:
        dependencies["elasticsearch"] = f"error: {exc}"

    try:
        await driver.verify_connectivity()
        dependencies["neo4j"] = "ok"
    except Exception as exc:
        dependencies["neo4j"] = f"error: {exc}"

    status = "ok" if all(value == "ok" for value in dependencies.values()) else "degraded"
    return {"status": status, "dependencies": dependencies}
