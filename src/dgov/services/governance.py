from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dgov.core.exceptions import ConflictError, NotFoundError
from dgov.models.governance import (
    ChangeRequest,
    EvaluationRun,
    GovernanceAgent,
    GovernanceService,
    RuntimeLog,
    SemanticTerm,
    WorkOrder,
)
from dgov.schemas.governance import TodoItem

SERVICES: list[dict[str, Any]] = [
    {
        "code": "SVC-01",
        "name": "资产目录检索",
        "engine": "数据治理引擎",
        "input_desc": "域 / 关键字 / 版本",
        "output_desc": "资产清单 + Owner + 分级 + 血缘 + AI 就绪态",
        "evidence": "资产版本 + 血缘",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "✕",
        "description": "按主题域、关键字与版本检索已登记资产，返回可追溯的资产卡片。",
    },
    {
        "code": "SVC-02",
        "name": "质量规则查询 / 稽核",
        "engine": "数据治理引擎",
        "input_desc": "规则 ID / 资产 ID",
        "output_desc": "判定结果 + 异常明细 + 工单号",
        "evidence": "规则编号 + 稽核日志",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "✕",
        "description": "执行质量规则并回传判定结果与问题工单，不改写业务数据。",
    },
    {
        "code": "SVC-03",
        "name": "语义版本解析",
        "engine": "语义治理引擎",
        "input_desc": "术语 / 时点",
        "output_desc": "生效定义 + 版本 + Owner + 适用资产",
        "evidence": "术语版本 + 生效日",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "✕",
        "description": "按业务时点解析唯一生效的语义定义，避免同名不同义。",
    },
    {
        "code": "SVC-04",
        "name": "知识切片检索（RAG）",
        "engine": "语义治理引擎",
        "input_desc": "查询 / 文档范围",
        "output_desc": "候选片段 + 出处 + 文档版本",
        "evidence": "文档条款 + 切片版本",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "受限",
        "allow_output": "✕",
        "description": "制度与规范文档的切片召回，训练用途需单独授权。",
    },
    {
        "code": "SVC-05",
        "name": "血缘与证据回传",
        "engine": "数据治理引擎",
        "input_desc": "结论 / 任务日志号",
        "output_desc": "可点回的证据链",
        "evidence": "任务日志 + 来源",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "✕",
        "description": "把每条结论绑定到资产版本、规则编号与任务日志，保证可点回。",
    },
    {
        "code": "SVC-06",
        "name": "变更影响分析",
        "engine": "数据治理引擎",
        "input_desc": "CR / 字段 / 规则变更",
        "output_desc": "受影响资产 / 规则 / Agent / 任务",
        "evidence": "变更单 + 影响面",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "✕",
        "description": "口径或结构变更发布前的影响面分析，输出回归队列。",
    },
    {
        "code": "SVC-07",
        "name": "报告生成与模板",
        "engine": "Agent-ready 引擎",
        "input_desc": "场景 / 基线 / 治理状态",
        "output_desc": "治理运行报告（带证据标注）",
        "evidence": "资产版本 + 规则 + 术语版本 + 日志号",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "受限",
        "description": "按场景模板生成治理报告，对外输出需单独授权。",
    },
    {
        "code": "SVC-08",
        "name": "回归评测",
        "engine": "Agent-ready 引擎",
        "input_desc": "CR / 任务 ID",
        "output_desc": "重生成结果 + 指标对比",
        "evidence": "评测报告 + 回归队列",
        "allow_system": "✓",
        "allow_retrieval": "✓",
        "allow_training": "✕",
        "allow_output": "✕",
        "description": "口径变更后重跑任务并对比指标，验证治理是否改善了 AI 应用。",
    },
]

