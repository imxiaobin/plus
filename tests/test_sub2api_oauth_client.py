from __future__ import annotations

from platforms.chatgpt.sub2api_oauth import (
    CREATE_ACCOUNT_PATH,
    DEFAULT_OPENAI_MODELS,
    EXCHANGE_CODE_PATH,
    GENERATE_AUTH_URL_PATH,
    GET_ACCOUNT_PATH,
    LIST_ACCOUNTS_PATH,
    LIST_GROUPS_ALL_PATH,
    LIST_MODELS_CANDIDATES_PATH,
    UPDATE_ACCOUNT_PATH,
    Sub2ApiError,
    Sub2ApiOAuthClient,
    availability_from_sub2api_account,
    build_sub2api_model_mapping,
    extract_sub2api_account_id,
    extract_sub2api_account_list,
    extract_sub2api_groups,
    extract_sub2api_models,
    join_sub2api_url,
    is_explicit_free_sub2_account,
    is_sol_terra_free_target,
    model_mapping_from_sub2api_account,
    models_from_sub2api_account,
    patch_sol_terra_model_mapping,
    state_from_auth_url,
    tokens_from_exchange,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or ""

    def json(self):
        return self._payload


def test_join_sub2api_url_appends_admin_paths():
    assert (
        join_sub2api_url("http://127.0.0.1:8080", GENERATE_AUTH_URL_PATH)
        == "http://127.0.0.1:8080/api/v1/admin/openai/generate-auth-url"
    )
    assert (
        join_sub2api_url("http://127.0.0.1:8080/api/v1", EXCHANGE_CODE_PATH)
        == "http://127.0.0.1:8080/api/v1/admin/openai/exchange-code"
    )


def test_generate_exchange_and_create_send_expected_headers_and_body():
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "json": json,
                "timeout": timeout,
            }
        )
        if url.endswith("generate-auth-url"):
            return FakeResponse(
                payload={
                    "data": {
                        "auth_url": "https://auth.openai.com/oauth/authorize?state=st_test",
                        "session_id": "sess-1234567890",
                        "state": "st_test",
                    }
                }
            )
        if url.endswith("exchange-code"):
            return FakeResponse(
                payload={
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "id_token": "id-1",
                    "email": "user@example.com",
                }
            )
        return FakeResponse(payload={"data": {"id": 88, "type": "oauth"}})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        concurrency=4,
        priority=9,
        group_ids=[1, 2],
        request_fn=fake_request,
    )
    session = client.generate_auth_url()
    exchanged = client.exchange_code(session_id=session["session_id"], code="ac_code", state="st_test")
    created = client.create_oauth_account(tokens_from_exchange(exchanged), name="user@example.com")

    assert session == {
        "auth_url": "https://auth.openai.com/oauth/authorize?state=st_test",
        "session_id": "sess-1234567890",
        "state": "st_test",
    }
    assert extract_sub2api_account_id(created) == "88"
    assert [item["url"] for item in calls] == [
        "http://127.0.0.1:8080" + GENERATE_AUTH_URL_PATH,
        "http://127.0.0.1:8080" + EXCHANGE_CODE_PATH,
        "http://127.0.0.1:8080" + CREATE_ACCOUNT_PATH,
    ]
    assert all(item["headers"]["X-API-Key"] == "admin-secret" for item in calls)
    assert calls[1]["json"] == {
        "session_id": "sess-1234567890",
        "code": "ac_code",
        "state": "st_test",
    }
    body = calls[2]["json"]
    assert body["platform"] == "openai"
    assert body["type"] == "oauth"
    assert body["credentials"]["access_token"] == "at-1"
    assert body["credentials"]["refresh_token"] == "rt-1"
    assert body["concurrency"] == 4
    assert body["priority"] == 9
    assert body["group_ids"] == [1, 2]
    assert "model_mapping" not in body["credentials"]


def test_generate_auth_url_reads_state_from_auth_url_query():
    def fake_request(method, url, headers=None, json=None, timeout=None):
        return FakeResponse(
            payload={
                "data": {
                    "auth_url": "https://auth.openai.com/oauth/authorize?client_id=app_EMoamEEZ73f0CkXaXp7hrann&state=st_from_url",
                    "session_id": "sess-no-state-field",
                }
            }
        )

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    session = client.generate_auth_url()
    assert session["session_id"] == "sess-no-state-field"
    assert session["state"] == "st_from_url"
    assert state_from_auth_url(session["auth_url"]) == "st_from_url"


