from __future__ import annotations

import json

from platforms.chatgpt.protocol_register import ChatGPTProtocolRegister
from platforms.chatgpt.sub2api_codex_login import Sub2ApiCodexLogin, validate_callback


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, url="", text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.url = url
        self.text = text if text else json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.handlers = []
        self.cookies = {}

    def add(self, method, substr, response):
        self.handlers.append((method.upper(), substr, response))

    def _dispatch(self, method, url, **kwargs):
        self.calls.append((method.upper(), url, kwargs))
        for expected_method, substr, response in self.handlers:
            if expected_method == method.upper() and substr in url:
                return response(url, kwargs) if callable(response) else response
        return FakeResponse(200, {}, url=url)

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)


class FakeMailbox:
    def get_email(self):
        return type("MailboxAccount", (), {"email": "bind@example.com", "extra": {}})()

    def get_current_ids(self, account):
        return set()

    def wait_for_code(self, account, timeout=120, before_ids=None):
        return "654321"

    def commit_email(self, account):
        return True

    def release_email(self, account):
        return True


def _login(session) -> Sub2ApiCodexLogin:
    register = ChatGPTProtocolRegister(session=session, totp_secret="JBSWY3DPEHPK3PXP")
    register.sentinel.build_headers = lambda *args, **kwargs: {}
    return Sub2ApiCodexLogin(register=register, totp_secret="JBSWY3DPEHPK3PXP")


def test_phone_har_login_never_calls_local_oauth_token(monkeypatch):
    session = FakeSession()
    auth_url = "https://auth.openai.com/oauth/authorize?client_id=app_EMoamEEZ73f0CkXaXp7hrann&state=st_sub2"
    session.add(
        "GET",
        "/oauth/authorize",
        FakeResponse(303, headers={"location": "https://auth.openai.com/log-in"}, url=auth_url),
    )
    session.add(
        "GET",
        "/log-in",
        FakeResponse(200, {}, url="https://auth.openai.com/log-in"),
    )
    session.add(
        "POST",
        "/api/accounts/authorize/continue",
        FakeResponse(200, {"page": {"type": "password"}}),
    )
    session.add(
        "POST",
        "/api/accounts/password/verify",
        FakeResponse(
            200,
            {"page": {"type": "mfa_challenge", "payload": {"factor_id": "factor-1", "type": "totp"}}},
        ),
    )
    session.add(
        "POST",
        "/api/accounts/mfa/issue_challenge",
        FakeResponse(200, {"page": {"type": "mfa_challenge"}}),
    )
    session.add(
        "POST",
        "/api/accounts/mfa/verify",
        FakeResponse(200, {"page": {"type": "add_email"}}),
    )
    session.add(
        "POST",
        "/api/accounts/add-email/send",
        FakeResponse(200, {"page": {"type": "email_otp_verification"}}),
    )
    session.add(
        "POST",
        "/api/accounts/email-otp/validate",
        FakeResponse(
            200,
            {
                "oai-client-auth-session": {
                    "workspaces": [{"id": "ws-1"}],
                }
            },
        ),
    )
    session.add(
        "POST",
        "/api/accounts/workspace/select",
        FakeResponse(
            303,
            {},
            headers={"location": "http://localhost:1455/auth/callback?code=ac_test&state=st_sub2"},
        ),
    )
    session.add(
        "GET",
        "localhost:1455",
        FakeResponse(200, {}, url="http://localhost:1455/auth/callback?code=ac_test&state=st_sub2"),
    )

    monkeypatch.setattr(
        "platforms.chatgpt.sub2api_codex_login.lease_mailbox_for_add_email",
        lambda **kwargs: (FakeMailbox(), FakeMailbox().get_email(), set()),
    )

    login = _login(session)
    callback = login.login_to_callback(
        identity="+15551234567",
        password="Secret123!",
        totp_secret="JBSWY3DPEHPK3PXP",
        auth_url=auth_url,
        expected_state="st_sub2",
    )
    parsed = validate_callback(callback, expected_state="st_sub2")

    assert parsed["code"] == "ac_test"
    assert parsed["state"] == "st_sub2"
    posted = [url for method, url, _kwargs in session.calls if method == "POST"]
    assert any("authorize/continue" in url for url in posted)
    assert any("password/verify" in url for url in posted)
    assert any("mfa/issue_challenge" in url for url in posted)
    assert any("mfa/verify" in url for url in posted)
    assert any("add-email/send" in url for url in posted)
    assert any("workspace/select" in url for url in posted)
    assert not any("/oauth/token" in url for method, url, _kwargs in session.calls)


def test_email_login_starts_from_sub2_auth_url_not_local_pkce():
    session = FakeSession()
    auth_url = "https://auth.openai.com/oauth/authorize?state=st_email"
    session.add("GET", "/oauth/authorize", FakeResponse(200, {}, url=auth_url))
    session.add(
        "POST",
        "/api/accounts/authorize/continue",
        FakeResponse(200, {"page": {"type": "password"}, "continue_url": "/log-in/password"}),
    )
    session.add(
        "POST",
        "/api/accounts/password/verify",
        FakeResponse(
            200,
            {
                "page": {"type": "mfa_challenge", "payload": {"factor_id": "factor-mail", "type": "totp"}},
            },
        ),
    )
    session.add("POST", "/api/accounts/mfa/issue_challenge", FakeResponse(200, {}))
    session.add(
        "POST",
        "/api/accounts/mfa/verify",
        FakeResponse(
            200,
            {
                "oai-client-auth-session": {"workspaces": [{"id": "ws-mail"}]},
            },
        ),
    )
    session.add(
        "POST",
        "/api/accounts/workspace/select",
        FakeResponse(
            303,
            {},
            headers={"location": "http://localhost:1455/auth/callback?code=ac_email&state=st_email"},
        ),
    )

    login = _login(session)
    callback = login.login_to_callback(
        identity="user@example.com",
        password="Secret123!",
        totp_secret="JBSWY3DPEHPK3PXP",
        auth_url=auth_url,
        expected_state="st_email",
    )

    assert "code=ac_email" in callback
    assert not any("generate_oauth_url" in url for _method, url, _kwargs in session.calls)
    assert not any("/oauth/token" in url for _method, url, _kwargs in session.calls)
    continue_calls = [
        kwargs.get("json")
        for method, url, kwargs in session.calls
        if method == "POST" and "authorize/continue" in url
    ]
    assert continue_calls
    assert continue_calls[0]["username"]["kind"] == "email"
    assert continue_calls[0]["username"]["value"] == "user@example.com"
