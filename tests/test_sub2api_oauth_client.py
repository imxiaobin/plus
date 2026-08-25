from __future__ import annotations

from platforms.chatgpt.sub2api_oauth import (
    CREATE_ACCOUNT_PATH,
    EXCHANGE_CODE_PATH,
    GENERATE_AUTH_URL_PATH,
    Sub2ApiError,
    Sub2ApiOAuthClient,
    extract_sub2api_account_id,
    join_sub2api_url,
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


def test_client_rejects_incomplete_config():
    try:
        Sub2ApiOAuthClient(base_url="", api_key="k")
    except Sub2ApiError as exc:
        assert "请先在设置中填写 Sub2API" in str(exc)
    else:
        raise AssertionError("empty URL should fail")
