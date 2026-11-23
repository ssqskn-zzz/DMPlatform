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

无中间件、无外部智能体的一键体验（内置 Mock 元数据 API + Mock 智能体 API，内存 SQLite）：

```bash
MYSQL_DSN="sqlite+aiosqlite://" AUTO_CREATE_TABLES=true INIT_OPTIONAL_INFRA=false \
  uvicorn dgov.main:app --port 8000
```

首次启动会自动采集样例血缘、发布默认血缘开发规范、注册默认巡检流程与智能体端点，并在采集完成后自动跑一次巡检。

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
工作台已接入两大引擎、13 项应用功能和 6 项管理配置入口。已上线：资产盘点、元数据及血缘、血缘智能巡检、标准规范（含血缘开发规范）、质量检测、数仓建模、本体建模、日志查询、用户中心、用户权限配置、数据源配置、智能体配置；分级分类、指标及业务口径、隐私权限、数据服务、API 配置、元模型配置仍为规划中。

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
| 血缘开发规范 | `GET /api/v1/lineage-specs`、`/active`、`POST /publish`（复用 `/standards` 存储，按 category 过滤） |
| 智能体端点 | `GET/POST /api/v1/agent-endpoints`、`GET/PATCH/DELETE /{id}`、`POST /{id}/test`、`GET /{id}/evaluations` |
| 血缘巡检流程 | `GET/POST /api/v1/lineage-flows`、`GET/PATCH/DELETE /{id}`、`POST /{id}/run`、`GET /{id}/runs` |
| 巡检运行与复核 | `GET /api/v1/lineage-runs[/{id}]`、`GET /lineage-issues`、`PATCH /lineage-issues/{id}`、`GET /lineage-alerts`、`POST /lineage-alerts/{id}/retry`、`GET /lineage-insight/summary` |
| 增量血缘采集 | `POST /api/v1/datasources/{id}/collect`（请求体带 `since` 即按 `updatetime` 增量拉取血缘） |

## 治理工作台

工作台页面由 FastAPI 直接托管：启动后访问 http://localhost:8000/ 。除原有视图外，新增三个页面：「血缘智能巡检」（流程、阶段时间线、问题复核、告警）、「标准规范」（血缘开发规范全文与版本发布）、「智能体配置」（端点注册、连通性测试、调用评测）。菜单由 `/api/v1/platform/modules` 动态生成，权限码 `lineage.flow:write` / `lineage.flow:run` / `lineage.issue:review` / `agent:manage` 控制写操作。

### 不依赖中间件本地运行

没有 MySQL / ES / Neo4j 时，可用 SQLite 跑通工作台与测试：

```bash
MYSQL_DSN="sqlite+aiosqlite://" AUTO_CREATE_TABLES=true INIT_OPTIONAL_INFRA=false \
  uvicorn dgov.main:app --port 8000
```

`internal://mock-metadata` 与 `internal://mock-agent` 是进程内 Mock 服务（同时挂载在 `/mock/metadata`、`/mock/agent`），不需要任何外部 API 即可演示「定期增量血缘 → 规范+表级血缘送智能体 → 问题识别 → 反馈告警」全链路。生产环境把数据源 `test_base_url` / `production_base_url` 与智能体端点 `base_url` 指向真实服务即可。

### 测试

```bash
pytest -q          # 73 个用例，内存 SQLite + ASGITransport，不依赖外部服务
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

开发环境 `AUTO_CREATE_TABLES=true` 会直接建表；仓库已包含首个初始迁移（含 5 张血缘巡检表），生产环境关闭自动建表后执行：

```bash
alembic upgrade head
```

## 后续可扩展

- 生产血缘 API 返回 `task_name` / `task_id` 后，补「任务名是否等于表名」的精确一致性比对
- webhook / 邮件告警通道（受 `allow_output` 运行时权限控制）
- 多实例部署下的调度分布式锁（当前调度器为单进程 asyncio 任务）
- 语义层与本体节点的映射、权限与审批流
