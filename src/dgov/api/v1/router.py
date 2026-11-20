from fastapi import APIRouter

from dgov.api.v1.endpoints import (
    catalog,
    datasources,
    governance,
    ontology,
    quality,
    semantic,
    standards,
)

api_router = APIRouter()
api_router.include_router(datasources.router)
api_router.include_router(catalog.router)
api_router.include_router(standards.router)
api_router.include_router(semantic.router)
api_router.include_router(quality.router)
api_router.include_router(ontology.router)
api_router.include_router(governance.router)
