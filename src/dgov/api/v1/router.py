from fastapi import APIRouter, Depends

from dgov.api.deps import get_current_user
from dgov.api.v1.endpoints import (
    catalog,
    datasources,
    governance,
    ontology,
    platform,
    quality,
    semantic,
    standards,
    users,
)

api_router = APIRouter(dependencies=[Depends(get_current_user)])
api_router.include_router(platform.router)
api_router.include_router(users.router)
api_router.include_router(datasources.router)
api_router.include_router(catalog.router)
api_router.include_router(standards.router)
api_router.include_router(semantic.router)
api_router.include_router(quality.router)
api_router.include_router(ontology.router)
api_router.include_router(governance.router)
