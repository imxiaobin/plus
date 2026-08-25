from __future__ import annotations

from application.accounts import AccountsService
from application.sub2api_oauth import authorize_chatgpt_account_to_sub2api
from application.tasks import TASK_STATUS_SUCCEEDED, TASK_TYPE_SUB2API_OAUTH, _execute_sub2api_oauth_task
from core.base_platform import Account
from core.db import save_account
from domain.accounts import AccountQuery, AccountUpdateCommand
from infrastructure.accounts_repository import AccountsRepository
from tests.test_platform_action_task import _FakeLogger


def _create_account(**overrides):
    payload = {
        "platform": "chatgpt",
        "email": "oauth@example.com",
        "password": "Secret123!",
        **overrides,
    }
    save_account(Account(**payload))
    _, records = AccountsRepository().list(
        AccountQuery(platform=payload["platform"], email=payload["email"])
    )
    return records[0].id


def test_config_masks_api_key_and_empty_put_keeps_existing(client):
    saved = client.put(
        "/api/config",
        json={
            "data": {
                "sub2api_url": "http://127.0.0.1:8080",
                "sub2api_api_key": "super-secret",
                "sub2api_concurrency": "5",
            }
        },
    )
    assert saved.status_code == 200
    loaded = client.get("/api/config")
    assert loaded.status_code == 200
    data = loaded.json()
    assert data["sub2api_url"] == "http://127.0.0.1:8080"
    assert data["sub2api_api_key"] == ""
    assert data["sub2api_api_key_configured"] == "1"
    assert data["sub2api_concurrency"] == "5"

    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": ""}},
    )
    from infrastructure.config_repository import ConfigRepository

    stored = ConfigRepository().get_flat()
    assert stored["sub2api_api_key"] == "super-secret"


def test_config_lists_sub2api_groups(client, monkeypatch):
    saved = client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )
    assert saved.status_code == 200
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_groups",
        lambda self, platform="openai": [
            {"id": "1", "name": "Codex", "platform": "openai", "status": "active"}
        ],
    )
    response = client.post("/api/config/sub2api/groups", json={})
    assert response.status_code == 200
    assert response.json()["items"] == [
        {"id": "1", "name": "Codex", "platform": "openai", "status": "active"}
    ]


def test_config_lists_sub2api_models(client, monkeypatch):
    saved = client.put(
        "/api/config",
        json={
            "data": {
                "sub2api_url": "http://127.0.0.1:8080",
                "sub2api_api_key": "k",
                "sub2api_group_ids": "2",
                "sub2api_models": "gpt-5.4,gpt-5",
                "sub2api_model_mapping": '{"gpt-5":"gpt-5.4"}',
            }
        },
    )
    assert saved.status_code == 200
    loaded = client.get("/api/config")
    assert loaded.json()["sub2api_models"] == "gpt-5.4,gpt-5"
    assert loaded.json()["sub2api_model_mapping"] == '{"gpt-5":"gpt-5.4"}'
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_models",
        lambda self, group_id=0, platform="openai": ["gpt-5.4", "gpt-5.4-mini"],
    )
    response = client.post("/api/config/sub2api/models", json={"group_id": 2})
    assert response.status_code == 200
    assert response.json()["items"] == ["gpt-5.4", "gpt-5.4-mini"]


def test_client_from_settings_combines_models_and_mapping():
    from application.sub2api_oauth import Sub2ApiSettings, client_from_settings

    client = client_from_settings(
        Sub2ApiSettings(
            base_url="http://127.0.0.1:8080",
            api_key="k",
            models=("gpt-5.4", "gpt-5"),
            model_mapping=(("gpt-5", "gpt-5.4"),),
        )
    )
    assert client.model_mapping == {"gpt-5.4": "gpt-5.4", "gpt-5": "gpt-5.4"}


def test_monitor_joins_local_authorized_accounts_with_sub2_status(client, monkeypatch):
    account_id = _create_account()
    AccountsService().update_account(
        account_id,
        AccountUpdateCommand(overview={"sub2_account_id": "88", "sub2api_authorized_at": "2026-08-25T00:00:00Z"}),
    )
    saved = client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )
    assert saved.status_code == 200
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_accounts",
        lambda self, **kwargs: (
            [
                {
                    "id": 88,
                    "name": "oauth@example.com",
                    "status": "active",
                    "schedulable": True,
                    "current_concurrency": 1,
                    "concurrency": 3,
                    "groups": [{"id": 2, "name": "codex"}],
                    "credentials": {"model_mapping": {"gpt-5.4": "gpt-5.4"}},
                    "extra": {"codex_5h_used_percent": 12},
                    "last_used_at": "2026-08-25T12:00:00Z",
                    "error_message": "",
                }
            ],
            1,
        ),
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.batch_today_stats",
        lambda self, ids: {"88": {"requests": 4, "tokens": 20, "cost": 0.5}},
    )
    response = client.get("/api/config/sub2api/monitor")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["available"] == 1
    assert payload["summary"]["in_use"] == 1
    item = payload["items"][0]
    assert item["email"] == "oauth@example.com"
    assert item["sub2_account_id"] == "88"
    assert item["availability"] == "available"
    assert item["groups"][0]["name"] == "codex"
    assert item["models"] == ["gpt-5.4"]
    assert item["today_requests"] == 4