AGENTS: list[dict[str, Any]] = [
    {
        "code": "AG-G01",
        "name": "资产发现智能体",
        "kind": "治理",
        "owner": "平台团队",
        "version": "v1.4",
        "status": "on",
        "boundary": "自动",
        "description": "识别数据源、文档类型、重复资产与潜在关系，辅助更新资产目录。",
        "services": ["SVC-01", "SVC-05"],
    },
    {
        "code": "AG-G02",
        "name": "质量诊断智能体",
        "kind": "治理",
        "owner": "李敏 / 赵鹏",
        "version": "v2.1",
        "status": "on",
        "boundary": "复核",
        "description": "分析异常、缺失、冲突与过期数据，提出原因判断与修复建议；不自动改数。",
        "services": ["SVC-02", "SVC-05"],
    },
    {
        "code": "AG-G03",
        "name": "语义建模智能体",
        "kind": "治理",
        "owner": "陈昊 / 孙瑜",
        "version": "v1.2",
        "status": "on",
        "boundary": "复核",
        "description": "抽取概念、属性与关系候选，辅助本体构建；候选须业务确认后方可纳入正式语义体系。",
        "services": ["SVC-03", "SVC-04"],
    },
    {
        "code": "AG-G04",
        "name": "知识治理智能体",
        "kind": "治理",
        "owner": "郑楠",
        "version": "v1.6",
        "status": "on",
        "boundary": "复核",
        "description": "识别文档版本、抽取知识片段、建立证据链，辅助知识库更新。",
        "services": ["SVC-04", "SVC-05"],
    },
    {
        "code": "AG-G05",
        "name": "变更影响智能体",
        "kind": "治理",
        "owner": "平台团队",
        "version": "v1.3",
        "status": "on",
        "boundary": "自动",
        "description": "分析字段、指标、语义、规则或数据源变化对资产、知识、Agent 与流程的影响面。",
        "services": ["SVC-06", "SVC-01", "SVC-03"],
    },
    {
        "code": "AG-G06",
        "name": "安全审查智能体",
        "kind": "治理",
        "owner": "何璐 / 安全法务",
        "version": "v1.5",
        "status": "on",
        "boundary": "复核",
        "description": "发现敏感数据、异常访问与违规使用线索；分级规则来自企业制度，不由模型裁定。",
        "services": ["SVC-01", "SVC-05", "SVC-08"],
    },
    {
        "code": "AG-G07",
        "name": "AI 效果评测智能体",
        "kind": "治理",
        "owner": "周维 / IT 治理办",
        "version": "v1.1",
        "status": "on",
        "boundary": "自动",
        "description": "组织测试集、分析错误类型、定位数据与语义问题、生成评估报告。",
        "services": ["SVC-07", "SVC-08"],
    },
    {
        "code": "AG-B01",
        "name": "治理报告生成 Agent",
        "kind": "业务",
        "owner": "周维 / IT 治理办",
        "version": "v2.0",
        "status": "on",
        "boundary": "批准",
        "description": (
            "读资产/质量/本体/工单状态，产出双域治理运行报告；"
            "三道控制：口径有版本、统计有约束、结论有证据。"
        ),
        "services": ["SVC-01", "SVC-02", "SVC-03", "SVC-05", "SVC-07"],
    },
    {
        "code": "AG-B02",
        "name": "穿透核查报告 Agent",
        "kind": "业务",
        "owner": "孙瑜 / 财务政策",
        "version": "v1.3",
        "status": "on",
        "boundary": "批准",
        "description": "募集资金五段链路穿透核查治理报告；缺口显式标注，不作风险定性。",
        "services": ["SVC-01", "SVC-02", "SVC-03", "SVC-04", "SVC-05", "SVC-07", "SVC-08"],
    },
    {
        "code": "AG-B03",
        "name": "智能问数 / 合同审查 Agent",
        "kind": "业务",
        "owner": "业务部门",
        "version": "v0.9",
        "status": "idle",
        "boundary": "批准",
        "description": "灰度中，阶段 3 接入；当前仅走统一平台接口试运行。",
        "services": ["SVC-03", "SVC-04", "SVC-07"],
    },
]

TERMS: list[dict[str, Any]] = [
    {
        "name": "在编员工",
        "domain": "人力",
        "owner": "陈昊",
        "version": "v1.2",
        "status": "active",
        "definition": "与本企业建立正式劳动关系且当前在职的员工。",
        "effective_date": "2026-07-01",
    },
    {
        "name": "在职 / 离职",
        "domain": "人力",
        "owner": "李敏",
        "version": "v2.3",
        "status": "active",
        "definition": "以劳动合同状态与最后工作日共同判定的人员状态口径。",
        "effective_date": "2026-06-01",
    },
    {
        "name": "薪酬口径",
        "domain": "人力",
        "owner": "王倩",
        "version": "v3.1",
        "status": "active",
        "definition": "应发工资、代扣项与实发工资的统一统计口径。",
        "effective_date": "2026-04-01",
    },
    {
        "name": "最终投向",
        "domain": "资金",
        "owner": "孙瑜",
        "version": "v2.0",
        "status": "active",
        "definition": "募集资金经逐层穿透后实际投向的项目或资产；跨境在途资金暂不计入。",
        "effective_date": "2026-01-01",
    },
    {
        "name": "关联方",
        "domain": "资金",
        "owner": "何璐",
        "version": "v1.8",
        "status": "active",
        "definition": "存在控制、共同控制或重大影响关系的自然人与机构。",
        "effective_date": "2026-03-01",
    },
    {
        "name": "底层资产",
        "domain": "资金",
        "owner": "何璐",
        "version": "v1.3",
        "status": "active",
        "definition": "资管产品穿透后最终承载风险的标的资产。",
        "effective_date": "2026-05-01",
    },
]