def test_extract_and_list_groups_from_sub2_payload():
    assert extract_sub2api_groups(
        {
            "data": [
                {"id": 1, "name": "Codex", "platform": "openai", "status": "active"},
                {"id": 2, "name": "Claude", "platform": "anthropic"},
            ]
        }
    ) == [
        {"id": "1", "name": "Codex", "platform": "openai", "status": "active"},
        {"id": "2", "name": "Claude", "platform": "anthropic", "status": ""},
    ]

    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        calls.append({"method": method, "url": url, "params": params, "json": json})
        return FakeResponse(
            payload={
                "data": [
                    {"id": 1, "name": "Codex", "platform": "openai", "status": "active"},
                    {"id": 2, "name": "Claude", "platform": "anthropic", "status": "active"},
                ]
            }
        )

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    groups = client.list_groups()
    assert groups == [{"id": "1", "name": "Codex", "platform": "openai", "status": "active"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith(LIST_GROUPS_ALL_PATH)
    assert calls[0]["json"] is None
    assert calls[0]["params"] == {"platform": "openai"}


def test_build_and_create_account_model_mapping():
    assert build_sub2api_model_mapping(["gpt-5.4", "gpt-5"], {"gpt-5": "gpt-5.4"}) == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5": "gpt-5.4",
    }
    assert build_sub2api_model_mapping([], {}) == {}

    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        calls.append(json)
        return FakeResponse(payload={"data": {"id": 9}})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        model_mapping=build_sub2api_model_mapping(["gpt-5.4"], {"gpt-5": "gpt-5.4"}),
        request_fn=fake_request,
    )
    client.create_oauth_account(
        {"access_token": "at-1", "refresh_token": "rt-1", "id_token": "id-1"},
        name="user@example.com",
    )
    assert calls[0]["credentials"]["model_mapping"] == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5": "gpt-5.4",
    }


def test_extract_and_list_models_from_sub2_payload():
    assert extract_sub2api_models(
        {"data": {"models": [{"id": "gpt-5.4"}, {"name": "gpt-5.4-mini"}, "gpt-5.4"]}}
    ) == ["gpt-5.4", "gpt-5.4-mini"]

    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        calls.append({"method": method, "url": url, "params": params})
        return FakeResponse(payload={"data": ["gpt-5.4", "gpt-5.4-mini"]})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    models = client.list_models(group_id=2)
    assert models == ["gpt-5.4", "gpt-5.4-mini"]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith(LIST_MODELS_CANDIDATES_PATH.format(group_id=2))
    assert calls[0]["params"] == {"platform": "openai"}


def test_list_models_falls_back_to_defaults():
    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        return FakeResponse(status_code=404, payload={"detail": "missing"})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    assert client.list_models() == list(DEFAULT_OPENAI_MODELS)


def test_client_rejects_incomplete_config():
    try:
        Sub2ApiOAuthClient(base_url="", api_key="k")
    except Sub2ApiError as exc:
        assert "请先在设置中填写 Sub2API" in str(exc)
    else:
        raise AssertionError("empty URL should fail")


def test_extract_account_list_models_and_availability():
    items, total = extract_sub2api_account_list(
        {
            "data": {
                "items": [
                    {
                        "id": 88,
                        "status": "active",
                        "schedulable": True,
                        "credentials": {"model_mapping": {"gpt-5": "gpt-5.4", "gpt-5.4": "gpt-5.4"}},
                    }
                ],
                "total": 1,
            }
        }
    )
    assert total == 1
    assert items[0]["id"] == 88
    assert models_from_sub2api_account(items[0]) == ["gpt-5", "gpt-5.4"]
    assert availability_from_sub2api_account(items[0]) == "available"
    assert availability_from_sub2api_account({"status": "error"}) == "error"
    assert availability_from_sub2api_account(None) == "missing"


