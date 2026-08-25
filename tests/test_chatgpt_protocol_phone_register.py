from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from platforms.chatgpt.constants import (
    CHATGPT_APP,
    OPENAI_API_ENDPOINTS,
    OPENAI_AUTH,
)
from platforms.chatgpt.protocol_phone_register import ChatGPTProtocolPhoneRegister
from providers.sms.herosms import HeroSMSClient, HeroSMSError, normalize_e164
from tests.test_chatgpt_protocol_register import (
    _FakeResponse,
    _FakeSession,
    _install_valid_web_session_mint,
)


def test_normalize_e164_adds_plus():
    assert normalize_e164("573180453717") == "+573180453717"
    assert normalize_e164("+573180453717") == "+573180453717"


def test_herosms_parses_access_number(monkeypatch):
    client = HeroSMSClient(api_key="k", service="oi", country=46)

    def fake_get(url, params=None, timeout=None):
        class Response:
            status_code = 200
            text = "ACCESS_NUMBER:998877:573180453717"

        assert params["action"] == "getNumber"
        assert params["service"] == "oi"
        return Response()

    monkeypatch.setattr(client.session, "get", fake_get)
    activation = client.get_number()
    assert activation.activation_id == "998877"
    assert activation.phone == "+573180453717"


def test_herosms_wait_for_code(monkeypatch):
    client = HeroSMSClient(api_key="k")
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        class Response:
            status_code = 200
            text = "STATUS_WAIT_CODE" if calls["n"] == 0 else "STATUS_OK:654321"

        calls["n"] += 1
        return Response()

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr("providers.sms.herosms.time.sleep", lambda _seconds: None)
    assert client.wait_for_code("1", timeout_seconds=10, poll_interval_seconds=0.01) == "654321"


_PHONE_AUTHORIZE_URL = (
    f"{OPENAI_AUTH}/api/accounts/authorize"
    "?client_id=app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
    "&login_hint=%2B573180453717"
    "&ccaps=login_methods"
)


class _PhoneSession(_FakeSession):
    def get(self, url, **kwargs):
        path = urlparse(url).path
        if path == "/api/accounts/phone-otp/send":
            self.calls.append(("GET", url, kwargs))
            self.send_otp_calls += 1
            return _FakeResponse(payload={"ok": True}, url=f"{OPENAI_AUTH}/contact-verification")
        if path == "/api/accounts/authorize":
            self.calls.append(("GET", url, kwargs))
            return _FakeResponse(url=f"{OPENAI_AUTH}/create-account/password")
        return super().get(url, **kwargs)

    def post(self, url, **kwargs):
        if url.startswith(f"{CHATGPT_APP}/api/auth/signin/openai?"):
            self.calls.append(("POST", url, kwargs))
            query = parse_qs(urlparse(url).query)
            assert query["screen_hint"] == ["login_or_signup"]
            assert query["login_hint"] == ["+573180453717"]
            assert query["ccaps"] == ["login_methods"]
            return _FakeResponse(payload={"url": _PHONE_AUTHORIZE_URL})
        if url == OPENAI_API_ENDPOINTS["register"]:
            self.calls.append(("POST", url, kwargs))
            self.password_body = kwargs["json"]
            return _FakeResponse(
                payload={
                    "continue_url": "/api/accounts/phone-otp/send",
                    "page": {"type": "phone_otp_send"},
                }
            )
        if url == OPENAI_API_ENDPOINTS["validate_phone_otp"]:
            self.calls.append(("POST", url, kwargs))
            assert kwargs["json"] == {"code": "654321"}
            token = json.loads(kwargs["headers"]["openai-sentinel-token"])
            assert token["flow"] == "verify_phone_otp"
            return _FakeResponse(
                payload={"continue_url": "/about-you", "page": {"type": "about_you"}}
            )
        if url == OPENAI_API_ENDPOINTS["signup"]:
            raise AssertionError("phone registration must not call email authorize/continue")
        if url == OPENAI_API_ENDPOINTS["validate_otp"]:
            raise AssertionError("phone registration must not call email-otp/validate")
        return super().post(url, **kwargs)