WORK_ORDERS: list[dict[str, Any]] = [
    {
        "code": "WO-2026-0211",
        "title": "补录 TXN-0731 末端投向凭证与 purpose_code",
        "subtitle": "链路断链 · 1 笔",
        "domain": "资金",
        "source": "Q-01",
        "owner": "赵鹏",
        "action_label": "认责",
        "status": "pending",
        "kind": "work_order",
        "description": (
            "TXN-20261028-0731 末端缺投向凭证与 purpose_code，parent_txn_id 可回溯但链路在最后一段断开。"
        ),
        "evidence": "FIN_FUND_TRANSFER_D v5.1｜FIN_PROJECT_LEDGER v2.0｜DOC-0631 §2.1",
        "impact": "链路完整率、Q-01/Q-02、报告结论、Q-08 报送比对",
    },
    {
        "code": "WO-2026-0212",
        "title": "确认 2 条关联方 cp_id 编码冲突",
        "subtitle": "主数据合并需业务确认",
        "domain": "资金",
        "source": "Q-04",
        "owner": "何璐",
        "action_label": "确认",
        "status": "pending",
        "kind": "work_order",
        "description": "2 条 cp_id 指向同一实际控制人且存在交叉任职，主数据未合并。",
        "evidence": "FIN_COUNTERPARTY v1.8｜FIN_EQUITY_GRAPH v1.3",
        "impact": "关联识别覆盖率、Q-02 三流关联、证据链",
    },
    {
        "code": "WO-2026-0214",
        "title": "补录 FIN_FUND_POOL_M 2026-10 归集数据",
        "subtitle": "归集月报缺失",
        "domain": "资金",
        "source": "Q-06",
        "owner": "郑楠",
        "action_label": "认责",
        "status": "pending",
        "kind": "work_order",
        "description": "FIN_FUND_POOL_M 2026-10 归集数据未补录，Q-06 暂无法判定，AI 就绪率下降。",
        "evidence": "FIN_FUND_POOL_M v2.0｜专户日切余额",
        "impact": "Q-06、报告数据新鲜度、全景图健康度",
    },
    {
        "code": "EVAL-2026-Q4",
        "title": "确认本轮 Agent 任务评测结论与业务反馈",
        "subtitle": "双域 · AI 评测",
        "domain": "双域",
        "source": "AI 评测",
        "owner": "周维 / IT 治理办",
        "action_label": "确认",
        "status": "waiting",
        "kind": "evaluation",
        "description": "等待穿透核查任务运行后确认评测结论与业务反馈。",
        "evidence": "评测报告 + 业务反馈",
        "impact": "回归队列、Agent 编目、治理全景图",
    },
]

CHANGE_REQUESTS: list[dict[str, Any]] = [
    {
        "code": "CR-2026-0041",
        "title": "评审「最终投向」v2.0 → v2.1 定义变更",
        "subtitle": "口径发布需授权批准",
        "term_name": "最终投向",
        "owner": "孙瑜",
        "from_version": "v2.0",
        "to_version": "v2.1",
        "description": (
            "2026 年三季度起，跨境在途已确认资金计入最终投向；"
            "仍不含集团内中转划转与未形成实际支付的在途资金。"
        ),
        "basis": "IT 治理办反馈 + DOC-0631 2026.Q3 §2.1",
        "status": "pending",
        "affected_agents": ["AG-B01", "AG-B02", "AG-G05", "AG-G07"],
        "affected_objects": ["项目台账", "流水日表", "制度切片", "Q-05/Q-08", "报告任务", "全景图"],
    }
]


