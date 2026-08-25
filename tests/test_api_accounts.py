"""Account CRUD endpoint tests."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from application.account_exports import AccountExportsService
from core.base_platform import Account
from core.db import save_account
from domain.accounts import AccountExportSelection, AccountQuery
from infrastructure.accounts_repository import AccountsRepository


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def _create_account(**overrides):
    payload = {
        "platform": "chatgpt",
        "email": "test@example.com",
        "password": "TestPass123!",
        **overrides,
    }
    save_account(Account(**payload))
    _, records = AccountsRepository().list(
        AccountQuery(platform=payload["platform"], email=payload["email"])
    )
    return records[0].id


def test_save_account_returns_model_with_loaded_attributes_after_session_close():
    created = save_account(
        Account(
            platform="chatgpt",
            email="detached-model@test.com",
            password="FirstPass123!",
            user_id="acct-detached",
            extra={"access_token": "access-token"},
        )
    )

    created_id = int(created.id)
    assert created_id > 0
    assert created.email == "detached-model@test.com"

    updated = save_account(
        Account(
            platform="chatgpt",
            email="detached-model@test.com",
            password="SecondPass123!",
            user_id="acct-detached",
            extra={"access_token": "updated-access-token"},
        )
    )

    assert int(updated.id) == created_id
    assert updated.email == "detached-model@test.com"
    assert updated.password == "SecondPass123!"


def test_list_accounts_empty(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_accounts_after_create(client):
    _create_account()
    resp = client.get("/api/accounts")
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["email"] == "test@example.com"


def test_get_account_by_id(client):
    account_id = _create_account()
    resp = client.get(f"/api/accounts/{account_id}")
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


def test_get_account_not_found(client):
    resp = client.get("/api/accounts/99999")
    assert resp.status_code == 404


def test_delete_account(client):
    account_id = _create_account()
    del_resp = client.delete(f"/api/accounts/{account_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True
    # Verify it's gone
    get_resp = client.get(f"/api/accounts/{account_id}")
    assert get_resp.status_code == 404


def test_update_account(client):
    account_id = _create_account()
    patch_resp = client.patch(
        f"/api/accounts/{account_id}",
        json={"password": "NewPass456!"},
    )
    assert patch_resp.status_code == 200


def test_filter_accounts_by_platform(client):
    _create_account(platform="chatgpt", email="a@test.com")
    _create_account(platform="cursor", email="b@test.com")
    resp = client.get("/api/accounts", params={"platform": "cursor"})
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["platform"] == "cursor"


def test_filter_accounts_by_refresh_token(client):
    _create_account(
        email="with-rt@test.com",
        extra={"access_token": "access-with-rt", "refresh_token": "refresh-token"},
    )
    _create_account(
        email="without-rt@test.com",
        extra={"access_token": "access-without-rt"},
    )

    with_rt = client.get("/api/accounts", params={"has_refresh_token": "true"})
    without_rt = client.get("/api/accounts", params={"has_refresh_token": "false"})

    assert with_rt.status_code == 200
    assert with_rt.json()["total"] == 1
    assert with_rt.json()["items"][0]["email"] == "with-rt@test.com"
    assert with_rt.json()["items"][0]["has_refresh_token"] is True
    assert without_rt.status_code == 200
    assert without_rt.json()["total"] == 1
    assert without_rt.json()["items"][0]["email"] == "without-rt@test.com"
    assert without_rt.json()["items"][0]["has_refresh_token"] is False


def test_export_any2api_multi_platform(client):
    _create_account(platform="kiro", email="k@test.com", password="")
    _create_account(platform="grok", email="g@test.com", password="")
    _create_account(platform="cursor", email="c@test.com", password="")
    resp = client.post("/api/accounts/export/any2api", json={"select_all": True})
    assert resp.status_code == 200
    assert "any2api_admin" in resp.headers.get("content-disposition", "")


def test_export_cpa_uses_standard_payload_schema():
    exp_timestamp = 1777166030
    expected_expired = datetime.fromtimestamp(
        exp_timestamp, tz=timezone(timedelta(hours=8))
    ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    access_token = _make_jwt({
        "exp": exp_timestamp,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct-standard",
        },
    })
    id_token = _make_jwt({
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct-standard",
        },
    })
    repository = AccountsRepository()
    save_account(
        Account(
            platform="chatgpt",
            email="cpa@test.com",
            password="TestPass123!",
            user_id="acct-standard",
            extra={
                "access_token": access_token,
                "refresh_token": "rt_standard",
                "id_token": id_token,
            },
        )
    )
    service = AccountExportsService(repository)

    artifact = service.export_chatgpt_cpa(AccountExportSelection(platform="chatgpt", select_all=True))
    payload = json.loads(artifact.content)
    assert list(payload.keys()) == [
        "access_token",
        "account_id",
        "email",
        "expired",
        "id_token",
        "last_refresh",
        "refresh_token",
        "type",
    ]
    assert payload["access_token"] == access_token
    assert payload["account_id"] == "acct-standard"
    assert payload["email"] == "cpa@test.com"
    assert payload["expired"] == expected_expired
    assert payload["id_token"] == id_token
    assert payload["last_refresh"].endswith("+08:00")
    assert payload["refresh_token"] == "rt_standard"
    assert payload["type"] == "codex"


def test_export_cpa_falls_back_to_stored_user_id_for_account_id():
    repository = AccountsRepository()
    save_account(
        Account(
            platform="chatgpt",
            email="fallback@test.com",
            password="TestPass123!",
            user_id="acct-from-user-id",
            extra={
                "access_token": _make_jwt({"exp": 1777166030}),
                "refresh_token": "rt_fallback",
            },
        )
    )
    service = AccountExportsService(repository)

    artifact = service.export_chatgpt_cpa(AccountExportSelection(platform="chatgpt", select_all=True))
    payload = json.loads(artifact.content)
    assert payload["account_id"] == "acct-from-user-id"
    assert payload["refresh_token"] == "rt_fallback"
