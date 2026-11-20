import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dgov.api.v1.endpoints import auth, health
from dgov.api.v1.router import api_router
from dgov.core.config import settings
from dgov.core.db import SessionFactory, dispose_db, init_db
from dgov.core.es import close_es, ensure_semantic_index
from dgov.core.exceptions import AppError
from dgov.core.graph import close_graph, ensure_graph_schema
from dgov.services.auth import seed_default_users
from dgov.services.governance import seed_demo_data

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _best_effort(name: str, step) -> None:
    try:
        await step()
    except Exception as exc:  # allow the API to boot while infrastructure is still starting
        logger.warning("%s initialization skipped: %s", name, exc)


async def _seed_governance() -> None:
    async with SessionFactory() as session:
        await seed_default_users(session)
        if settings.seed_demo_data:
            await seed_demo_data(session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _best_effort("mysql", init_db)
    await _best_effort("governance seed", _seed_governance)
    await _best_effort("elasticsearch", ensure_semantic_index)
    await _best_effort("neo4j", ensure_graph_schema)
    yield
    await close_es()
    await close_graph()
    await dispose_db()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Metadata catalog, semantic layer, ontology graph and data quality APIs.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/health",
        "api": settings.api_v1_prefix,
    }


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(api_router, prefix=settings.api_v1_prefix)