def test_list_and_get_account_and_today_stats():
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        calls.append({"method": method, "url": url, "params": params, "json": json})
        if url.endswith("/admin/accounts") and method == "GET":
            return FakeResponse(payload={"data": {"items": [{"id": 88, "status": "active"}], "total": 1}})
        if url.endswith("/admin/accounts/88"):
            return FakeResponse(payload={"data": {"id": 88, "status": "active", "name": "user@example.com"}})
        if url.endswith("/today-stats/batch"):
            return FakeResponse(payload={"data": {"stats": {"88": {"requests": 3, "tokens": 10, "cost": 0.2}}}})
        return FakeResponse(status_code=404, payload={"detail": "no"})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    items, total = client.list_accounts()
    assert total == 1
    assert items[0]["id"] == 88
    assert calls[0]["url"].endswith(LIST_ACCOUNTS_PATH)
    assert calls[0]["params"]["platform"] == "openai"
    assert client.get_account(88)["name"] == "user@example.com"
    assert client.batch_today_stats([88])["88"]["requests"] == 3
    assert GET_ACCOUNT_PATH.format(account_id=88) in calls[1]["url"]


def test_apply_oauth_credentials_posts_to_existing_account():
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        calls.append({"method": method, "url": url, "json": json})
        return FakeResponse(payload={"data": {"id": 88, "type": "oauth"}})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    payload = client.apply_oauth_credentials(
        88,
        {"access_token": "at-2", "refresh_token": "rt-2", "id_token": "id-2"},
    )
    assert extract_sub2api_account_id(payload) == "88"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/api/v1/admin/accounts/88/apply-oauth-credentials")
    assert calls[0]["json"]["type"] == "oauth"
    assert calls[0]["json"]["credentials"]["refresh_token"] == "rt-2"
    assert "model_mapping" not in calls[0]["json"]["credentials"]


def test_patch_sol_terra_and_free_account_helpers():
    mapping = {"gpt-5.6-sol": "gpt-5.6-sol", "gpt-5.6": "gpt-5.6"}
    enabled = patch_sol_terra_model_mapping(mapping, enable=True)
    assert enabled["gpt-5.6-sol"] == "gpt-5.6-terra"
    assert enabled["gpt-5.6"] == "gpt-5.6"
    fallback = {"gpt-5.6-sol": "gpt-5.6-sol", "gpt-5.6-terra": "gpt-5.6-terra"}
    assert patch_sol_terra_model_mapping({}, enable=True, fallback_mapping=fallback) == {
        "gpt-5.6-sol": "gpt-5.6-terra",
        "gpt-5.6-terra": "gpt-5.6-terra",
    }
    disabled = patch_sol_terra_model_mapping(
        {"gpt-5.6-sol": "gpt-5.6-terra", "gpt-5.6": "gpt-5.6"},
        enable=False,
    )
    assert disabled["gpt-5.6-sol"] == "gpt-5.6-sol"
    assert patch_sol_terra_model_mapping({"gpt-5.6": "gpt-5.6"}, enable=False) == {"gpt-5.6": "gpt-5.6"}
    assert is_explicit_free_sub2_account({"credentials": {"plan_type": "free"}}) is True
    assert is_explicit_free_sub2_account({"credentials": {"plan_type": "plus"}}) is False
    assert is_explicit_free_sub2_account({"status": "active"}) is False
    assert is_sol_terra_free_target({"credentials": {"plan_type": "free"}}) is True
    assert is_sol_terra_free_target({"status": "active"}, local_plan_state="unknown", has_local_record=True) is True
    assert is_sol_terra_free_target({"status": "active"}, local_plan_state="unknown") is False
    assert is_sol_terra_free_target({"credentials": {"plan_type": "plus"}}) is False
    assert is_sol_terra_free_target({"status": "active"}, local_plan_state="subscribed", has_local_record=True) is False
    assert model_mapping_from_sub2api_account(
        {"credentials": {"model_mapping": {"gpt-5.6-sol": "gpt-5.6-terra"}}}
    ) == {"gpt-5.6-sol": "gpt-5.6-terra"}


def test_update_account_credentials_puts_merged_body():
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None, params=None):
        calls.append({"method": method, "url": url, "json": json})
        return FakeResponse(payload={"data": {"id": 88}})

    client = Sub2ApiOAuthClient(
        base_url="http://127.0.0.1:8080",
        api_key="admin-secret",
        request_fn=fake_request,
    )
    client.update_account_credentials(88, {"access_token": "tok", "model_mapping": {"gpt-5.6-sol": "gpt-5.6-terra"}})
    assert calls[0]["method"] == "PUT"
    assert UPDATE_ACCOUNT_PATH.format(account_id=88) in calls[0]["url"]
    assert calls[0]["json"]["credentials"]["access_token"] == "tok"
    assert calls[0]["json"]["credentials"]["model_mapping"]["gpt-5.6-sol"] == "gpt-5.6-terra"