def test_monitor_marks_missing_sub2_account(client, monkeypatch):
    account_id = _create_account(email="gone@example.com")
    AccountsService().update_account(
        account_id,
        AccountUpdateCommand(overview={"sub2_account_id": "99"}),
    )
    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_accounts",
        lambda self, **kwargs: ([], 0),
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.get_account",
        lambda self, account_id: None,
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.batch_today_stats",
        lambda self, ids: {},
    )
    response = client.get("/api/config/sub2api/monitor")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["availability"] == "missing"
    assert item["sub2_found"] is False
    assert item["can_reauthorize"] is True


def test_authorize_requires_sub2api_settings(client):
    account_id = _create_account()
    response = client.post(f"/api/accounts/{account_id}/authorize/sub2api")
    assert response.status_code == 400
    assert "请先在设置中填写 Sub2API" in response.json()["detail"]


def test_authorize_creates_sub2api_oauth_task(client, monkeypatch):
    account_id = _create_account()
    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )
    monkeypatch.setattr("api.accounts.task_runtime.wake_up", lambda: None)
    monkeypatch.setattr(
        "api.accounts.create_sub2api_oauth_task",
        lambda value: {"task_id": "task_sub2", "type": TASK_TYPE_SUB2API_OAUTH, "account_id": value},
    )
    response = client.post(f"/api/accounts/{account_id}/authorize/sub2api")
    assert response.status_code == 200
    assert response.json()["type"] == TASK_TYPE_SUB2API_OAUTH


def test_list_item_exposes_sub2api_authorization_state(client):
    account_id = _create_account()
    AccountsService().update_account(
        account_id,
        AccountUpdateCommand(overview={"sub2_account_id": "42", "sub2api_authorize_status": "idle"}),
    )
    response = client.get("/api/accounts")
    item = response.json()["items"][0]
    assert item["sub2api_authorized"] is True
    assert item["sub2api_authorize_status"] == "idle"


def test_sub2api_oauth_task_writes_sub2_account_id(monkeypatch):
    account_id = _create_account(extra={"totp_secret": "JBSWY3DPEHPK3PXP"})
    login_kwargs = {}

    class FakeLogin:
        def __init__(self, **kwargs):
            login_kwargs.update(kwargs)
            self.kwargs = kwargs

        def login_to_callback(self, **kwargs):
            assert "/oauth/token" not in kwargs.get("auth_url", "")
            return "http://localhost:1455/auth/callback?code=ac_ok&state=st_ok"

    class FakeClient:
        concurrency = 3
        priority = 50
        group_ids = []

        def generate_auth_url(self):
            return {
                "auth_url": "https://auth.openai.com/oauth/authorize?state=st_ok",
                "session_id": "sess-ok",
                "state": "st_ok",
            }

        def exchange_code(self, **kwargs):
            assert kwargs["code"] == "ac_ok"
            return {"access_token": "at", "refresh_token": "rt", "id_token": "id"}

        def create_oauth_account(self, tokens, name=""):
            assert tokens["refresh_token"] == "rt"
            return {"data": {"id": 77, "type": "oauth"}}

        def get_account(self, account_id):
            return None

    monkeypatch.setattr(
        "application.sub2api_oauth.require_sub2api_configured",
        lambda: type("S", (), {"configured": True})(),
    )
    monkeypatch.setattr("application.sub2api_oauth.client_from_settings", lambda settings: FakeClient())
    monkeypatch.setattr("application.sub2api_oauth.Sub2ApiCodexLogin", FakeLogin)

    result = authorize_chatgpt_account_to_sub2api(
        account_id,
        login_factory=FakeLogin,
        client=FakeClient(),
        proxy="http://pool.example:8080",
        proxy_rotate_callback=lambda: "http://pool.example:8080",
    )
    assert result["sub2_account_id"] == "77"
    assert result["reauthorized"] is False
    assert login_kwargs["proxy"] == "http://pool.example:8080"
    assert callable(login_kwargs["proxy_rotate_callback"])

    listed = AccountsService().list_accounts(AccountQuery(platform="chatgpt", email="oauth@example.com"))
    item = listed["items"][0]
    assert item["sub2api_authorized"] is True
    assert item["sub2api_authorize_status"] == "idle"

    logger = _FakeLogger()
    captured = {}

    def fake_resolve(*_args, **kwargs):
        captured["prefer_http_pool"] = kwargs.get("prefer_http_pool")
        return "http://pool.example:8080"

    def fake_authorize(*_args, **kwargs):
        captured["proxy"] = kwargs.get("proxy")
        captured["rotate"] = kwargs.get("proxy_rotate_callback")
        return {"sub2_account_id": "77"}

    monkeypatch.setattr("application.tasks._resolve_refresh_login_proxy", fake_resolve)
    monkeypatch.setattr("core.proxy_pool.proxy_pool.active_count", lambda: 2)
    monkeypatch.setattr(
        "application.sub2api_oauth.authorize_chatgpt_account_to_sub2api",
        fake_authorize,
    )
    _execute_sub2api_oauth_task({"account_id": account_id}, logger)
    assert logger.finished == (TASK_STATUS_SUCCEEDED, "")
    assert captured["prefer_http_pool"] is True
    assert captured["proxy"] == "http://pool.example:8080"
    assert callable(captured["rotate"])


