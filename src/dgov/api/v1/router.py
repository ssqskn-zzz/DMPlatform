from fastapi import APIRouter, Depends

from dgov.api.deps import get_current_user
from dgov.api.v1.endpoints import (
    agent_endpoints,
    assets,
    authz,
    catalog,
    collection,
    datasources,
    governance,
    lineage,
    lineage_insight,
    logs,
    ontology,
    ontology_model,
    platform,
    quality,
    quality_center,
    semantic,
    standards,
    users,
    warehouse,
)

api_router = APIRouter(dependencies=[Depends(get_current_user)])
api_router.include_router(platform.router)
api_router.include_router(users.router)
api_router.include_router(authz.router)
api_router.include_router(datasources.router)
api_router.include_router(collection.datasource_router)
api_router.include_router(collection.router)
api_router.include_router(catalog.router)
api_router.include_router(standards.router)
api_router.include_router(semantic.router)
api_router.include_router(quality.router)
api_router.include_router(quality_center.router)
api_router.include_router(ontology.router)
api_router.include_router(ontology_model.router)
api_router.include_router(governance.router)
api_router.include_router(assets.router)
api_router.include_router(lineage.router)
api_router.include_router(warehouse.router)
api_router.include_router(logs.router)
api_router.include_router(agent_endpoints.router)
api_router.include_router(lineage_insight.router)
