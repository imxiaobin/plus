"""ChatGPT phone-number protocol registration.

This is a separate worker from the email protocol in ``protocol_register.py``.
The request sequence follows a captured ChatGPT Web phone signup HAR:

1. ``GET chatgpt.com/`` until Cloudflare passes (warmup, before renting a number)
2. ``GET chatgpt.com/auth/login_with?screen_hint=login_or_signup&login_hint=+57...``
3. NextAuth CSRF + ``POST /api/auth/signin/openai`` with E.164 ``login_hint``
4. Browser-navigate the returned ``auth.openai.com/api/accounts/authorize`` URL
   (one 302 to ``/create-account/password``; not a Codex OAuth hop chain)
5. ``POST /api/accounts/user/register`` with ``{password, username: "+57..."}``
6. ``GET /api/accounts/phone-otp/send``
7. ``POST /api/accounts/phone-otp/validate`` with Sentinel ``verify_phone_otp``
8. ``POST /api/accounts/create_account``
"""
from __future__ import annotations

import random
import uuid
from urllib.parse import urlencode, urljoin

from curl_cffi import CurlError

from .constants import CHATGPT_APP, OPENAI_API_ENDPOINTS, OPENAI_AUTH
from .protocol_register import (
    ChatGPTCloudflareChallengeError,
    ChatGPTProtocolRegister,
    ChatGPTRateLimitError,
    _OAUTH_INIT_MAX_ATTEMPTS,
    _OAUTH_INIT_RETRY_BASE_SECONDS,
    _OAUTH_INIT_RETRY_MAX_SECONDS,
    _authorization_continue_url,
    _authorization_error_from_url,
    _authorization_page_type,
    _is_cloudflare_challenge_response,
    _raise_if_explicit_account_ban,
    _random_profile,
    _response_error,
    _response_json,
)


_BROWSER_HTML_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
)
_PHONE_SAME_SESSION_MAX_ATTEMPTS = 4


def _phone_otp_step(page_type: str, continue_url: str = "") -> bool:
    normalized = str(page_type or "").strip().lower()
    target = str(continue_url or "").strip().lower()
    return normalized in {"phone_otp_send", "phone_otp_verification"} or "phone-otp" in target or "contact-verification" in target