def test_existing_sub2_account_is_reauthorized_instead_of_created(monkeypatch):
    account_id = _create_account(email="reauth@example.com")
    AccountsService().update_account(
        account_id,
        AccountUpdateCommand(overview={"sub2_account_id": "88"}),
    )
    applied = []

    class FakeLogin:
        def __init__(self, **kwargs):
            pass

        def login_to_callback(self, **kwargs):
            return "http://localhost:1455/auth/callback?code=ac_ok&state=st_ok"

    class FakeClient:
        def generate_auth_url(self):
            return {
                "auth_url": "https://auth.openai.com/oauth/authorize?state=st_ok",
                "session_id": "sess-ok",
                "state": "st_ok",
            }

        def exchange_code(self, **kwargs):
            return {"access_token": "at", "refresh_token": "rt", "id_token": "id"}

        def get_account(self, account_id):
            assert account_id == 88
            return {"id": 88, "status": "error"}

        def apply_oauth_credentials(self, account_id, tokens):
            applied.append((account_id, tokens))
            return {"data": {"id": 88, "type": "oauth"}}

        def create_oauth_account(self, tokens, name=""):
            raise AssertionError("re-authorize must not create a new Sub2 account")

    monkeypatch.setattr(
        "application.sub2api_oauth.require_sub2api_configured",
        lambda: type("S", (), {"configured": True})(),
    )
    result = authorize_chatgpt_account_to_sub2api(
        account_id,
        login_factory=FakeLogin,
        client=FakeClient(),
    )
    assert result["sub2_account_id"] == "88"
    assert result["reauthorized"] is True
    assert applied[0][0] == 88
    assert applied[0][1]["refresh_token"] == "rt"
    assert applied[0][1]["access_token"] == "at"


def test_add_email_without_mailbox_pool_has_clear_error(monkeypatch):
    from platforms.chatgpt.sub2api_codex_login import Sub2ApiLoginError, lease_mailbox_for_add_email

    monkeypatch.setattr(
        "infrastructure.provider_definitions_repository.ProviderDefinitionsRepository.list_by_type",
        lambda self, provider_type, enabled_only=False: [],
    )
    try:
        lease_mailbox_for_add_email()
    except Sub2ApiLoginError as exc:
        assert "邮箱池" in str(exc)
    else:
        raise AssertionError("missing mailbox pool should fail clearly")


def _authorize_local_account(account_id: int, sub2_id: str = "88"):
    AccountsService().update_account(
        account_id,
        AccountUpdateCommand(overview={"sub2_account_id": sub2_id, "sub2api_authorized_at": "2026-08-25T00:00:00Z"}),
    )