async def seed_demo_data(session: AsyncSession) -> None:
    """Insert the demonstration dataset once so a fresh workbench is not empty."""
    existing = (await session.execute(select(func.count()).select_from(GovernanceService))).scalar_one()
    if existing:
        return

    services: dict[str, GovernanceService] = {}
    for item in SERVICES:
        service = GovernanceService(**item)
        session.add(service)
        services[item["code"]] = service
    await session.flush()

    for item in AGENTS:
        codes = item["services"]
        agent = GovernanceAgent(**{k: v for k, v in item.items() if k != "services"})
        agent.services = [services[code] for code in codes]
        session.add(agent)
    for item in TERMS:
        session.add(SemanticTerm(**item))
    for item in WORK_ORDERS:
        session.add(WorkOrder(**item))
    for item in CHANGE_REQUESTS:
        session.add(ChangeRequest(**item))
    await session.commit()


async def list_services(session: AsyncSession) -> list[GovernanceService]:
    result = await session.execute(select(GovernanceService).order_by(GovernanceService.id))
    return list(result.scalars().all())


async def list_agents(session: AsyncSession) -> list[GovernanceAgent]:
    result = await session.execute(
        select(GovernanceAgent)
        .options(selectinload(GovernanceAgent.services))
        .order_by(GovernanceAgent.id)
    )
    return list(result.scalars().all())