class ChatGPTProtocolPhoneRegister(ChatGPTProtocolRegister):
    """Phone-OTP protocol worker. Email ``run(email=...)`` is not used."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._web_session_ready = False
        self._web_csrf_token = ""

    def _chatgpt_browser_headers(self, *, referer: str = "") -> dict:
        headers = {
            "accept": _BROWSER_HTML_ACCEPT,
            "upgrade-insecure-requests": "1",
            "user-agent": self.user_agent,
        }
        if referer:
            headers["referer"] = referer
        return headers

    def _retry_phone_cloudflare(self, once_fn, *, action: str):
        for attempt in range(1, _OAUTH_INIT_MAX_ATTEMPTS + 1):
            try:
                return once_fn()
            except (ChatGPTCloudflareChallengeError, ChatGPTRateLimitError) as exc:
                can_retry = (
                    self._session_factory is not None
                    and callable(self.proxy_rotate_callback)
                    and attempt < _OAUTH_INIT_MAX_ATTEMPTS
                )
                if not can_retry or not self._rotate_proxy_after_challenge():
                    raise
                delay = min(
                    _OAUTH_INIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    _OAUTH_INIT_RETRY_MAX_SECONDS,
                )
                self.log(
                    f"Cloudflare challenge at {exc.stage}; retrying {action} "
                    f"on a new proxy in {delay:.1f}s "
                    f"({attempt + 1}/{_OAUTH_INIT_MAX_ATTEMPTS})"
                )
                self._wait_before_oauth_retry(delay)
                self._replace_owned_session()
                self._web_session_ready = False
                self._web_csrf_token = ""
            except CurlError as exc:
                can_retry = (
                    self._session_factory is not None
                    and self._is_transient_curl_error(exc)
                    and attempt < _OAUTH_INIT_MAX_ATTEMPTS
                )
                if not can_retry:
                    raise
                delay = min(
                    _OAUTH_INIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    _OAUTH_INIT_RETRY_MAX_SECONDS,
                ) * random.uniform(0.8, 1.2)
                if callable(self.proxy_rotate_callback):
                    self._rotate_proxy(f"{action} curl({int(getattr(exc, 'code', 0) or 0)})")
                self.log(
                    f"{action} hit transient curl; retrying in {delay:.1f}s "
                    f"({attempt + 1}/{_OAUTH_INIT_MAX_ATTEMPTS})"
                )
                self._wait_before_oauth_retry(delay)
                self._replace_owned_session()
                self._web_session_ready = False
                self._web_csrf_token = ""
        raise RuntimeError(f"{action} 重试次数用尽")

    def _retry_phone_same_session(self, once_fn, *, action: str):
        """Retry ChatGPT web steps without rotating proxy or dropping cookies."""
        for attempt in range(1, _PHONE_SAME_SESSION_MAX_ATTEMPTS + 1):
            try:
                return once_fn()
            except (ChatGPTCloudflareChallengeError, ChatGPTRateLimitError) as exc:
                if attempt >= _PHONE_SAME_SESSION_MAX_ATTEMPTS:
                    raise
                delay = min(
                    _OAUTH_INIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    6.0,
                )
                self.log(
                    f"Cloudflare challenge at {exc.stage}; retrying {action} "
                    f"on the same ChatGPT session in {delay:.1f}s "
                    f"({attempt + 1}/{_PHONE_SAME_SESSION_MAX_ATTEMPTS})"
                )
                self._wait_before_oauth_retry(delay)
            except CurlError as exc:
                if (
                    not self._is_transient_curl_error(exc)
                    or attempt >= _PHONE_SAME_SESSION_MAX_ATTEMPTS
                ):
                    raise
                delay = min(
                    _OAUTH_INIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    6.0,
                ) * random.uniform(0.8, 1.2)
                self.log(
                    f"{action} hit transient curl; retrying same ChatGPT session "
                    f"in {delay:.1f}s ({attempt + 1}/{_PHONE_SAME_SESSION_MAX_ATTEMPTS})"
                )
                self._wait_before_oauth_retry(delay)
        raise RuntimeError(f"{action} 重试次数用尽")

    def warmup_web_session(self) -> str:
        """Pass ChatGPT homepage/CSRF Cloudflare checks before renting a number."""
        self.log("等待 Cloudflare 挑战通过后再取号...")
        token = self._retry_phone_cloudflare(
            self._open_chatgpt_web_session_once,
            action="ChatGPT session warmup",
        )
        self._web_csrf_token = token
        self._web_session_ready = True
        return token

    def _open_chatgpt_homepage_once(self) -> None:
        response = self.session.get(
            CHATGPT_APP,
            headers=self._chatgpt_browser_headers(),
            allow_redirects=True,
        )
        self._check_cancelled()
        if _is_cloudflare_challenge_response(response):
            raise ChatGPTCloudflareChallengeError("ChatGPT homepage", response)
        if getattr(response, "status_code", 0) >= 400:
            raise RuntimeError(f"ChatGPT 首页访问失败: {_response_error(response)}")
        try:
            cookie_device_id = str(self.session.cookies.get("oai-did") or "").strip()
            if cookie_device_id:
                self.device_id = cookie_device_id
        except Exception:
            pass

    def _fetch_csrf_token_once(self) -> str:
        csrf_response = self.session.get(f"{CHATGPT_APP}/api/auth/csrf")
        self._check_cancelled()
        if _is_cloudflare_challenge_response(csrf_response):
            raise ChatGPTCloudflareChallengeError("ChatGPT CSRF", csrf_response)
        csrf_payload = _response_json(csrf_response)
        _raise_if_explicit_account_ban(csrf_payload, stage="ChatGPT OAuth CSRF 获取")
        csrf_token = str(csrf_payload.get("csrfToken") or "").strip()
        if getattr(csrf_response, "status_code", 0) != 200 or not csrf_token:
            raise RuntimeError(f"CSRF 获取失败: {_response_error(csrf_response, csrf_payload)}")
        self._web_csrf_token = csrf_token
        return csrf_token

    def _open_chatgpt_web_session_once(self) -> str:
        self._open_chatgpt_homepage_once()
        return self._fetch_csrf_token_once()

    def _visit_chatgpt_login_with(self, phone: str) -> str:
        query = urlencode(
            {
                "callback_path": "/",
                "screen_hint": "login_or_signup",
                "login_hint": phone,
            }
        )
        login_with_url = f"{CHATGPT_APP}/auth/login_with?{query}"
        response = self.session.get(
            login_with_url,
            headers=self._chatgpt_browser_headers(referer=f"{CHATGPT_APP}/"),
            allow_redirects=True,
        )
        self._check_cancelled()
        if _is_cloudflare_challenge_response(response):
            raise ChatGPTCloudflareChallengeError("ChatGPT login_with", response)
        if getattr(response, "status_code", 0) >= 400:
            raise RuntimeError(f"ChatGPT 登录页访问失败: {_response_error(response)}")
        return login_with_url

    def _open_phone_authorize(self, location: str):
        url = urljoin(OPENAI_AUTH, str(location or "").strip())
        if not url:
            raise RuntimeError("手机号注册缺少 authorize URL")
        response = self.session.get(
            url,
            headers=self._chatgpt_browser_headers(referer=f"{CHATGPT_APP}/"),
            allow_redirects=True,
        )
        self._check_cancelled()
        if _is_cloudflare_challenge_response(response):
            raise ChatGPTCloudflareChallengeError("ChatGPT authorize", response)
        if getattr(response, "status_code", 0) >= 400:
            raise RuntimeError(f"ChatGPT 授权页访问失败: {_response_error(response)}")
        return response

    def _initialize_phone_signup(self, phone: str):
        if self._web_session_ready:
            return self._retry_phone_same_session(
                lambda: self._initialize_phone_signup_once(phone),
                action="phone signup",
            )
        return self._retry_phone_cloudflare(
            lambda: self._initialize_phone_signup_once(phone),
            action="phone OAuth",
        )

    def _initialize_phone_signup_once(self, phone: str):
        self.log("从 ChatGPT 首页继续手机号协议注册...")
        if not self._web_session_ready:
            self._open_chatgpt_homepage_once()
        login_with_url = self._visit_chatgpt_login_with(phone)
        csrf_token = self._fetch_csrf_token_once()

        query = urlencode(
            {
                "prompt": "login",
                "screen_hint": "login_or_signup",
                "ext-oai-did": self.device_id,
                "auth_session_logging_id": str(uuid.uuid4()),
                "login_hint": phone,
                "ccaps": "login_methods",
            }
        )
        signin_response = self.session.post(
            f"{CHATGPT_APP}/api/auth/signin/openai?{query}",
            data=urlencode(
                {
                    "callbackUrl": f"{CHATGPT_APP}/",
                    "csrfToken": csrf_token,
                    "json": "true",
                }
            ),
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "origin": CHATGPT_APP,
                "referer": login_with_url,
                "user-agent": self.user_agent,
            },
            allow_redirects=False,
        )
        self._check_cancelled()
        if _is_cloudflare_challenge_response(signin_response):
            raise ChatGPTCloudflareChallengeError("ChatGPT OAuth sign-in", signin_response)
        signin_payload = _response_json(signin_response)
        _raise_if_explicit_account_ban(signin_payload, stage="ChatGPT 手机号 OAuth 初始化")
        location = str(
            signin_payload.get("url")
            or signin_response.headers.get("location")
            or ""
        ).strip()
        if getattr(signin_response, "status_code", 0) >= 400 or not location:
            raise RuntimeError(
                f"手机号注册授权初始化失败: {_response_error(signin_response, signin_payload)}"
            )
        final_response = self._open_phone_authorize(location)
        final_url = str(getattr(final_response, "url", "") or location).strip()
        authorization_error = _authorization_error_from_url(final_url)
        if authorization_error:
            if any(
                marker in authorization_error.lower()
                for marker in ("rate_limit", "rate limit", "too_many_requests")
            ):
                raise ChatGPTRateLimitError(authorization_error)
            raise RuntimeError(f"手机号 OAuth 初始化失败: {authorization_error}")
        try:
            cookie_device_id = str(self.session.cookies.get("oai-did") or "").strip()
            if cookie_device_id:
                self.device_id = cookie_device_id
        except Exception:
            pass
        return final_response

    def _send_phone_otp(self, referer: str = "") -> dict:
        resolved_referer = urljoin(OPENAI_AUTH, referer or "/create-account/password")
        response = self.session.get(
            OPENAI_API_ENDPOINTS["send_phone_otp"],
            headers={
                "accept": _BROWSER_HTML_ACCEPT,
                "origin": OPENAI_AUTH,
                "referer": resolved_referer,
                "oai-device-id": self.device_id,
                "user-agent": self.user_agent,
            },
            allow_redirects=True,
        )
        payload = _response_json(response)
        _raise_if_explicit_account_ban(payload, stage="OpenAI 手机验证码发送")
        if getattr(response, "status_code", 0) >= 400 or payload.get("error"):
            raise RuntimeError(f"发送手机验证码失败: {_response_error(response, payload)}")
        self.log("手机验证码已发送")
        return payload

    def _validate_phone_otp(self, code: str) -> dict:
        headers = self._common_headers(f"{OPENAI_AUTH}/contact-verification")
        headers["oai-device-id"] = self.device_id
        headers.update(self.sentinel.build_headers(self.device_id, "verify_phone_otp"))
        response = self.session.post(
            OPENAI_API_ENDPOINTS["validate_phone_otp"],
            json={"code": code},
            headers=headers,
        )
        payload = _response_json(response)
        _raise_if_explicit_account_ban(payload, stage="OpenAI 手机验证码校验")
        if getattr(response, "status_code", 0) >= 400 or payload.get("error"):
            raise RuntimeError(f"手机验证码校验失败: {_response_error(response, payload)}")
        return payload

    def _run_phone_web_registration(self, *, phone: str, password: str) -> dict:
        self.log(f"开始 ChatGPT 手机号协议注册: {phone}")
        self._initialize_phone_signup(phone)
        password_result = self._register_password(phone, password)
        self._direct_registration_mutated = True
        page_type = _authorization_page_type(password_result)
        continue_url = _authorization_continue_url(password_result)
        self.log(f"ChatGPT 手机号密码创建成功: next={page_type or continue_url or 'unknown'}")
        if not _phone_otp_step(page_type, continue_url):
            raise RuntimeError(
                f"手机号注册未进入短信验证步骤: {page_type or continue_url or 'unknown'}"
            )
        self._send_phone_otp(continue_url or "/create-account/password")
        code = str(self.otp_callback() or "").strip()
        self._check_cancelled()
        if not code:
            raise RuntimeError("未收到手机短信验证码")
        validation = self._validate_phone_otp(code)
        page_type = _authorization_page_type(validation)
        continue_url = _authorization_continue_url(validation)
        self.log(f"手机验证码校验通过: next={page_type or continue_url or 'unknown'}")
        if continue_url:
            self._visit_auth_step(continue_url, referer="/contact-verification")
        created = self._create_account(*_random_profile())
        callback_url = _authorization_continue_url(created)
        if callback_url:
            self.session.get(
                urljoin(OPENAI_AUTH, callback_url),
                headers={"user-agent": self.user_agent},
                allow_redirects=True,
            )
        result = self._finalize_registration_result(
            self._session_result(phone, password)
        )
        self.log("ChatGPT 手机号协议注册完成")
        return result

    def close(self) -> None:
        try:
            self.sentinel.close()
        except Exception:
            pass
        try:
            self.session.close()
        except Exception:
            pass

    def register_phone(self, *, phone: str, password: str = "") -> dict:
        number = str(phone or "").strip()
        if not number:
            raise RuntimeError("手机号协议注册缺少手机号")
        if not callable(self.otp_callback):
            raise RuntimeError("手机号协议注册缺少短信验证码回调")
        self._check_cancelled()
        self._direct_registration_mutated = False
        return self._run_phone_web_registration(phone=number, password=password)

    def run(self, *, email: str = "", password: str = "", phone: str = "") -> dict:
        try:
            return self.register_phone(phone=str(phone or email or "").strip(), password=password)
        finally:
            self.close()
