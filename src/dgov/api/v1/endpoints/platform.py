from fastapi import APIRouter

from dgov.api.deps import CurrentUserDep, SessionDep
from dgov.core.modules import MODULE_GROUPS, module_permission
from dgov.services import authz

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/modules")
async def list_modules(session: SessionDep, user: CurrentUserDep) -> list[dict]:
    granted = set(await authz.effective_permissions(session, user))
    groups: list[dict] = []
    for group in MODULE_GROUPS:
        items = [
            item for item in group["items"] if module_permission(item["id"]) in granted
        ]
        if items:
            groups.append({**group, "items": items})
    return groups
