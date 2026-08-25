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

    class FakeLogin:
        def __init__(self, **kwargs):
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

    monkeypatch.setattr(
        "application.sub2api_oauth.require_sub2api_configured",
        lambda: type("S", (), {"configured": True})(),
    )
    monkeypatch.setattr("application.sub2api_oauth.client_from_settings", lambda settings: FakeClient())
    monkeypatch.setattr("application.sub2api_oauth.Sub2ApiCodexLogin", FakeLogin)

    result = authorize_chatgpt_account_to_sub2api(account_id, login_factory=FakeLogin, client=FakeClient())
    assert result["sub2_account_id"] == "77"

    listed = AccountsService().list_accounts(AccountQuery(platform="chatgpt", email="oauth@example.com"))
    item = listed["items"][0]
    assert item["sub2api_authorized"] is True
    assert item["sub2api_authorize_status"] == "idle"

    logger = _FakeLogger()
    monkeypatch.setattr(
        "application.sub2api_oauth.authorize_chatgpt_account_to_sub2api",
        lambda *args, **kwargs: {"sub2_account_id": "77"},
    )
    _execute_sub2api_oauth_task({"account_id": account_id}, logger)
    assert logger.finished == (TASK_STATUS_SUCCEEDED, "")


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