async def get_agent(session: AsyncSession, code: str) -> GovernanceAgent:
    result = await session.execute(
        select(GovernanceAgent)
        .options(selectinload(GovernanceAgent.services))
        .where(GovernanceAgent.code == code)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise NotFoundError(f"agent {code} not found")
    return agent


async def list_terms(session: AsyncSession) -> list[SemanticTerm]:
    result = await session.execute(select(SemanticTerm).order_by(SemanticTerm.id))
    return list(result.scalars().all())


async def get_term(session: AsyncSession, name: str) -> SemanticTerm:
    result = await session.execute(select(SemanticTerm).where(SemanticTerm.name == name))
    term = result.scalar_one_or_none()
    if term is None:
        raise NotFoundError(f"semantic term {name} not found")
    return term


async def list_work_orders(session: AsyncSession) -> list[WorkOrder]:
    result = await session.execute(select(WorkOrder).order_by(WorkOrder.id))
    return list(result.scalars().all())


async def get_work_order(session: AsyncSession, code: str) -> WorkOrder:
    result = await session.execute(select(WorkOrder).where(WorkOrder.code == code))
    order = result.scalar_one_or_none()
    if order is None:
        raise NotFoundError(f"work order {code} not found")
    return order


async def complete_work_order(session: AsyncSession, code: str) -> WorkOrder:
    order = await get_work_order(session, code)
    if order.status != "pending":
        raise ConflictError(f"work order {code} is not pending (status={order.status})")
    order.status = "done"
    order.closed_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(order)
    return order


async def list_change_requests(session: AsyncSession) -> list[ChangeRequest]:
    result = await session.execute(select(ChangeRequest).order_by(ChangeRequest.id))
    return list(result.scalars().all())


async def get_change_request(session: AsyncSession, code: str) -> ChangeRequest:
    result = await session.execute(select(ChangeRequest).where(ChangeRequest.code == code))
    request = result.scalar_one_or_none()
    if request is None:
        raise NotFoundError(f"change request {code} not found")
    return request


async def approve_change_request(
    session: AsyncSession, code: str, approved_by: str = "孙瑜"
) -> tuple[ChangeRequest, SemanticTerm, EvaluationRun | None]:
    request = await get_change_request(session, code)
    if request.status != "pending":
        raise ConflictError(f"change request {code} is not pending (status={request.status})")

    term = await get_term(session, request.term_name)
    request.status = "approved"
    request.approved_by = approved_by
    request.approved_at = datetime.now(UTC).replace(tzinfo=None)
    term.version = request.to_version
    term.status = "pending_regression"

    regression = EvaluationRun(
        task_code=f"REG-{request.code.split('-')[-1]}",
        agent_code="MULTI",
        status="queued",
        metrics={},
        feedback=(
            f"{request.code} 已批准：等待 {len(request.affected_agents)} 个 Agent "
            f"按 {term.version} 回归。"
        ),
        steps=[],
        evidence={"变更单": request.code, "术语版本": term.version},
    )
    session.add(regression)
    await session.commit()
    await session.refresh(request)
    await session.refresh(term)
    await session.refresh(regression)
    return request, term, regression


async def list_todos(session: AsyncSession) -> list[TodoItem]:
    orders = await list_work_orders(session)
    requests = await list_change_requests(session)
    rows: list[TodoItem] = [
        TodoItem(
            code=item.code,
            title=item.title,
            subtitle=item.subtitle,
            domain=item.domain,
            source=item.source,
            owner=item.owner,
            action=item.action_label,
            status=item.status,
            kind=item.kind,
        )
        for item in orders
    ]
    rows.extend(
        TodoItem(
            code=item.code,
            title=item.title,
            subtitle=item.subtitle,
            domain="资金",
            source="本体变更",
            owner=item.owner,
            action="批准",
            status=item.status,
            kind="change_request",
        )
        for item in requests
    )
    priority = {"work_order": 0, "change_request": 1, "evaluation": 2}
    rows.sort(key=lambda row: priority.get(row.kind, 9))
    return rows


async def latest_evaluation(
    session: AsyncSession, completed_only: bool = True
) -> EvaluationRun | None:
    stmt = select(EvaluationRun)
    if completed_only:
        stmt = stmt.where(EvaluationRun.status == "completed")
    result = await session.execute(stmt.order_by(EvaluationRun.id.desc()).limit(1))
    return result.scalar_one_or_none()


async def list_runtime_logs(session: AsyncSession, limit: int = 20) -> list[RuntimeLog]:
    result = await session.execute(select(RuntimeLog).order_by(RuntimeLog.id.desc()).limit(limit))
    return list(reversed(result.scalars().all()))


def _task_code(sequence: int) -> str:
    today = date.today().strftime("%Y%m%d")
    return f"T-{today}-{42 + sequence:04d}"


RUN_STEPS: list[dict[str, str]] = [
    {
        "no": "①",
        "title": "身份与意图校验",
        "meta": "AG-B02 以 svc-penetration-report 身份发起，绑定报告模板与场景基线",
        "body": "校验通过；载入试点链路五段、双域术语版本与效果基线。Agent 无权自行扩大数据范围。",
    },
    {
        "no": "②",
        "title": "服务编排（三类引擎协同）",
        "meta": "按契约依次调用服务，全部经统一平台接口",
        "body": (
            "SVC-03 语义版本解析 → SVC-01 资产目录检索 → SVC-02 质量规则稽核 → "
            "SVC-04 知识切片 → SVC-05 证据回传 → SVC-07 报告生成。"
        ),
    },
    {
        "no": "③",
        "title": "运行时策略生效",
        "meta": "四种权限分离，不依赖提示词",
        "body": "资产检索、知识切片、聚合统计允许模型检索；敏感明细用于训练或对外输出被策略拦截并写入审计。",
    },
    {
        "no": "④",
        "title": "输出与缺口标注",
        "meta": "结论附资产版本 + 规则编号 + 术语版本 + 日志号",
        "body": (
            "在管资产 11 项（8 就绪、3 待处置），募集款 28.0 亿元，"
            "链路缺口 1.2 亿元显式标注，不静默填空。"
        ),
    },
    {
        "no": "⑤",
        "title": "任务评测 · 五项指标",
        "meta": "AI 效果评测智能体 + IT 治理办业务反馈",
        "body": "链路完整率 95.7%、证据完整率 100%、口径一致性 100%、任务成功率 95%、业务反馈改善 80%。",
    },
    {
        "no": "⑥",
        "title": "变更与回归 · 闭环反哺",
        "meta": "反馈 → CR → 语义 Owner 批准 → 影响分析 → 重生成",
        "body": "命中口径漂移 1 处，已生成变更建议，等待语义 Owner 批准后重生成并回归评测。",
    },
]

RUN_METRICS: dict[str, float] = {
    "链路完整率": 95.7,
    "证据完整率": 100.0,
    "口径一致性": 100.0,
    "任务成功率": 95.0,
    "业务反馈改善": 80.0,
}


async def run_agent(session: AsyncSession, code: str = "AG-B02") -> tuple[EvaluationRun, list[RuntimeLog]]:
    agent = await get_agent(session, code)
    count = (await session.execute(select(func.count()).select_from(EvaluationRun))).scalar_one()
    task_code = _task_code(count)

    run = EvaluationRun(
        task_code=task_code,
        agent_code=agent.code,
        status="completed",
        metrics=RUN_METRICS,
        feedback="Q3 新规应考虑跨境在途已确认资金 → 命中口径漂移 1 处。",
        steps=RUN_STEPS,
        evidence={
            "资产版本": "资产登记表 v1.4~v5.1",
            "规则编号": "Q-01 / Q-02 / Q-06",
            "术语版本": "最终投向 v2.0",
            "任务日志": task_code,
        },
    )
    session.add(run)
    await session.flush()

    logs = [
        RuntimeLog(run_id=run.id, level="PASS", message=f"auth: {agent.code} / svc-penetration-report"),
        RuntimeLog(run_id=run.id, level="PASS", message="policy: SVC-01/03/04 → 允许模型检索"),
        RuntimeLog(run_id=run.id, level="PASS", message="orchestration: SVC-03 → 01 → 02 → 04 → 05 → 07"),
        RuntimeLog(run_id=run.id, level="HINT", message="权限由运行时策略执行，不写进提示词"),
        RuntimeLog(run_id=run.id, level="PASS", message=f"evidence: 结论已绑定 {task_code}"),
    ]
    session.add_all(logs)

    order = await get_work_order(session, "EVAL-2026-Q4")
    if order.status == "waiting":
        order.status = "pending"
    await session.commit()
    await session.refresh(run)
    for log in logs:
        await session.refresh(log)
    return run, logs


async def simulate_blocked_call(session: AsyncSession) -> tuple[bool, list[RuntimeLog]]:
    latest = await latest_evaluation(session, completed_only=False)
    run_id = latest.id if latest else None
    task_code = latest.task_code if latest else "T-UNBOUND"
    logs = [
        RuntimeLog(run_id=run_id, level="BLOCK", message="request: SVC-07 output → 对外输出敏感明细"),
        RuntimeLog(run_id=run_id, level="BLOCK", message="reason: 未获对外输出授权 / 无业务用途"),
        RuntimeLog(
            run_id=run_id,
            level="AUDIT",
            message=f"写入 {task_code}，报告不含该字段",
        ),
    ]
    session.add_all(logs)
    await session.commit()
    for log in logs:
        await session.refresh(log)
    return True, logs


async def overview(session: AsyncSession) -> dict[str, Any]:
    agents = await list_agents(session)
    services = await list_services(session)
    terms = await list_terms(session)
    orders = await list_work_orders(session)
    requests = await list_change_requests(session)

    pending_orders = sum(1 for item in orders if item.status == "pending")
    pending_changes = sum(1 for item in requests if item.status == "pending")
    regression_agents = sorted(
        {code for item in requests if item.status == "approved" for code in item.affected_agents}
    )
    term_use = next((item for item in terms if item.name == "最终投向"), None)
    latest_run = await latest_evaluation(session, completed_only=True)

    if term_use is not None and term_use.status == "pending_regression":
        loop_value = "回归中"
        loop_note = "口径变更待回归评测"
        term_note = "1 项变更待回归"
    else:
        loop_value = f"{1 if latest_run else 0} 轮"
        loop_note = "命中口径漂移 1 处" if latest_run else "尚未运行任务"
        term_note = "均有 Owner 与版本"

    kpis = [
        {"label": "在管资产", "value": 21, "unit": "", "note": "人力 10 / 资金 11"},
        {"label": "Owner 覆盖", "value": 100, "unit": "%", "note": "21/21 已认责"},
        {"label": "AI 就绪率", "value": 86, "unit": "%", "note": "18/21 可进生成"},
        {"label": "在管 Agent", "value": len(agents), "unit": "", "note": "治理 7 / 业务 3"},
        {"label": "语义术语", "value": len(terms), "unit": "", "note": term_note},
        {"label": "评测闭环", "value": loop_value, "unit": "", "note": loop_note},
    ]

    domains = [
        {
            "unit": "人力域",
            "sub": "",
            "assets": "10 项",
            "rules": "5 / 4",
            "term": "在编员工 v1.2",
            "ai_ready": "10/10",
            "todos": "1",
            "owner": "陈昊",
        },
        {
            "unit": "财务资金域",
            "sub": "穿透式监管链路",
            "assets": "11 项",
            "rules": "8 / 1",
            "term": f"最终投向 {term_use.version if term_use else 'v2.0'}",
            "ai_ready": "8/11",
            "todos": f"{pending_orders} + {pending_changes} CR",
            "owner": "孙瑜",
        },
    ]

    health = [
        {"name": "Owner 认责", "value": 100, "tone": ""},
        {"name": "版本可追溯", "value": 100, "tone": ""},
        {"name": "血缘完整率", "value": 90, "tone": "b"},
        {"name": "质量规则通过", "value": 38, "tone": "w"},
        {"name": "AI 就绪率", "value": 86, "tone": "b"},
    ]

    phases = [
        {"idx": "01", "name": "诊断试点", "note": "四个清单 + 基线 ✓"},
        {"idx": "02", "name": "最小平台", "note": "当前 · 闭环已跑通"},
        {"idx": "03", "name": "共性沉淀", "note": "引擎封装待启动"},
        {"idx": "04", "name": "持续运营", "note": "预算与考核待定"},
    ]

    decisions = [
        "① 共性能力是否下沉为三类引擎：双域已复用登记模板、口径规则、评测回路与服务契约。",
        "② Agent 是否扩编：当前 10 个，7 类治理智能体全部通过统一平台接口调用，无割裂治理知识。",
        "③ 治理报告是否纳入月度例行：证据完整率 100%，但仍有 4 张工单与 1 处口径漂移，"
        "建议回归评测完成后再例行化。",
    ]

    return {
        "kpis": kpis,
        "domains": domains,
        "health": health,
        "phases": phases,
        "decisions": decisions,
        "stats": {
            "agent_count": len(agents),
            "service_count": len(services),
            "term_count": len(terms),
            "pending_work_orders": pending_orders,
            "pending_change_requests": pending_changes,
            "regression_agents": len(regression_agents),
            "evaluation_runs": 1 if latest_run else 0,
        },
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def foundation(session: AsyncSession) -> dict[str, Any]:
    orders = await list_work_orders(session)
    requests = await list_change_requests(session)
    services = await list_services(session)
    terms = await list_terms(session)
    term_use = next((item for item in terms if item.name == "最终投向"), None)
    pending = sum(1 for item in orders if item.status == "pending")

    rows = [
        {
            "capability": "元数据 / 资产登记",
            "managed": "21 项",
            "status": "已认责",
            "tone": "ok",
            "landing": "资产详情页",
        },
        {
            "capability": "质量规则与工单",
            "managed": "13 条 / 5 单",
            "status": f"{pending} 待办",
            "tone": "warn",
            "landing": "工作台",
        },
        {
            "capability": "标准与主数据",
            "managed": "cp_id 等 4 类",
            "status": "2 冲突",
            "tone": "warn",
            "landing": "WO-0212",
        },
        {
            "capability": "指标口径 / 业务规则 / 数据模型",
            "managed": f"{len(terms)} 术语 / 9 规则",
            "status": "版本受控",
            "tone": "ok",
            "landing": "本体视图",
        },
        {
            "capability": "动态本体（六项机制）",
            "managed": f"{len(terms)} 术语",
            "status": f"{term_use.version if term_use else 'v2.0'} 生效",
            "tone": "acc",
            "landing": "CR-0041",
        },
        {
            "capability": "知识与非结构化",
            "managed": "4 份制度切片",
            "status": "证据链完整",
            "tone": "ok",
            "landing": "SVC-04 / 05",
        },
        {
            "capability": "血缘 / 版本 / 变更 / 审计",
            "managed": "全链路",
            "status": "可追溯",
            "tone": "ok",
            "landing": "T-0042 日志",
        },
        {
            "capability": "权限与四种权限分离",
            "managed": f"{len(services)} 服务",
            "status": "运行时生效",
            "tone": "ok",
            "landing": "服务契约",
        },
        {
            "capability": "服务管理 / agent-ready",
            "managed": f"{len(services)} 服务",
            "status": "统一目录",
            "tone": "ok",
            "landing": "p3 视图",
        },
    ]
    mapping = [
        {"idx": "01", "name": "政策要求", "note": "行业高质量数据集建设 / 三项国标"},
        {"idx": "02", "name": "企业制度", "note": "五项基础制度 · 语义 Owner 唯一变更权"},
        {"idx": "03", "name": "治理流程", "note": "九步流程 · 按风险分级，不做九道审批"},
        {"idx": "04", "name": "平台控制", "note": "规则引擎 · 版本审批 · 运行时策略 · 工单"},
        {"idx": "05", "name": "应用证据", "note": "任务日志 T-0042 · 证据可点回 · 回归记录"},
    ]
    return {
        "rows": rows,
        "mapping": mapping,
        "pending_work_orders": pending,
        "pending_change_requests": sum(1 for item in requests if item.status == "pending"),
    }