def test_protocol_phone_register_completes_without_email_otp(monkeypatch):
    _install_valid_web_session_mint(monkeypatch)
    monkeypatch.setattr(
        "platforms.chatgpt.mfa.bind_totp_2fa",
        lambda *_args, **_kwargs: {
            "activated": True,
            "secret": "TESTPROTOCOLTOTP",
            "result": {"success": True},
        },
    )
    session = _PhoneSession()
    logs = []
    worker = ChatGPTProtocolPhoneRegister(
        session=session,
        otp_callback=lambda: "654321",
        log_fn=logs.append,
    )

    result = worker.run(phone="+573180453717", password="StrongPass123!")

    assert result["email"] == "+573180453717"
    assert result["password"] == "StrongPass123!"
    assert result["refresh_token"] == "web-codex-refresh"
    assert session.password_body == {
        "username": "+573180453717",
        "password": "StrongPass123!",
    }
    assert session.send_otp_calls == 1
    assert any(
        method == "GET" and urlparse(url).path == "/api/accounts/phone-otp/send"
        for method, url, _kwargs in session.calls
    )
    assert any(
        method == "POST" and url == OPENAI_API_ENDPOINTS["validate_phone_otp"]
        for method, url, _kwargs in session.calls
    )
    assert not any(
        method == "POST" and url == OPENAI_API_ENDPOINTS["signup"]
        for method, url, _kwargs in session.calls
    )
    create_sentinel = json.loads(session.create_headers["openai-sentinel-token"])
    assert create_sentinel["flow"] == "oauth_create_account"
    assert any("手机号协议注册" in line for line in logs)
    assert session.closed is True
    assert result["totp_2fa"]["bound"] is True
    assert any(
        method == "GET" and "/auth/login_with" in url
        for method, url, _kwargs in session.calls
    )
    authorize_calls = [
        (url, kwargs)
        for method, url, kwargs in session.calls
        if method == "GET" and urlparse(url).path == "/api/accounts/authorize"
    ]
    assert authorize_calls
    _authorize_url, authorize_kwargs = authorize_calls[0]
    assert authorize_kwargs.get("allow_redirects") is True
    authorize_headers = authorize_kwargs.get("headers") or {}
    assert "text/html" in authorize_headers.get("accept", "")
    assert authorize_headers.get("referer") == f"{CHATGPT_APP}/"
    signin_calls = [
        kwargs
        for method, url, kwargs in session.calls
        if method == "POST" and "/api/auth/signin/openai" in url
    ]
    assert signin_calls
    assert "/auth/login_with" in (signin_calls[0].get("headers") or {}).get("referer", "")
    assert not any(
        method == "GET" and kwargs.get("allow_redirects") is False
        for method, url, kwargs in session.calls
        if urlparse(url).path == "/api/accounts/authorize"
    )


def test_herosms_rejects_empty_key():
    try:
        HeroSMSClient(api_key=" ")
    except HeroSMSError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected HeroSMSError")


def test_phone_register_rents_herosms_after_cloudflare_warmup(monkeypatch):
    from core.base_platform import RegisterConfig
    from platforms.chatgpt.plugin import ChatGPTPlatform
    from providers.sms.herosms import HeroSMSActivation

    order = []

    class FakeWorker:
        def __init__(self, **_kwargs):
            pass

        def warmup_web_session(self):
            order.append("warmup")

        def register_phone(self, *, phone, password):
            order.append(("register", phone))
            return {
                "email": phone,
                "password": password,
                "account_id": "acc-1",
                "access_token": "at",
                "refresh_token": "rt",
            }

        def close(self):
            order.append("close")

    class FakeHero:
        service = "dr"
        country = 33

        @classmethod
        def from_config(cls, extra=None):
            return shared

        def get_number(self):
            order.append("get_number")
            return HeroSMSActivation("act-1", "+573180453717")

        def mark_ready(self, _activation_id):
            order.append("mark_ready")

        def complete(self, _activation_id):
            order.append("complete")

        def cancel(self, _activation_id):
            order.append("cancel")

        def wait_for_code(self, *_args, **_kwargs):
            return "123456"

    shared = FakeHero()
    monkeypatch.setattr("providers.sms.herosms.HeroSMSClient", FakeHero)
    monkeypatch.setattr(
        "platforms.chatgpt.protocol_phone_register.ChatGPTProtocolPhoneRegister",
        FakeWorker,
    )

    platform = ChatGPTPlatform(
        config=RegisterConfig(executor_type="protocol", extra={"identity_provider": "phone"})
    )
    account = platform.register()

    assert order[0] == "warmup"
    assert order.index("warmup") < order.index("get_number")
    assert ("register", "+573180453717") in order
    assert account.email == "+573180453717"
    assert "complete" in order
    assert "cancel" not in order


