#!/usr/bin/env bash
# 一键启动 DMPlatform（内存 SQLite + 内置 Mock 元数据/智能体 API，无需外部中间件）
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH=src
export MYSQL_DSN="sqlite+aiosqlite://"
export AUTO_CREATE_TABLES=true
export INIT_OPTIONAL_INFRA=false
export SEED_DEMO_DATA=true
export SEED_METADATA_SOURCE=true
export LINEAGE_INSIGHT_ENABLED=true
export LINEAGE_INSIGHT_AUTO_TRIGGER=true
echo "启动中：http://localhost:8000  (admin / abc123456789)"
exec python3 -m uvicorn dgov.main:app --host 0.0.0.0 --port 8000
