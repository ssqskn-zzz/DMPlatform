import logging

from neo4j import AsyncDriver, AsyncGraphDatabase

from dgov.core.config import settings

logger = logging.getLogger(__name__)

_driver: AsyncDriver | None = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=settings.neo4j_connection_timeout,
            max_connection_lifetime=3600,
        )
    return _driver


async def close_graph() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def ensure_graph_schema() -> None:
    """Create the constraints/indexes used by the ontology graph."""
    statements = [
        "CREATE CONSTRAINT ontology_node_uid IF NOT EXISTS "
        "FOR (n:OntologyNode) REQUIRE n.uid IS UNIQUE",
        "CREATE INDEX ontology_node_name IF NOT EXISTS FOR (n:OntologyNode) ON (n.name)",
        "CREATE INDEX ontology_node_type IF NOT EXISTS FOR (n:OntologyNode) ON (n.node_type)",
        "CREATE CONSTRAINT ontology_edge_uid IF NOT EXISTS "
        "FOR ()-[r:ONTOLOGY_EDGE]-() REQUIRE r.uid IS UNIQUE",
    ]
    async with get_driver().session(database=settings.neo4j_database) as session:
        for statement in statements:
            await (await session.run(statement)).consume()
    logger.info("ontology graph constraints are ready")