def test_phone_register_does_not_rent_number_when_cloudflare_warmup_fails(monkeypatch):
    from core.base_platform import RegisterConfig
    from platforms.chatgpt.plugin import ChatGPTPlatform

    order = []

    class FakeWorker:
        def __init__(self, **_kwargs):
            pass

        def warmup_web_session(self):
            order.append("warmup")
            raise RuntimeError("Cloudflare 挑战未通过")

        def register_phone(self, **_kwargs):
            order.append("register")
            raise AssertionError("warmup failed, should not continue")

        def close(self):
            order.append("close")

    class FakeHero:
        @classmethod
        def from_config(cls, extra=None):
            return shared

        def get_number(self):
            order.append("get_number")
            raise AssertionError("should not rent a number before Cloudflare passes")

        def cancel(self, _activation_id):
            order.append("cancel")

    shared = FakeHero()
    monkeypatch.setattr("providers.sms.herosms.HeroSMSClient", FakeHero)
    monkeypatch.setattr(
        "platforms.chatgpt.protocol_phone_register.ChatGPTProtocolPhoneRegister",
        FakeWorker,
    )

    platform = ChatGPTPlatform(
        config=RegisterConfig(executor_type="protocol", extra={"identity_provider": "phone"})
    )
    try:
        platform.register()
    except RuntimeError as exc:
        assert "Cloudflare" in str(exc)
    else:
        raise AssertionError("expected warmup failure")
    assert order == ["warmup", "close"]


def _homepage_gets(session):
    return [
        (method, url, kwargs)
        for method, url, kwargs in session.calls
        if method == "GET" and urlparse(url).path in {"", "/"}
        and urlparse(url).netloc == urlparse(CHATGPT_APP).netloc
    ]


def test_phone_signup_reuses_warmup_session_and_browser_authorize():
    session = _PhoneSession()
    worker = ChatGPTProtocolPhoneRegister(
        session=session,
        otp_callback=lambda: "654321",
        log_fn=lambda _message: None,
    )
    worker.warmup_web_session()
    homepage_count = len(_homepage_gets(session))
    assert homepage_count == 1
    worker._initialize_phone_signup("+573180453717")
    assert len(_homepage_gets(session)) == homepage_count
    login_with = [
        kwargs
        for method, url, kwargs in session.calls
        if method == "GET" and "/auth/login_with" in url
    ]
    assert login_with
    assert login_with[0].get("allow_redirects") is True
    authorize = [
        kwargs
        for method, url, kwargs in session.calls
        if method == "GET" and urlparse(url).path == "/api/accounts/authorize"
    ]
    assert authorize
    assert authorize[0].get("allow_redirects") is True
    headers = authorize[0].get("headers") or {}
    assert "text/html" in headers.get("accept", "")
    assert headers.get("referer") == f"{CHATGPT_APP}/"


def test_phone_signup_after_warmup_retries_authorize_without_rotating(monkeypatch):
    class Session(_PhoneSession):
        def __init__(self):
            super().__init__()
            self.authorize_attempts = 0

        def get(self, url, **kwargs):
            if urlparse(url).path == "/api/accounts/authorize":
                self.calls.append(("GET", url, kwargs))
                self.authorize_attempts += 1
                if self.authorize_attempts < 2:
                    return _FakeResponse(
                        status_code=403,
                        headers={"server": "cloudflare", "cf-ray": "abc"},
                        text="<title>Just a moment</title>",
                    )
                return _FakeResponse(url=f"{OPENAI_AUTH}/create-account/password")
            return super().get(url, **kwargs)

    rotated = []
    rebuilt = []
    session = Session()
    worker = ChatGPTProtocolPhoneRegister(
        session=session,
        otp_callback=lambda: "654321",
        proxy_rotate_callback=lambda: rotated.append("rotated") or "http://proxy",
        log_fn=lambda _message: None,
    )
    worker._session_factory = lambda: rebuilt.append("rebuilt") or session
    monkeypatch.setattr(worker, "_wait_before_oauth_retry", lambda _delay: None)
    worker.warmup_web_session()
    worker._initialize_phone_signup("+573180453717")
    assert session.authorize_attempts == 2
    assert rotated == []
    assert rebuilt == []


