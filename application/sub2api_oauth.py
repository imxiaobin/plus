"""Sub2API OAuth authorization: config + generate/login/exchange/create."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from application.accounts import AccountsService
from domain.accounts import AccountUpdateCommand
from infrastructure.config_repository import ConfigRepository
from platforms.chatgpt.constants import CODEX_REDIRECT_URI
from platforms.chatgpt.sub2api_codex_login import (
    Sub2ApiCodexLogin,
    parse_oauth_callback,
    validate_callback,
)
from platforms.chatgpt.sub2api_oauth import (
    Sub2ApiError,
    Sub2ApiOAuthClient,
    extract_sub2api_account_id,
    tokens_from_exchange,
)

SUB2API_NOT_CONFIGURED = "请先在设置中填写 Sub2API"
DEFAULT_CONCURRENCY = 3
DEFAULT_PRIORITY = 50


class Sub2ApiNotConfiguredError(ValueError):
    """Saved Sub2API URL or Admin API Key is missing."""


@dataclass(frozen=True)
class Sub2ApiSettings:
    base_url: str
    api_key: str
    concurrency: int = DEFAULT_CONCURRENCY
    priority: int = DEFAULT_PRIORITY
    group_ids: tuple[int, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


def _parse_group_ids(value: str) -> list[int]:
    ids: list[int] = []
    for part in str(value or "").replace("，", ",").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            ids.append(int(text))
        except ValueError as exc:
            raise ValueError(f"分组 ID 无效: {text}") from exc
    return ids


def _parse_int(value: str, default: int) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    return int(text)


def load_sub2api_settings(repository: ConfigRepository | None = None) -> Sub2ApiSettings:
    store = repository or ConfigRepository()
    data = store.get_flat()
    try:
        concurrency = max(_parse_int(data.get("sub2api_concurrency", ""), DEFAULT_CONCURRENCY), 1)
        priority = _parse_int(data.get("sub2api_priority", ""), DEFAULT_PRIORITY)
        group_ids = _parse_group_ids(data.get("sub2api_group_ids", ""))
    except ValueError as exc:
        raise Sub2ApiError(str(exc)) from exc
    return Sub2ApiSettings(
        base_url=str(data.get("sub2api_url") or "").strip(),
        api_key=str(data.get("sub2api_api_key") or "").strip(),
        concurrency=concurrency,
        priority=priority,
        group_ids=tuple(group_ids),
    )


def require_sub2api_configured(settings: Sub2ApiSettings | None = None) -> Sub2ApiSettings:
    resolved = settings or load_sub2api_settings()
    if not resolved.configured:
        raise Sub2ApiNotConfiguredError(SUB2API_NOT_CONFIGURED)
    parsed = urlparse(resolved.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise Sub2ApiNotConfiguredError(SUB2API_NOT_CONFIGURED)
    return resolved


def client_from_settings(
    settings: Sub2ApiSettings | None = None,
    *,
    api_key_override: str = "",
    base_url_override: str = "",
) -> Sub2ApiOAuthClient:
    resolved = settings or load_sub2api_settings()
    base_url = str(base_url_override or resolved.base_url).strip()
    api_key = str(api_key_override or resolved.api_key).strip()
    if not base_url or not api_key:
        raise Sub2ApiNotConfiguredError(SUB2API_NOT_CONFIGURED)
    return Sub2ApiOAuthClient(
        base_url=base_url,
        api_key=api_key,
        concurrency=resolved.concurrency,
        priority=resolved.priority,
        group_ids=list(resolved.group_ids),
    )


def test_sub2api_connection(data: dict[str, str] | None = None) -> dict[str, Any]:
    payload = dict(data or {})
    settings = load_sub2api_settings()
    client = client_from_settings(
        settings,
        base_url_override=str(payload.get("sub2api_url") or "").strip(),
        api_key_override=str(payload.get("sub2api_api_key") or "").strip(),
    )
    result = client.test_connection()
    return {
        "ok": True,
        "session_id": result.get("session_id") or "",
        "state": result.get("state") or "",
    }


def _totp_secret_from_account(item: dict) -> str:
    for credential in item.get("credentials") or []:
        if str(credential.get("key") or "") == "totp_secret":
            return str(credential.get("value") or "").strip()
    overview = item.get("overview") or {}
    return str(overview.get("totp_secret") or "").strip()


def _patch_overview(account_id: int, updates: dict[str, Any]) -> None:
    AccountsService().update_account(
        int(account_id),
        AccountUpdateCommand(overview=dict(updates or {})),
    )


def authorize_chatgpt_account_to_sub2api(
    account_id: int,
    *,
    log_fn: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    login_factory=None,
    client: Sub2ApiOAuthClient | None = None,
) -> dict[str, Any]:
    log = log_fn or (lambda _message: None)
    cancelled = cancel_check or (lambda: False)
    item = AccountsService().get_account(int(account_id))
    if not item:
        raise ValueError("账号不存在")
    identity = str(item.get("email") or "").strip()
    password = str(item.get("password") or "")
    totp_secret = _totp_secret_from_account(item)
    if not identity or not password:
        raise ValueError("账号缺少登录邮箱/手机号或密码")

    settings = require_sub2api_configured()
    oauth_client = client or client_from_settings(settings)
    log("向 Sub2API 申请授权地址")
    if cancelled():
        raise RuntimeError("任务已取消")
    session = oauth_client.generate_auth_url()
    log("开始协议登录")
    factory = login_factory or Sub2ApiCodexLogin
    login = factory(
        totp_secret=totp_secret,
        log_fn=log,
        cancel_check=cancelled,
    )
    callback_url = login.login_to_callback(
        identity=identity,
        password=password,
        totp_secret=totp_secret,
        auth_url=session["auth_url"],
        expected_state=session["state"],
        redirect_uri=CODEX_REDIRECT_URI,
    )
    parsed = validate_callback(callback_url, expected_state=session["state"])
    if cancelled():
        raise RuntimeError("任务已取消")
    log("向 Sub2API 提交 exchange-code")
    exchange = oauth_client.exchange_code(
        session_id=session["session_id"],
        code=parsed["code"],
        state=parsed["state"] or session["state"],
    )
    tokens = tokens_from_exchange(exchange)
    if cancelled():
        raise RuntimeError("任务已取消")
    log("在 Sub2API 创建 oauth 账号")
    created = oauth_client.create_oauth_account(tokens, name=identity)
    sub2_account_id = extract_sub2api_account_id(created) or extract_sub2api_account_id(exchange)
    if not sub2_account_id:
        raise Sub2ApiError("Sub2API 已换票但未返回账号 ID")
    authorized_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _patch_overview(
        int(account_id),
        {
            "sub2_account_id": sub2_account_id,
            "sub2api_authorized": True,
            "sub2api_authorized_at": authorized_at,
            "sub2api_authorize_status": "idle",
            "sub2api_authorize_error": "",
        },
    )
    log(f"授权完成，Sub2 账号 {sub2_account_id}")
    return {
        "account_id": int(account_id),
        "sub2_account_id": sub2_account_id,
        "authorized_at": authorized_at,
        "callback": parse_oauth_callback(callback_url),
    }
