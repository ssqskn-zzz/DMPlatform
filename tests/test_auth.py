from httpx import AsyncClient

from dgov.models.auth import PlatformUser
from dgov.services.auth import DEFAULT_PASSWORD, set_password, verify_password

API = "/api/v1"


async def login_as(client: AsyncClient, username: str, password: str = DEFAULT_PASSWORD) -> dict:
    response = await client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    client.headers.update({"Authorization": f"Bearer {payload['token']}"})
    return payload


async def create_user(client: AsyncClient, username: str, role: str = "viewer") -> dict:
    response = await client.post(
        f"{API}/users",
        json={"username": username, "display_name": f"测试 {username}", "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_password_is_salted_and_hashed() -> None:
    first = PlatformUser(username="first", display_name="First", role="viewer")
    second = PlatformUser(username="second", display_name="Second", role="viewer")
    set_password(first, DEFAULT_PASSWORD)
    set_password(second, DEFAULT_PASSWORD)

    assert first.password_hash != DEFAULT_PASSWORD
    assert first.password_salt != second.password_salt
    assert first.password_hash != second.password_hash
    assert verify_password(first, DEFAULT_PASSWORD)
    assert not verify_password(first, "incorrect-password")


async def test_default_users_can_login_and_receive_permissions(client: AsyncClient) -> None:
    for username, role in (("admin", "admin"), ("governance", "governance"), ("viewer", "viewer")):
        payload = await login_as(client, username)
        assert payload["token_type"] == "bearer"
        assert payload["user"]["username"] == username
        assert payload["user"]["role"] == role
        assert "platform:read" in payload["user"]["permissions"]
        assert "password_hash" not in payload["user"]
        assert "password_salt" not in payload["user"]


async def test_login_rejects_invalid_credentials(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"username": "admin", "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


async def test_protected_api_requires_bearer_token(client: AsyncClient) -> None:
    client.headers.pop("Authorization")
    response = await client.get(f"{API}/platform/modules")
    assert response.status_code == 401

    response = await client.get(
        f"{API}/platform/modules", headers={"Authorization": "Bearer invalid-token"}
    )
    assert response.status_code == 401


async def test_logout_invalidates_current_session(client: AsyncClient) -> None:
    response = await client.post(f"{API}/auth/logout")
    assert response.status_code == 200

    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 401


async def test_platform_catalog_contains_all_requested_modules(client: AsyncClient) -> None:
    response = await client.get(f"{API}/platform/modules")
    assert response.status_code == 200
    groups = {group["key"]: group["items"] for group in response.json()}
    assert set(groups) == {"engines", "applications", "configurations"}
    assert len(groups["engines"]) == 2
    assert len(groups["applications"]) == 12
    assert len(groups["configurations"]) == 6

    names = {item["name"] for items in groups.values() for item in items}
    assert {
        "治理引擎",
        "Agent 就绪引擎",
        "总览视图",
        "资产盘点",
        "元数据及血缘",
        "标准规范",
        "分级分类",
        "指标及业务口径",
        "质量检测",
        "数仓建模",
        "本体建模",
        "隐私权限",
        "数据服务",
        "日志查询",
        "用户中心",
        "用户权限配置",
        "数据源配置",
        "API 配置",
        "元模型配置",
        "智能体配置",
    } == names
    modules = {item["id"]: item for items in groups.values() for item in items}
    implemented = {
        module_id
        for module_id, item in modules.items()
        if item.get("implemented")
    }
    assert implemented == {
        "asset-inventory",
        "metadata-lineage",
        "quality",
        "warehouse-modeling",
        "ontology-modeling",
        "log-query",
        "user-center",
        "user-permissions",
        "datasource-config",
    }
    assert all(
        not item.get("implemented")
        for module_id, item in modules.items()
        if module_id not in implemented
    )


async def test_viewer_can_use_platform_but_cannot_manage_users(client: AsyncClient) -> None:
    await login_as(client, "viewer")
    assert (await client.get(f"{API}/platform/modules")).status_code == 200
    assert (await client.get(f"{API}/auth/me")).status_code == 200

    response = await client.get(f"{API}/users")
    assert response.status_code == 403
    response = await client.post(
        f"{API}/users",
        json={"username": "forbidden", "display_name": "Forbidden", "role": "admin"},
    )
    assert response.status_code == 403


async def test_governance_role_cannot_manage_users(client: AsyncClient) -> None:
    await login_as(client, "governance")
    response = await client.get(f"{API}/users")
    assert response.status_code == 403


async def test_admin_creates_user_with_default_password(client: AsyncClient) -> None:
    created = await create_user(client, "new.analyst", "governance")
    assert created["role"] == "governance"
    assert created["is_active"] is True

    payload = await login_as(client, "new.analyst")
    assert payload["user"]["display_name"] == "测试 new.analyst"
    assert "governance:write" in payload["user"]["permissions"]

    await login_as(client, "admin")
    response = await client.post(
        f"{API}/users",
        json={"username": "new.analyst", "display_name": "Duplicate", "role": "viewer"},
    )
    assert response.status_code == 409


async def test_admin_updates_role_and_permissions(client: AsyncClient) -> None:
    created = await create_user(client, "role.change")
    response = await client.patch(f"{API}/users/{created['id']}", json={"role": "governance"})
    assert response.status_code == 200
    assert response.json()["role"] == "governance"

    payload = await login_as(client, "role.change")
    assert "config:write" in payload["user"]["permissions"]
    assert "users:manage" not in payload["user"]["permissions"]


async def test_disabled_user_session_is_revoked(client: AsyncClient) -> None:
    created = await create_user(client, "disabled.user")
    user_login = await login_as(client, "disabled.user")
    user_token = user_login["token"]

    await login_as(client, "admin")
    response = await client.patch(f"{API}/users/{created['id']}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    client.headers.update({"Authorization": f"Bearer {user_token}"})
    assert (await client.get(f"{API}/auth/me")).status_code == 401
    response = await client.post(
        f"{API}/auth/login", json={"username": "disabled.user", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 401


async def test_reset_password_restores_default_and_revokes_sessions(client: AsyncClient) -> None:
    created = await create_user(client, "reset.user")
    user_login = await login_as(client, "reset.user")
    user_token = user_login["token"]
    response = await client.post(
        f"{API}/auth/password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a-new-password-123"},
    )
    assert response.status_code == 200

    await login_as(client, "reset.user", "a-new-password-123")
    await login_as(client, "admin")
    response = await client.post(f"{API}/users/{created['id']}/reset-password")
    assert response.status_code == 200
    assert DEFAULT_PASSWORD in response.json()["detail"]

    client.headers.update({"Authorization": f"Bearer {user_token}"})
    assert (await client.get(f"{API}/auth/me")).status_code == 401
    await login_as(client, "reset.user")


async def test_user_changes_password_and_old_sessions_are_revoked(client: AsyncClient) -> None:
    payload = await login_as(client, "viewer")
    old_token = payload["token"]
    response = await client.post(
        f"{API}/auth/password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "viewer-new-password"},
    )
    assert response.status_code == 200

    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 401
    client.headers.update({"Authorization": f"Bearer {old_token}"})
    assert (await client.get(f"{API}/platform/modules")).status_code == 401

    response = await client.post(
        f"{API}/auth/login", json={"username": "viewer", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 401
    await login_as(client, "viewer", "viewer-new-password")


async def test_password_change_validates_current_password(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/password",
        json={"current_password": "wrong-password", "new_password": "new-password-123"},
    )
    assert response.status_code == 400

    response = await client.post(
        f"{API}/auth/password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "short"},
    )
    assert response.status_code == 422


async def test_admin_cannot_disable_or_downgrade_own_account(client: AsyncClient) -> None:
    me = (await client.get(f"{API}/auth/me")).json()
    response = await client.patch(f"{API}/users/{me['id']}", json={"is_active": False})
    assert response.status_code == 409
    response = await client.patch(f"{API}/users/{me['id']}", json={"role": "viewer"})
    assert response.status_code == 409
