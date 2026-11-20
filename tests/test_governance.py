from httpx import AsyncClient

API = "/api/v1/workbench"


async def test_overview_snapshot(client: AsyncClient) -> None:
    response = await client.get(f"{API}/overview")
    assert response.status_code == 200
    data = response.json()

    assert len(data["kpis"]) == 6
    assert data["stats"] == {
        "agent_count": 10,
        "service_count": 8,
        "term_count": 6,
        "pending_work_orders": 3,
        "pending_change_requests": 1,
        "regression_agents": 0,
        "evaluation_runs": 0,
    }
    assert data["domains"][1]["term"] == "最终投向 v2.0"
    assert data["kpis"][3]["value"] == 10


async def test_todos_merge_work_orders_and_change_requests(client: AsyncClient) -> None:
    response = await client.get(f"{API}/todos")
    assert response.status_code == 200
    todos = response.json()

    assert [item["code"] for item in todos] == [
        "WO-2026-0211",
        "WO-2026-0212",
        "WO-2026-0214",
        "CR-2026-0041",
        "EVAL-2026-Q4",
    ]
    assert [item["kind"] for item in todos] == [
        "work_order",
        "work_order",
        "work_order",
        "change_request",
        "evaluation",
    ]
    assert todos[3]["status"] == "pending"
    assert todos[4]["status"] == "waiting"


async def test_agents_are_registered_with_service_bindings(client: AsyncClient) -> None:
    response = await client.get(f"{API}/agents")
    assert response.status_code == 200
    agents = response.json()

    assert len(agents) == 10
    by_code = {agent["code"]: agent for agent in agents}
    assert by_code["AG-B02"]["boundary"] == "批准"
    assert len(by_code["AG-B02"]["services"]) == 7
    assert {service["code"] for service in by_code["AG-G01"]["services"]} == {"SVC-01", "SVC-05"}
    assert by_code["AG-B03"]["status"] == "idle"


async def test_services_expose_four_separated_permissions(client: AsyncClient) -> None:
    response = await client.get(f"{API}/services")
    assert response.status_code == 200
    services = response.json()

    assert len(services) == 8
    by_code = {service["code"]: service for service in services}
    assert by_code["SVC-07"]["allow_output"] == "受限"
    assert by_code["SVC-04"]["allow_training"] == "受限"
    assert by_code["SVC-01"]["allow_training"] == "✕"
    assert all(item["engine"] for item in services)


async def test_complete_work_order_and_refresh_overview(client: AsyncClient) -> None:
    response = await client.post(f"{API}/work-orders/WO-2026-0211/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["closed_at"] is not None

    duplicate = await client.post(f"{API}/work-orders/WO-2026-0211/complete")
    assert duplicate.status_code == 409

    overview = (await client.get(f"{API}/overview")).json()
    assert overview["stats"]["pending_work_orders"] == 2
    assert overview["domains"][1]["todos"] == "2 + 1 CR"

    unknown = await client.post(f"{API}/work-orders/WO-9999/complete")
    assert unknown.status_code == 404


async def test_approve_change_request_versions_term_and_queues_regression(
    client: AsyncClient,
) -> None:
    response = await client.post(f"{API}/change-requests/CR-2026-0041/approve", json={})
    assert response.status_code == 200
    result = response.json()

    assert result["change_request"]["status"] == "approved"
    assert result["change_request"]["approved_by"] == "孙瑜"
    assert result["term"]["version"] == "v2.1"
    assert result["term"]["status"] == "pending_regression"
    assert result["affected_agents"] == ["AG-B01", "AG-B02", "AG-G05", "AG-G07"]
    assert result["regression_run"]["status"] == "queued"

    overview = (await client.get(f"{API}/overview")).json()
    assert overview["stats"]["pending_change_requests"] == 0
    assert overview["stats"]["regression_agents"] == 4
    assert overview["kpis"][5]["value"] == "回归中"

    terms = (await client.get(f"{API}/terms")).json()
    assert {term["name"]: term["version"] for term in terms}["最终投向"] == "v2.1"

    duplicate = await client.post(f"{API}/change-requests/CR-2026-0041/approve", json={})
    assert duplicate.status_code == 409


async def test_run_agent_creates_evaluation_logs_and_todo(client: AsyncClient) -> None:
    response = await client.post(f"{API}/agents/AG-B02/run")
    assert response.status_code == 201
    payload = response.json()

    run = payload["run"]
    assert run["status"] == "completed"
    assert run["task_code"].startswith("T-")
    assert run["metrics"] == {
        "链路完整率": 95.7,
        "证据完整率": 100.0,
        "口径一致性": 100.0,
        "任务成功率": 95.0,
        "业务反馈改善": 80.0,
    }
    assert len(run["steps"]) == 6
    assert run["evidence"]["术语版本"] == "最终投向 v2.0"
    assert {log["level"] for log in payload["logs"]} == {"PASS", "HINT"}

    latest = (await client.get(f"{API}/evaluations/latest")).json()
    assert latest["task_code"] == run["task_code"]

    todos = (await client.get(f"{API}/todos")).json()
    evaluation = next(item for item in todos if item["kind"] == "evaluation")
    assert evaluation["status"] == "pending"

    overview = (await client.get(f"{API}/overview")).json()
    assert overview["stats"]["evaluation_runs"] == 1
    assert overview["kpis"][5]["value"] == "1 轮"


async def test_simulate_blocked_call_writes_audit_trail(client: AsyncClient) -> None:
    response = await client.post(f"{API}/runtime/simulate")
    assert response.status_code == 200
    payload = response.json()

    assert payload["blocked"] is True
    assert [log["level"] for log in payload["logs"]] == ["BLOCK", "BLOCK", "AUDIT"]
    assert "对外输出" in payload["logs"][0]["message"]

    logs = (await client.get(f"{API}/runtime/logs")).json()
    assert len(logs) == 3


async def test_foundation_snapshot(client: AsyncClient) -> None:
    response = await client.get(f"{API}/foundation")
    assert response.status_code == 200
    data = response.json()

    assert len(data["rows"]) == 9
    assert len(data["mapping"]) == 5
    assert data["rows"][0]["capability"] == "元数据 / 资产登记"
    assert data["mapping"][0]["name"] == "政策要求"


async def test_unknown_agent_returns_404(client: AsyncClient) -> None:
    response = await client.post(f"{API}/agents/AG-UNKNOWN/run")
    assert response.status_code == 404


async def test_workbench_page_is_served(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "数据治理工作台" in response.text
    assert "敬请期待功能上线......" in response.text
    assert "/platform/modules" in response.text
    assert "/auth/login" in response.text
