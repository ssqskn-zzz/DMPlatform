from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Data Governance Workbench"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "*"

    # MySQL: governance metadata, catalog and semantic definitions
    mysql_dsn: str = "mysql+aiomysql://dgov:dgov@localhost:3306/dgov?charset=utf8mb4"
    db_echo: bool = False
    auto_create_tables: bool = True

    # Elasticsearch: semantic layer vector index
    es_url: str = "http://localhost:9200"
    es_username: str | None = None
    es_password: str | None = None
    es_request_timeout: float = 10.0
    es_semantic_index: str = "dgov_semantic_assets"
    embedding_dim: int = 1024

    # Neo4j: ontology graph
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "dgov_password"
    neo4j_database: str = "neo4j"
    neo4j_connection_timeout: float = 5.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
