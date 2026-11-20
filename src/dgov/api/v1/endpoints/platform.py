from fastapi import APIRouter

router = APIRouter(prefix="/platform", tags=["platform"])

MODULE_GROUPS = [
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
            {"id": "asset-inventory", "name": "资产盘点", "description": "资产清单、责任人与就绪状态"},
            {"id": "metadata-lineage", "name": "元数据及血缘", "description": "元数据采集与全链路血缘"},
            {"id": "standards", "name": "标准规范", "description": "数据标准与规范落标"},
            {"id": "classification", "name": "分级分类", "description": "数据分类分级与敏感标识"},
            {"id": "metrics", "name": "指标及业务口径", "description": "指标定义、版本与业务口径"},
            {"id": "quality", "name": "质量检测", "description": "质量规则、稽核与问题整改"},
            {"id": "warehouse-modeling", "name": "数仓建模", "description": "数仓分层与模型设计"},
            {"id": "ontology-modeling", "name": "本体建模", "description": "业务实体、属性与关系建模"},
            {"id": "privacy", "name": "隐私权限", "description": "隐私数据与访问策略"},
            {"id": "data-service", "name": "数据服务", "description": "数据服务发布与调用管理"},
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
            {"id": "user-permissions", "name": "用户权限配置", "description": "角色与功能权限配置"},
            {"id": "datasource-config", "name": "数据源配置", "description": "数据源连接与采集配置"},
            {"id": "api-config", "name": "API 配置", "description": "平台接口与服务连接配置"},
            {"id": "metamodel-config", "name": "元模型配置", "description": "元模型实体与属性配置"},
            {"id": "agent-config", "name": "智能体配置", "description": "智能体注册与服务编排配置"},
        ],
    },
]


@router.get("/modules")
async def list_modules() -> list[dict]:
    return MODULE_GROUPS
