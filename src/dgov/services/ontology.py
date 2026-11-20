import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from neo4j import AsyncDriver

from dgov.core.config import settings
from dgov.core.exceptions import BadRequestError, NotFoundError
from dgov.schemas.ontology import OntologyNodeCreate, OntologyRelationCreate


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _node_to_dict(node: Any) -> dict:
    data = dict(node)
    raw = data.pop("properties_json", None)
    data["properties"] = json.loads(raw) if raw else {}
    return data


async def create_node(driver: AsyncDriver, payload: OntologyNodeCreate) -> dict:
    uid = uuid4().hex
    now = _now()
    query = """
    CREATE (n:OntologyNode {
        uid: $uid,
        name: $name,
        node_type: $node_type,
        description: $description,
        properties_json: $properties_json,
        created_at: $now,
        updated_at: $now
    })
    RETURN n
    """
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            query,
            uid=uid,
            name=payload.name,
            node_type=payload.node_type,
            description=payload.description,
            properties_json=json.dumps(payload.properties, ensure_ascii=False),
            now=now,
        )
        record = await result.single()
    return _node_to_dict(record["n"])


async def list_nodes(
    driver: AsyncDriver, node_type: str | None = None, keyword: str | None = None, limit: int = 100
) -> list[dict]:
    query = """
    MATCH (n:OntologyNode)
    WHERE ($node_type IS NULL OR n.node_type = $node_type)
      AND ($keyword IS NULL OR n.name CONTAINS $keyword)
    RETURN n
    ORDER BY n.name
    LIMIT $limit
    """
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(query, node_type=node_type, keyword=keyword, limit=limit)
        records = [record async for record in result]
    return [_node_to_dict(record["n"]) for record in records]


async def get_node(driver: AsyncDriver, uid: str) -> dict:
    query = "MATCH (n:OntologyNode {uid: $uid}) RETURN n"
    async with driver.session(database=settings.neo4j_database) as session:
        record = await (await session.run(query, uid=uid)).single()
    if record is None:
        raise NotFoundError(f"ontology node {uid} not found")
    return _node_to_dict(record["n"])


async def update_node(
    driver: AsyncDriver, uid: str, *, name: str | None, description: str | None, properties: dict | None
) -> dict:
    query = """
    MATCH (n:OntologyNode {uid: $uid})
    SET n.name = coalesce($name, n.name),
        n.description = coalesce($description, n.description),
        n.properties_json = coalesce($properties_json, n.properties_json),
        n.updated_at = $now
    RETURN n
    """
    async with driver.session(database=settings.neo4j_database) as session:
        properties_json = (
            json.dumps(properties, ensure_ascii=False) if properties is not None else None
        )
        record = await (
            await session.run(
                query,
                uid=uid,
                name=name,
                description=description,
                properties_json=properties_json,
                now=_now(),
            )
        ).single()
    if record is None:
        raise NotFoundError(f"ontology node {uid} not found")
    return _node_to_dict(record["n"])


async def delete_node(driver: AsyncDriver, uid: str) -> None:
    query = "MATCH (n:OntologyNode {uid: $uid}) DETACH DELETE n"
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(query, uid=uid)
        summary = await result.consume()
    if summary.counters.nodes_deleted == 0:
        raise NotFoundError(f"ontology node {uid} not found")


async def create_relation(driver: AsyncDriver, payload: OntologyRelationCreate) -> dict:
    if payload.source_uid == payload.target_uid:
        raise BadRequestError("source and target node must be different")
    uid = uuid4().hex
    query = """
    MATCH (a:OntologyNode {uid: $source_uid})
    MATCH (b:OntologyNode {uid: $target_uid})
    CREATE (a)-[r:ONTOLOGY_EDGE {
        uid: $uid,
        relation_type: $relation_type,
        description: $description,
        created_at: $now
    }]->(b)
    RETURN r, a.uid AS source_uid, b.uid AS target_uid
    """
    async with driver.session(database=settings.neo4j_database) as session:
        record = await (
            await session.run(
                query,
                source_uid=payload.source_uid,
                target_uid=payload.target_uid,
                uid=uid,
                relation_type=payload.relation_type,
                description=payload.description,
                now=_now(),
            )
        ).single()
    if record is None:
        raise NotFoundError("source or target ontology node not found")
    data = dict(record["r"])
    data["source_uid"] = record["source_uid"]
    data["target_uid"] = record["target_uid"]
    return data


async def delete_relation(driver: AsyncDriver, uid: str) -> None:
    query = "MATCH ()-[r:ONTOLOGY_EDGE {uid: $uid}]->() DELETE r"
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(query, uid=uid)
        summary = await result.consume()
    if summary.counters.relationships_deleted == 0:
        raise NotFoundError(f"ontology relation {uid} not found")


async def get_neighborhood(driver: AsyncDriver, uid: str, depth: int = 2) -> dict:
    depth = max(1, min(depth, 5))
    query = f"""
    MATCH (center:OntologyNode {{uid: $uid}})
    OPTIONAL MATCH path = (center)-[:ONTOLOGY_EDGE*1..{depth}]-(m:OntologyNode)
    WITH center, collect(DISTINCT m) AS neighbors, collect(relationships(path)) AS relation_groups
    RETURN center, neighbors, relation_groups
    """
    async with driver.session(database=settings.neo4j_database) as session:
        record = await (await session.run(query, uid=uid)).single()
    if record is None:
        raise NotFoundError(f"ontology node {uid} not found")

    nodes = {record["center"]["uid"]: _node_to_dict(record["center"])}
    for neighbor in record["neighbors"]:
        if neighbor is not None:
            nodes[neighbor["uid"]] = _node_to_dict(neighbor)

    relations = {}
    for group in record["relation_groups"]:
        for relation in group:
            if relation["uid"] not in relations:
                relations[relation["uid"]] = {
                    "uid": relation["uid"],
                    "source_uid": relation.start_node["uid"],
                    "target_uid": relation.end_node["uid"],
                    "relation_type": relation.get("relation_type"),
                    "description": relation.get("description"),
                    "created_at": relation.get("created_at"),
                }
    return {"nodes": list(nodes.values()), "relations": list(relations.values())}
