"""Initialize MySQL tables, the Elasticsearch semantic index and Neo4j constraints."""

import asyncio
import logging

from dgov.core.config import settings
from dgov.core.db import dispose_db, init_db
from dgov.core.es import close_es, ensure_semantic_index
from dgov.core.graph import close_graph, ensure_graph_schema


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

    await init_db()
    print("MySQL schema ready")
    await ensure_semantic_index()
    print(f"Elasticsearch index ready: {settings.es_semantic_index} (dims={settings.embedding_dim})")
    await ensure_graph_schema()
    print("Neo4j constraints ready")

    await close_es()
    await close_graph()
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