def test_sol_terra_mapping_preview_and_enable_keeps_token(client, monkeypatch):
    account_id = _create_account()
    _authorize_local_account(account_id, "88")
    plus_id = _create_account(email="plus@example.com")
    _authorize_local_account(plus_id, "99")
    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )
    puts = []

    def fake_get(self, account_id):
        if int(account_id) == 88:
            return {
                "id": 88,
                "status": "active",
                "name": "oauth@example.com",
                "credentials": {
                    "plan_type": "free",
                    "access_token": "tok",
                    "model_mapping": {"gpt-5.6-sol": "gpt-5.6-sol", "gpt-5.6": "gpt-5.6"},
                },
            }
        return {
            "id": 99,
            "status": "active",
            "name": "plus@example.com",
            "credentials": {"plan_type": "plus", "access_token": "plus-tok"},
        }

    def fake_put(self, account_id, credentials):
        puts.append({"id": int(account_id), "credentials": dict(credentials)})
        return {"data": {"id": account_id}}

    monkeypatch.setattr("platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.get_account", fake_get)
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.update_account_credentials",
        fake_put,
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_all_accounts",
        lambda self, **kwargs: [],
    )
    preview = client.get("/api/config/sub2api/sol-terra-mapping")
    assert preview.status_code == 200
    assert preview.json()["total"] == 1
    assert preview.json()["items"][0]["sub2_account_id"] == "88"

    applied = client.post("/api/config/sub2api/sol-terra-mapping", json={"enable": True})
    assert applied.status_code == 200
    payload = applied.json()
    assert payload["updated"] == 1
    assert payload["failed"] == 0
    assert puts[0]["id"] == 88
    assert puts[0]["credentials"]["access_token"] == "tok"
    assert puts[0]["credentials"]["model_mapping"]["gpt-5.6-sol"] == "gpt-5.6-terra"
    assert puts[0]["credentials"]["model_mapping"]["gpt-5.6"] == "gpt-5.6"


def test_sol_terra_mapping_includes_unmarked_active_accounts(client, monkeypatch):
    account_id = _create_account(email="unmarked@example.com")
    _authorize_local_account(account_id, "77")
    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )

    def fake_get(self, account_id):
        return {"id": 77, "status": "active", "name": "unmarked@example.com", "credentials": {"access_token": "tok"}}

    monkeypatch.setattr("platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.get_account", fake_get)
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_all_accounts",
        lambda self, **kwargs: [],
    )
    preview = client.get("/api/config/sub2api/sol-terra-mapping")
    assert preview.status_code == 200
    assert preview.json()["total"] == 1
    assert preview.json()["items"][0]["sub2_account_id"] == "77"


def test_sol_terra_mapping_disable_skips_unchanged(client, monkeypatch):
    account_id = _create_account()
    _authorize_local_account(account_id, "88")
    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )

    def fake_get(self, account_id):
        return {
            "id": 88,
            "status": "active",
            "credentials": {
                "plan_type": "free",
                "model_mapping": {"gpt-5.6-sol": "gpt-5.6-sol"},
            },
        }

    def fake_put(self, account_id, credentials):
        raise AssertionError("should not PUT when mapping already cancelled")

    monkeypatch.setattr("platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.get_account", fake_get)
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.update_account_credentials",
        fake_put,
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_all_accounts",
        lambda self, **kwargs: [],
    )
    response = client.post("/api/config/sub2api/sol-terra-mapping", json={"enable": False})
    assert response.status_code == 200
    payload = response.json()
    assert payload["updated"] == 0
    assert payload["skipped"] == 1
    assert payload["success"] == 1


def test_sol_terra_mapping_includes_all_active_free_accounts_on_sub2(client, monkeypatch):
    client.put(
        "/api/config",
        json={"data": {"sub2api_url": "http://127.0.0.1:8080", "sub2api_api_key": "k"}},
    )
    puts = []
    extra_free = {
        "id": 201,
        "status": "active",
        "name": "extra-free",
        "credentials": {
            "plan_type": "free",
            "access_token": "tok-201",
            "model_mapping": {"gpt-5.6-sol": "gpt-5.6-sol"},
        },
    }

    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.list_all_accounts",
        lambda self, **kwargs: [
            extra_free,
            {"id": 202, "status": "active", "name": "unmarked", "credentials": {"access_token": "tok-202"}},
            {"id": 203, "status": "error", "name": "dead-free", "credentials": {"plan_type": "free"}},
        ],
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.get_account",
        lambda self, account_id: extra_free if int(account_id) == 201 else None,
    )
    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_oauth.Sub2ApiOAuthClient.update_account_credentials",
        lambda self, account_id, credentials: puts.append({"id": int(account_id), "credentials": dict(credentials)}) or {"data": {"id": account_id}},
    )
    preview = client.get("/api/config/sub2api/sol-terra-mapping")
    assert preview.status_code == 200
    assert preview.json()["total"] == 1
    assert preview.json()["items"][0]["sub2_account_id"] == "201"

    applied = client.post("/api/config/sub2api/sol-terra-mapping", json={"enable": True})
    assert applied.status_code == 200
    assert applied.json()["updated"] == 1
    assert puts[0]["id"] == 201
    assert puts[0]["credentials"]["model_mapping"]["gpt-5.6-sol"] == "gpt-5.6-terra"
    assert puts[0]["credentials"]["access_token"] == "tok-201"