def test_retryable_phone_auth_step_error_detection():
    from platforms.chatgpt.plugin import is_retryable_phone_auth_step_error

    assert is_retryable_phone_auth_step_error(
        RuntimeError("设置 ChatGPT 密码失败: invalid_auth_step: Invalid authorization step.")
    )
    assert not is_retryable_phone_auth_step_error(RuntimeError("Cloudflare challenge during ChatGPT homepage"))


def test_phone_register_swaps_number_on_invalid_auth_step(monkeypatch):
    from core.base_platform import RegisterConfig
    from platforms.chatgpt.plugin import ChatGPTPlatform
    from providers.sms.herosms import HeroSMSActivation

    order = []
    attempts = {"n": 0}

    class FakeWorker:
        def __init__(self, **_kwargs):
            pass

        def warmup_web_session(self):
            order.append("warmup")

        def register_phone(self, *, phone, password):
            attempts["n"] += 1
            order.append(("register", phone))
            if attempts["n"] == 1:
                raise RuntimeError(
                    "设置 ChatGPT 密码失败: invalid_auth_step: Invalid authorization step."
                )
            return {
                "email": phone,
                "password": password,
                "account_id": "acc-2",
                "access_token": "at",
                "refresh_token": "rt",
            }

        def close(self):
            order.append("close")

    activations = [
        HeroSMSActivation("act-1", "+573180453717"),
        HeroSMSActivation("act-2", "+573181042649"),
    ]

    class FakeHero:
        service = "dr"
        country = 33

        @classmethod
        def from_config(cls, extra=None):
            return shared

        def get_number(self):
            order.append("get_number")
            return activations.pop(0)

        def mark_ready(self, activation_id):
            order.append(("ready", activation_id))

        def complete(self, activation_id):
            order.append(("complete", activation_id))

        def cancel(self, activation_id):
            order.append(("cancel", activation_id))

        def wait_for_code(self, *_args, **_kwargs):
            return "123456"

    shared = FakeHero()
    monkeypatch.setattr("providers.sms.herosms.HeroSMSClient", FakeHero)
    monkeypatch.setattr(
        "platforms.chatgpt.protocol_phone_register.ChatGPTProtocolPhoneRegister",
        FakeWorker,
    )

    logs = []
    platform = ChatGPTPlatform(
        config=RegisterConfig(executor_type="protocol", extra={"identity_provider": "phone"})
    )
    platform.log = logs.append
    account = platform.register()

    assert order[0] == "warmup"
    assert order.count("get_number") == 2
    assert ("cancel", "act-1") in order
    assert ("complete", "act-2") in order
    assert order.index(("cancel", "act-1")) < order.index(("register", "+573181042649"))
    assert account.email == "+573181042649"
    assert any("invalid_auth_step" in line and "换号" in line for line in logs)
    assert "close" in order
    assert "warmup" == order[0]
    assert order.count("warmup") == 1


def test_phone_register_does_not_swap_number_on_other_errors(monkeypatch):
    from core.base_platform import RegisterConfig
    from platforms.chatgpt.plugin import ChatGPTPlatform
    from providers.sms.herosms import HeroSMSActivation

    order = []

    class FakeWorker:
        def __init__(self, **_kwargs):
            pass

        def warmup_web_session(self):
            order.append("warmup")

        def register_phone(self, **_kwargs):
            order.append("register")
            raise RuntimeError("Cloudflare challenge during ChatGPT homepage (HTTP 403)")

        def close(self):
            order.append("close")

    class FakeHero:
        service = "dr"
        country = 33

        @classmethod
        def from_config(cls, extra=None):
            return shared

        def get_number(self):
            order.append("get_number")
            return HeroSMSActivation("act-1", "+573180453717")

        def mark_ready(self, _activation_id):
            pass

        def cancel(self, activation_id):
            order.append(("cancel", activation_id))

        def complete(self, _activation_id):
            order.append("complete")

    shared = FakeHero()
    monkeypatch.setattr("providers.sms.herosms.HeroSMSClient", FakeHero)
    monkeypatch.setattr(
        "platforms.chatgpt.protocol_phone_register.ChatGPTProtocolPhoneRegister",
        FakeWorker,
    )

    platform = ChatGPTPlatform(
        config=RegisterConfig(executor_type="protocol", extra={"identity_provider": "phone"})
    )
    try:
        platform.register()
    except RuntimeError as exc:
        assert "Cloudflare" in str(exc)
    else:
        raise AssertionError("expected non-retryable failure")
    assert order.count("get_number") == 1
    assert ("cancel", "act-1") in order
    assert "complete" not in order
    assert "close" in order
