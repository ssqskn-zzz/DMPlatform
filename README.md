# DMPlatform · 数据治理工作台

Python 数据治理工作台骨架，覆盖数据源、元数据目录、数据标准、语义层、本体图谱与数据质量。

## 技术栈

| 能力 | 选型 |
| --- | --- |
| Web 框架 | FastAPI + Uvicorn |
| 元数据/治理主库 | MySQL 8（SQLAlchemy 2.0 async + Alembic） |
| 语义层向量检索 | Elasticsearch 8 `dense_vector`（cosine，支持外部 embedding 写入与 kNN 召回） |
| 本体图谱 | Neo4j 5 Community（标签属性图，节点 + 关系 + 邻域检索） |

## 快速开始

```bash
# 1. 启动基础设施
docker compose up -d

# 2. 安装依赖
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"

# 3. 配置
cp .env.example .env

# 4. 启动 API（启动时会自动建表、创建 ES 索引与 Neo4j 约束）
uvicorn dgov.main:app --reload
```

- API 文档：http://localhost:8000/docs
- 健康检查：`GET /health`、`GET /health/infra`
- Neo4j Browser：http://localhost:7474

## 目录结构

```text
src/dgov/
├── main.py              # FastAPI 应用与生命周期
├── api/v1/endpoints/    # 按领域拆分的路由
├── services/            # 领域服务（MySQL / ES / Neo4j 编排）
├── models/              # SQLAlchemy ORM 模型
├── schemas/             # Pydantic 请求/响应模型
└── core/                # 配置、MySQL/ES/Neo4j 客户端、异常
migrations/              # Alembic 迁移
scripts/init_infra.py    # 手动初始化表、索引、图约束
tests/                   # 冒烟测试
```

## 核心 API

| 领域 | 接口 |
| --- | --- |
| 数据源 | `GET/POST /api/v1/datasources`、`GET/PATCH/DELETE /api/v1/datasources/{id}` |
| 元数据目录 | `GET/POST /api/v1/datasets`、`/datasets/{id}/columns`、`/columns/{id}` |
| 数据标准 | `GET/POST /api/v1/standards` |
| 语义层 | `GET/POST /api/v1/semantic/assets` |
| 向量写入 | `PUT /api/v1/semantic/assets/{id}/embedding` |
| 向量召回 | `POST /api/v1/semantic/search` |
| 本体图谱 | `GET/POST /api/v1/ontology/nodes`、`POST /api/v1/ontology/relations`、`GET /api/v1/ontology/nodes/{uid}/neighborhood` |
| 数据质量 | `GET/POST /api/v1/quality/rules`、`/rules/{id}/results` |

### 向量写入与召回

向量由外部 embedding 服务计算，工作台只负责存储与召回；`EMBEDDING_DIM` 必须与外部模型维度一致。

```bash
curl -X PUT http://localhost:8000/api/v1/semantic/assets/1/embedding \
  -H "Content-Type: application/json" \
  -d '{"vector": [0.1, 0.2, ...], "model": "bge-large-zh"}'

curl -X POST http://localhost:8000/api/v1/semantic/search \
  -H "Content-Type: application/json" \
  -d '{"vector": [0.1, 0.2, ...], "top_k": 5, "asset_type": "metric"}'
```

## 数据库迁移

开发环境 `AUTO_CREATE_TABLES=true` 会直接建表；生产环境应关闭并使用 Alembic：

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

## 后续可扩展

- 数据质量规则执行器与调度（`quality_result` 已预留）
- 数据血缘采集（`lineage_edge` 已预留）与影响分析
- 语义层与本体节点的映射、权限与审批流
