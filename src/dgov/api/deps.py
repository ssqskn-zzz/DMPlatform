from typing import Annotated

from elasticsearch import AsyncElasticsearch
from fastapi import Depends
from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession

from dgov.core.db import get_session
from dgov.core.es import get_es
from dgov.core.graph import get_driver

SessionDep = Annotated[AsyncSession, Depends(get_session)]
EsDep = Annotated[AsyncElasticsearch, Depends(get_es)]
GraphDep = Annotated[AsyncDriver, Depends(get_driver)]
