from fastapi import APIRouter

from dgov.api.deps import GovernanceWriterDep, SessionDep
from dgov.services import modeling

router = APIRouter(prefix="/ontology-model", tags=["ontology-model"])


@router.get("")
async def view(session: SessionDep) -> dict:
    return await modeling.ontology_view(session)


@router.post("/rebuild")
async def rebuild(session: SessionDep, manager: GovernanceWriterDep) -> dict:
    result = await modeling.rebuild_ontology(session)
    await session.commit()
    return result
