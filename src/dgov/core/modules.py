"""Platform module catalogue and functional permission codes.

Keeping the catalogue in one place lets the navigation API, role editor and
authorization checks share the same identifiers.
"""

from __future__ import annotations

from typing import Any

MODULE_GROUPS: list[dict[str, Any]] = [
    {
        "key": "engines",
        "title": "两大引擎",
        "items": [
            {
                "id": "governance-engine",
                "name": "治理引擎",
                "description": "统一承载元数据、质量、标准与治理流程",
            },
            {
                "id": "agent-ready-engine",
                "name": "Agent 就绪引擎",
                "description": "提供语义理解、知识检索、证据回传与任务评测",
            },
        ],
    },
    {
        "key": "applications",
        "title": "应用功能",
        "items": [
            {"id": "overview", "name": "总览视图", "description": "数据资产与治理运行态势"},
            {
                "id": "asset-inventory",
                "name": "资产盘点",
                "description": "资产总量、分层分布、注释与血缘覆盖",
                "implemented": True,
            },
            {
                "id": "metadata-lineage",
                "name": "元数据及血缘",
                "description": "表与字段元数据、表级与字段级血缘",
                "implemented": True,
            },
            {
                "id": "lineage-insight",
                "name": "血缘智能巡检",
                "description": "任务血缘增量采集、规范校验、问题识别与反馈告警",
                "implemented": True,
            },
            {
                "id": "standards",
                "name": "标准规范",
                "description": "数据标准与血缘关系开发规范维护",
                "implemented": True,
            },
            {"id": "classification", "name": "分级分类", "description": "数据分类分级与敏感标识"},
            {"id": "metrics", "name": "指标及业务口径", "description": "指标定义、版本与业务口径"},
            {
                "id": "quality",
                "name": "质量检测",
                "description": "基于元数据的质量规则、巡检与问题清单",
                "implemented": True,
            },
            {
                "id": "warehouse-modeling",
                "name": "数仓建模",
                "description": "分层建模规范、模型登记与合规评分",
                "implemented": True,
            },
            {
                "id": "ontology-modeling",
                "name": "本体建模",
                "description": "业务实体识别、实体-表映射与关系图谱",
                "implemented": True,
            },
            {"id": "privacy", "name": "隐私权限", "description": "隐私数据与访问策略"},
            {"id": "data-service", "name": "数据服务", "description": "数据服务发布与调用管理"},
            {
                "id": "log-query",
                "name": "日志查询",
                "description": "调度、盘点、元数据与质量巡检运行日志",
                "implemented": True,
            },
        ],
    },
    {
        "key": "configurations",
        "title": "管理配置",
        "items": [
            {
                "id": "user-center",
                "name": "用户中心",
                "description": "个人资料、密码与平台用户管理",
                "implemented": True,
            },
            {
                "id": "user-permissions",
                "name": "用户权限配置",
                "description": "角色、功能权限与用户级授权",
                "implemented": True,
            },
            {
                "id": "datasource-config",
                "name": "数据源配置",
                "description": "API 数据源、环境切换与采集调度",
                "implemented": True,
            },
            {"id": "api-config", "name": "API 配置", "description": "平台接口与服务连接配置"},
            {"id": "metamodel-config", "name": "元模型配置", "description": "元模型实体与属性配置"},
            {
                "id": "agent-config",
                "name": "智能体配置",
                "description": "智能体 API 端点注册、连通性测试与调用评测",
                "implemented": True,
            },
        ],
    },
]

ALL_MODULES: list[dict[str, Any]] = [item for group in MODULE_GROUPS for item in group["items"]]


def module_permission(module_id: str) -> str:
    return f"module:{module_id}:view"


ACTION_PERMISSIONS: list[dict[str, str]] = [
    {"code": "platform:read", "name": "平台访问", "group": "基础"},
    {"code": "governance:write", "name": "治理数据维护", "group": "治理"},
    {"code": "config:write", "name": "管理配置修改", "group": "配置"},
    {"code": "collection:run", "name": "触发数据采集", "group": "配置"},
    {"code": "users:manage", "name": "用户管理", "group": "安全"},
    {"code": "authz:manage", "name": "角色与授权管理", "group": "安全"},
    {"code": "logs:read", "name": "日志查询", "group": "审计"},
    {"code": "agent:manage", "name": "智能体端点管理", "group": "智能体"},
    {"code": "lineage.flow:write", "name": "巡检流程配置", "group": "血缘巡检"},
    {"code": "lineage.flow:run", "name": "触发巡检流程", "group": "血缘巡检"},
    {"code": "lineage.issue:review", "name": "血缘问题复核", "group": "血缘巡检"},
]

PERMISSION_CATALOG: list[dict[str, str]] = [
    *ACTION_PERMISSIONS,
    *[
        {
            "code": module_permission(item["id"]),
            "name": f"查看{group['title']} · {item['name']}",
            "group": "功能模块",
        }
        for group in MODULE_GROUPS
        for item in group["items"]
    ],
]

PERMISSION_CODES = {item["code"] for item in PERMISSION_CATALOG}

ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "admin": {
        "name": "系统管理员",
        "description": "拥有平台全部功能与配置权限",
        "is_system": True,
        "permissions": [item["code"] for item in PERMISSION_CATALOG],
    },
    "governance": {
        "name": "治理专员",
        "description": "维护治理数据、执行采集与质量巡检",
        "is_system": True,
        "permissions": [
            "platform:read",
            "governance:write",
            "config:write",
            "collection:run",
            "logs:read",
            "lineage.flow:write",
            "lineage.flow:run",
            "lineage.issue:review",
            *[
                module_permission(item["id"])
                for item in ALL_MODULES
                if item["id"] not in {"user-permissions", "api-config", "metamodel-config"}
            ],
        ],
    },
    "viewer": {
        "name": "只读用户",
        "description": "只读查看资产、血缘、质量与日志",
        "is_system": True,
        "permissions": [
            "platform:read",
            "logs:read",
            *[
                module_permission(item["id"])
                for item in ALL_MODULES
                if item["id"]
                in {
                    "overview",
                    "governance-engine",
                    "agent-ready-engine",
                    "asset-inventory",
                    "metadata-lineage",
                    "standards",
                    "classification",
                    "metrics",
                    "quality",
                    "warehouse-modeling",
                    "ontology-modeling",
                    "privacy",
                    "data-service",
                    "log-query",
                    "user-center",
                    "lineage-insight",
                }
            ],
        ],
    },
}
