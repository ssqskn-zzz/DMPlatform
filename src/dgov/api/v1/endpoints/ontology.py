from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from dgov.api.deps import GraphDep
from dgov.schemas import (
    OntologyGraph,
    OntologyNode,
    OntologyNodeCreate,
    OntologyNodeUpdate,
    OntologyRelation,
    OntologyRelationCreate,
)
from dgov.services import ontology as service

router = APIRouter(prefix="/ontology", tags=["ontology"])


@router.get("/nodes", response_model=list[OntologyNode])
async def list_nodes(
    driver: GraphDep,
    node_type: str | None = None,
    keyword: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[OntologyNode]:
    return await service.list_nodes(driver, node_type, keyword, limit)


@router.post("/nodes", response_model=OntologyNode, status_code=status.HTTP_201_CREATED)
async def create_node(payload: OntologyNodeCreate, driver: GraphDep) -> OntologyNode:
    return await service.create_node(driver, payload)


@router.get("/nodes/{uid}", response_model=OntologyNode)
async def get_node(uid: str, driver: GraphDep) -> OntologyNode:
    return await service.get_node(driver, uid)


@router.patch("/nodes/{uid}", response_model=OntologyNode)
async def update_node(uid: str, payload: OntologyNodeUpdate, driver: GraphDep) -> OntologyNode:
    return await service.update_node(
        driver,
        uid,
        name=payload.name,
        description=payload.description,
        properties=payload.properties,
    )


@router.delete("/nodes/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(uid: str, driver: GraphDep) -> Response:
    await service.delete_node(driver, uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/nodes/{uid}/neighborhood", response_model=OntologyGraph)
async def get_neighborhood(
    uid: str, driver: GraphDep, depth: Annotated[int, Query(ge=1, le=5)] = 2
) -> OntologyGraph:
    return await service.get_neighborhood(driver, uid, depth)


@router.post(
    "/relations", response_model=OntologyRelation, status_code=status.HTTP_201_CREATED
)
async def create_relation(
    payload: OntologyRelationCreate, driver: GraphDep
) -> OntologyRelation:
    return await service.create_relation(driver, payload)


@router.delete("/relations/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_relation(uid: str, driver: GraphDep) -> Response:
    await service.delete_relation(driver, uid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
