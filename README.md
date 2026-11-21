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

- 工作台：http://localhost:8000
- API 文档：http://localhost:8000/docs
- 健康检查：`GET /health`、`GET /health/infra`
- Neo4j Browser：http://localhost:7474

## 平台登录与角色

首次启动自动创建以下账号，默认密码均为 `abc123456789`：

| 用户名 | 角色 | 权限 |
| --- | --- | --- |
| `admin` | 系统管理员 | 平台访问、治理配置、用户管理 |
| `governance` | 治理专员 | 平台访问、治理与配置 |
| `viewer` | 只读用户 | 平台访问与个人密码管理 |

除 `POST /api/v1/auth/login` 和健康检查外，业务 API 均需携带 `Authorization: Bearer <token>`。
工作台已接入两大引擎、11 项应用功能和 6 项管理配置入口；除用户中心外，当前均为功能占位页。

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
| 治理工作台 | `GET /api/v1/workbench/overview`、`/todos`、`/foundation` |
| 待办与变更 | `GET/POST /api/v1/workbench/work-orders`、`POST /work-orders/{code}/complete`、`POST /change-requests/{code}/approve` |
| 语义术语 | `GET /api/v1/workbench/terms` |
| Agent 编目 | `GET /api/v1/workbench/agents`、`POST /agents/{code}/run` |
| 服务目录 | `GET /api/v1/workbench/services` |
| 运行与评测 | `GET /api/v1/workbench/evaluations/latest`、`/runtime/logs`、`POST /runtime/simulate` |
| 数据源 | `GET/POST /api/v1/datasources`、`GET/PATCH/DELETE /api/v1/datasources/{id}` |
| 元数据目录 | `GET/POST /api/v1/datasets`、`/datasets/{id}/columns`、`/columns/{id}` |
| 数据标准 | `GET/POST /api/v1/standards` |
| 语义层 | `GET/POST /api/v1/semantic/assets` |
| 向量写入 | `PUT /api/v1/semantic/assets/{id}/embedding` |
| 向量召回 | `POST /api/v1/semantic/search` |
| 本体图谱 | `GET/POST /api/v1/ontology/nodes`、`POST /api/v1/ontology/relations`、`GET /api/v1/ontology/nodes/{uid}/neighborhood` |
| 数据质量 | `GET/POST /api/v1/quality/rules`、`/rules/{id}/results` |

## 治理工作台

工作台页面由 FastAPI 直接托管：启动后访问 http://localhost:8000/ ，包含资产治理全景图、治理工作台待办、Agent 注册与编目、服务与权限绑定、运行监控与评测闭环、底座速览六个视图。前端调用 `/api/v1/workbench/*`，首次启动会写入演示数据（`SEED_DEMO_DATA=true`）。

### 不依赖中间件本地运行

没有 MySQL / ES / Neo4j 时，可用 SQLite 跑通工作台与测试：

```bash
MYSQL_DSN="sqlite+aiosqlite:////tmp/dgov.db" AUTO_CREATE_TABLES=true \
  uvicorn dgov.main:app --port 8000
```

### 测试

```bash
pytest -q          # 后端 API 测试，使用内存 SQLite，不依赖外部服务
ruff check src tests
```

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
