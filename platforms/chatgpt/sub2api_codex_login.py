"""HAR-aligned Codex OAuth login that starts from a Sub2 ``auth_url``.

Phone: authorize/continue → password/verify → optional TOTP → optional add-email
→ workspace/select → intercept localhost:1455 Location.

Email: ``_submit_login_email`` then the same password/MFA/workspace tail.

Never calls local ``/oauth/token`` or ``submit_callback_url``.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from curl_cffi import CurlError

from platforms.chatgpt.constants import (
    CODEX_CLIENT_ID,
    CODEX_REDIRECT_URI,
    OPENAI_API_ENDPOINTS,
    OPENAI_AUTH,
)
from platforms.chatgpt.credential_checks import _workspace_and_org_from_payload
from platforms.chatgpt.mfa import totp_code
from platforms.chatgpt.oauth import OAuthStart
from platforms.chatgpt.protocol_register import (
    ChatGPTCloudflareChallengeError,
    ChatGPTProtocolRegister,
    _authorization_continue_url,
    _authorization_page_type,
    _is_cloudflare_challenge_response,
    _oauth_callback_target,
    _OAUTH_INIT_MAX_ATTEMPTS,
    _OAUTH_INIT_RETRY_BASE_SECONDS,
    _OAUTH_INIT_RETRY_MAX_SECONDS,
    _raise_if_explicit_account_ban,
    _response_error,
    _response_json,
)

logger = logging.getLogger(__name__)

ADD_EMAIL_PAGE_TYPES = {
    "add_email",
    "add-email",
    "email_otp_verification",
    "email_required",
    "bind_email",
}
MFA_PAGE_TYPES = {
    "mfa_challenge",
    "mfa",
    "totp",
    "totp_challenge",
    "two_factor",
}


class Sub2ApiLoginError(RuntimeError):
    """Protocol login failed before Sub2 exchange-code."""


def is_email_identity(value: str) -> bool:
    return "@" in str(value or "").strip()


def normalize_phone(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("+"):
        return text
    digits = "".join(ch for ch in text if ch.isdigit())
    return f"+{digits}" if digits else text


def parse_oauth_callback(callback_url: str) -> dict[str, str]:
    parsed = urlparse(str(callback_url or "").strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)

    def first(key: str) -> str:
        items = query.get(key) or fragment.get(key) or [""]
        return str(items[0] if items else "").strip()

    return {
        "code": first("code"),
        "state": first("state"),
        "error": first("error"),
        "error_description": first("error_description"),
    }


def validate_callback(
    callback_url: str,
    *,
    expected_state: str,
) -> dict[str, str]:
    parsed = parse_oauth_callback(callback_url)
    if parsed.get("error"):
        detail = parsed.get("error_description") or parsed["error"]
        raise Sub2ApiLoginError(f"OAuth callback 返回错误：{detail}")
    if not parsed.get("code"):
        raise Sub2ApiLoginError("OAuth callback 缺少 code")
    actual_state = parsed.get("state") or ""
    if str(expected_state or "").strip() and actual_state != str(expected_state).strip():
        raise Sub2ApiLoginError("OAuth callback state 与 Sub2 会话不一致")
    return parsed


def _page_type(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return _authorization_page_type(payload).strip().lower().replace("-", "_")


def _needs_add_email(url: str, payload: dict | None) -> bool:
    text = str(url or "").lower()
    if "add-email" in text or "email-verification" in text:
        page = _page_type(payload)
        if page in {"email_otp_send", "email_otp_verification"} and "add-email" not in text:
            return False
        if "add-email" in text:
            return True
    return _page_type(payload) in ADD_EMAIL_PAGE_TYPES


def _needs_mfa(payload: dict | None) -> bool:
    if _page_type(payload) in MFA_PAGE_TYPES:
        return True
    if not isinstance(payload, dict):
        return False
    page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
    inner = page.get("payload") if isinstance(page.get("payload"), dict) else {}
    return bool(inner.get("factor_id") or payload.get("factor_id"))


def _pick_totp_factor(payload: dict | None) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", "totp"

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
    inner = page.get("payload") if isinstance(page.get("payload"), dict) else {}
    direct = str(inner.get("factor_id") or payload.get("factor_id") or "").strip()
    if direct:
        return direct, str(inner.get("type") or payload.get("type") or "totp")
    for obj in walk(payload):
        factor_id = str(obj.get("id") or obj.get("factor_id") or "").strip()
        factor_type = str(obj.get("type") or obj.get("factor_type") or "").strip().lower()
        if factor_id and factor_type in {"totp", "otp", "authenticator"}:
            return factor_id, factor_type or "totp"
    return "", "totp"


def lease_mailbox_for_add_email(*, proxy: str | None = None):
    from core.base_mailbox import create_mailbox
    from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository

    definitions = ProviderDefinitionsRepository().list_by_type("mailbox", enabled_only=True)
    if not definitions:
        raise Sub2ApiLoginError(
            "add-email 需要邮箱池，请先在设置中启用微软邮箱池或其他邮箱服务"
        )
    preferred = next(
        (item for item in definitions if str(item.provider_key) == "local_ms_pool"),
        None,
    )
    chosen = preferred or definitions[0]
    mailbox = create_mailbox(provider=str(chosen.provider_key), extra={}, proxy=proxy)
    account = mailbox.get_email()
    before_ids = mailbox.get_current_ids(account)
    return mailbox, account, before_ids


class Sub2ApiCodexLogin:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        totp_secret: str = "",
        log_fn: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        register: ChatGPTProtocolRegister | None = None,
        proxy_rotate_callback: Callable[[], str | None] | None = None,
    ):
        self.log = log_fn or (lambda _message: None)
        if register is not None:
            self.register = register
        else:
            from platforms.chatgpt.credential_checks import _next_protocol_login_profile

            self.register = ChatGPTProtocolRegister(
                proxy=proxy,
                totp_secret=totp_secret,
                log_fn=self.log,
                cancel_check=cancel_check,
                proxy_rotate_callback=proxy_rotate_callback,
                profile=_next_protocol_login_profile(),
            )
        if totp_secret:
            self.register.totp_secret = str(totp_secret).strip()

    def _oauth_start(self, *, auth_url: str, state: str, redirect_uri: str) -> OAuthStart:
        return OAuthStart(
            auth_url=auth_url,
            state=state,
            code_verifier="",
            redirect_uri=redirect_uri or CODEX_REDIRECT_URI,
            client_id=CODEX_CLIENT_ID,
        )

    def _post_account_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        referer: str,
        flow: str = "",
    ):
        headers = self.register._common_headers(referer)
        headers["oai-device-id"] = self.register.device_id
        if flow:
            headers.update(self.register.sentinel.build_headers(self.register.device_id, flow))
        response = self.register.session.post(url, json=payload, headers=headers, allow_redirects=False)
        if _is_cloudflare_challenge_response(response):
            raise ChatGPTCloudflareChallengeError(url, response)
        body = _response_json(response)
        _raise_if_explicit_account_ban(body, stage=url)
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400 or body.get("error"):
            raise Sub2ApiLoginError(f"{url} 失败: {_response_error(response, body)}")
        location = str(getattr(response, "headers", {}).get("location") or "").strip()
        if location and not _authorization_continue_url(body):
            body = {**body, "continue_url": location}
        return response, body

    def login_to_callback(
        self,
        *,
        identity: str,
        password: str,
        totp_secret: str,
        auth_url: str,
        expected_state: str,
        redirect_uri: str = CODEX_REDIRECT_URI,
    ) -> str:
        identity_value = str(identity or "").strip()
        if not identity_value or not str(password or ""):
            raise Sub2ApiLoginError("授权需要账号和密码")
        last_error: Exception | None = None
        for attempt in range(1, _OAUTH_INIT_MAX_ATTEMPTS + 1):
            self.register._check_cancelled()
            try:
                return self._login_to_callback_once(
                    identity_value=identity_value,
                    password=password,
                    totp_secret=totp_secret,
                    auth_url=auth_url,
                    expected_state=expected_state,
                    redirect_uri=redirect_uri,
                )
            except ChatGPTCloudflareChallengeError as exc:
                last_error = exc
                can_retry = (
                    attempt < _OAUTH_INIT_MAX_ATTEMPTS
                    and self.register._session_factory is not None
                    and callable(self.register.proxy_rotate_callback)
                    and self.register._rotate_proxy_after_challenge()
                )
                if not can_retry:
                    raise
                delay = min(
                    _OAUTH_INIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    _OAUTH_INIT_RETRY_MAX_SECONDS,
                )
                self.log(
                    f"Cloudflare challenge at {exc.stage}; retrying Sub2 OAuth "
                    f"on a new proxy in {delay:.1f}s "
                    f"({attempt + 1}/{_OAUTH_INIT_MAX_ATTEMPTS})"
                )
                self.register._wait_before_oauth_retry(delay)
                self.register._replace_owned_session()
            except CurlError as exc:
                last_error = exc
                can_retry = (
                    attempt < _OAUTH_INIT_MAX_ATTEMPTS
                    and self.register._session_factory is not None
                    and self.register._is_transient_curl_error(exc)
                )
                if not can_retry:
                    raise
                base_delay = min(
                    _OAUTH_INIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                    _OAUTH_INIT_RETRY_MAX_SECONDS,
                )
                delay = base_delay * random.uniform(0.8, 1.2)
                error_code = int(getattr(exc, "code", 0) or 0)
                if callable(self.register.proxy_rotate_callback):
                    self.register._rotate_proxy(f"Sub2 OAuth curl({error_code})")
                self.log(
                    f"Sub2 OAuth 协议登录遇到瞬时 curl({error_code})；"
                    f"{delay:.1f}s 后重试 "
                    f"({attempt + 1}/{_OAUTH_INIT_MAX_ATTEMPTS})"
                )
                self.register._wait_before_oauth_retry(delay)
                self.register._replace_owned_session()
        raise last_error or Sub2ApiLoginError("协议登录失败")

    def _login_to_callback_once(
        self,
        *,
        identity_value: str,
        password: str,
        totp_secret: str,
        auth_url: str,
        expected_state: str,
        redirect_uri: str,
    ) -> str:
        oauth_start = self._oauth_start(
            auth_url=auth_url,
            state=expected_state,
            redirect_uri=redirect_uri,
        )
        self.log("跟随 Sub2 授权地址")
        self.register._follow_authorize_chain(auth_url)
        if is_email_identity(identity_value):
            payload = self._login_email(identity_value, password, totp_secret)
        else:
            payload = self._login_phone(normalize_phone(identity_value), password, totp_secret)
        callback = self._finish_to_callback(payload, oauth_start)
        parsed = validate_callback(callback, expected_state=expected_state)
        self.log(f"已拦截 OAuth callback code={parsed['code'][:8]}...")
        return callback

    def _login_email(self, email: str, password: str, totp_secret: str) -> dict:
        self.log(f"邮箱登录: {email}")
        authorization = self.register._submit_login_email(email)
        payload = self._verify_password(password, after=authorization)
        if _needs_mfa(payload):
            payload = self._complete_mfa(payload, totp_secret)
        elif totp_secret and self.register._looks_like_totp_challenge(
            self._payload_as_response(payload)
        ):
            totp_result = self.register._login_totp(totp_secret, self._payload_as_response(payload))
            payload = totp_result.get("payload") or payload
            if totp_result.get("continue_url"):
                payload = {**payload, "continue_url": totp_result["continue_url"]}
        return payload

    def _login_phone(self, phone: str, password: str, totp_secret: str) -> dict:
        self.log(f"手机号登录: {phone}")
        last_error: Exception | None = None
        for referer, screen_hint in (
            (f"{OPENAI_AUTH}/log-in-or-create-account?usernameKind=phone_number", "login_or_signup"),
            (f"{OPENAI_AUTH}/log-in?usernameKind=phone_number", "login"),
        ):
            try:
                _response, payload = self._post_account_json(
                    OPENAI_API_ENDPOINTS["signup"],
                    {
                        "username": {"kind": "phone_number", "value": phone},
                        "screen_hint": screen_hint,
                    },
                    referer=referer,
                    flow="authorize_continue",
                )
                if _needs_add_email("", payload):
                    return self._bind_email(payload)
                payload = self._verify_password(password, after=payload)
                if _needs_mfa(payload):
                    payload = self._complete_mfa(payload, totp_secret)
                if _needs_add_email(_authorization_continue_url(payload), payload):
                    payload = self._bind_email(payload)
                return payload
            except Exception as exc:
                last_error = exc
                logger.warning("phone continue failed screen_hint=%s: %s", screen_hint, exc)
        raise last_error or Sub2ApiLoginError("手机号密码登录失败")

    def _verify_password(self, password: str, *, after: dict) -> dict:
        _response, payload = self._post_account_json(
            OPENAI_API_ENDPOINTS["password_verify"],
            {"password": password},
            referer=f"{OPENAI_AUTH}/log-in/password",
            flow="password_verify",
        )
        if not payload and after:
            return after
        return payload

    def _complete_mfa(self, payload: dict, totp_secret: str) -> dict:
        secret = str(totp_secret or self.register.totp_secret or "").strip()
        if not secret:
            raise Sub2ApiLoginError("账号开启了 2FA，但本地没有 totp_secret")
        factor_id, factor_type = _pick_totp_factor(payload)
        if not factor_id:
            raise Sub2ApiLoginError("需要 MFA，但响应未提供 factor_id")
        self.log("提交 TOTP")
        _response, issued = self._post_account_json(
            OPENAI_API_ENDPOINTS["mfa_issue_challenge"],
            {"id": factor_id, "type": factor_type or "totp", "force_fresh_challenge": False},
            referer=f"{OPENAI_AUTH}/log-in/password",
        )
        _response, verified = self._post_account_json(
            OPENAI_API_ENDPOINTS["mfa_verify"],
            {
                "id": factor_id,
                "type": factor_type or "totp",
                "code": totp_code(secret),
            },
            referer=f"{OPENAI_AUTH}/mfa-challenge/{factor_id}",
        )
        return verified or issued or payload

    def _bind_email(self, payload: dict) -> dict:
        mailbox = None
        leased = None
        try:
            mailbox, leased, before_ids = lease_mailbox_for_add_email(proxy=self.register.proxy)
            email = str(leased.email or "").strip()
            if not email:
                raise Sub2ApiLoginError("邮箱池未返回可用邮箱")
            self.log(f"add-email 领取邮箱: {email}")
            _response, sent = self._post_account_json(
                OPENAI_API_ENDPOINTS["add_email_send"],
                {"email": email},
                referer=f"{OPENAI_AUTH}/add-email",
                flow="authorize_continue",
            )
            if _authorization_continue_url(sent) and not _needs_add_email("", sent):
                commit = getattr(mailbox, "commit_email", None)
                if callable(commit):
                    commit(leased)
                return sent
            code = str(
                mailbox.wait_for_code(leased, timeout=120, before_ids=before_ids) or ""
            ).strip()
            if not code:
                raise Sub2ApiLoginError("等待 add-email 验证码超时")
            _response, validated = self._post_account_json(
                OPENAI_API_ENDPOINTS["validate_otp"],
                {"code": code},
                referer=f"{OPENAI_AUTH}/email-verification",
                flow="email_otp_validate",
            )
            commit = getattr(mailbox, "commit_email", None)
            if callable(commit):
                commit(leased)
            return validated or sent
        except Exception:
            if mailbox is not None and leased is not None:
                release = getattr(mailbox, "release_email", None)
                if callable(release):
                    try:
                        release(leased)
                    except Exception:
                        pass
            raise

    def _finish_to_callback(self, payload: dict, oauth_start: OAuthStart) -> str:
        continue_url = _authorization_continue_url(payload)
        if continue_url and _oauth_callback_target(continue_url, oauth_start):
            return continue_url
        workspace_id, _org_id, _project_id = _workspace_and_org_from_payload(payload)
        if not workspace_id:
            dump = self.register.session.get(
                f"{OPENAI_AUTH}/api/accounts/client_auth_session_dump",
                headers={
                    **self.register._common_headers(OPENAI_AUTH),
                    "oai-device-id": self.register.device_id,
                },
                allow_redirects=False,
            )
            workspace_id, _org_id, _project_id = _workspace_and_org_from_payload(
                [_response_json(dump), payload]
            )
        if workspace_id:
            self.log("选择 workspace")
            _response, selected = self._post_account_json(
                OPENAI_API_ENDPOINTS["select_workspace"],
                {"workspace_id": workspace_id},
                referer=OPENAI_AUTH,
            )
            continue_url = _authorization_continue_url(selected) or continue_url
            payload = selected or payload
        if not continue_url:
            continue_url = _authorization_continue_url(payload)
        callback = self.register._follow_codex_callback(oauth_start, continue_url)
        if not callback:
            raise Sub2ApiLoginError("登录后未拦截到 localhost OAuth callback")
        return callback

    @staticmethod
    def _payload_as_response(payload: dict):
        class _Fake:
            text = ""
            url = f"{OPENAI_AUTH}/mfa"
            headers = {}
            status_code = 200

            def json(self):
                return payload

        fake = _Fake()
        fake.text = str(payload)
        fake.url = _authorization_continue_url(payload) or fake.url
        return fake
