"""Sub2API OAuth authorization: config + generate/login/exchange/create."""
from __future__ import annotations

import json
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
    DEFAULT_OPENAI_MODELS,
    Sub2ApiError,
    Sub2ApiOAuthClient,
    availability_from_sub2api_account,
    build_sub2api_model_mapping,
    extract_sub2api_account_id,
    is_sol_terra_free_target,
    model_mapping_from_sub2api_account,
    models_from_sub2api_account,
    patch_sol_terra_model_mapping,
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
    models: tuple[str, ...] = ()
    model_mapping: tuple[tuple[str, str], ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


def _parse_csv_values(value: str) -> list[str]:
    items: list[str] = []
    for part in str(value or "").replace("，", ",").split(","):
        text = part.strip()
        if text:
            items.append(text)
    return items


def _parse_group_ids(value: str) -> list[int]:
    ids: list[int] = []
    for text in _parse_csv_values(value):
        try:
            ids.append(int(text))
        except ValueError as exc:
            raise ValueError(f"分组 ID 无效: {text}") from exc
    return ids


def _parse_model_mapping(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("模型映射必须是 JSON 对象，例如 {\"gpt-5\":\"gpt-5.4\"}") from exc
    if not isinstance(payload, dict):
        raise ValueError("模型映射必须是 JSON 对象")
    mapping: dict[str, str] = {}
    for source, target in payload.items():
        from_model = str(source or "").strip()
        to_model = str(target or "").strip()
        if from_model and to_model:
            mapping[from_model] = to_model
    return mapping


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
        models = _parse_csv_values(data.get("sub2api_models", ""))
        mapping = _parse_model_mapping(data.get("sub2api_model_mapping", ""))
    except ValueError as exc:
        raise Sub2ApiError(str(exc)) from exc
    return Sub2ApiSettings(
        base_url=str(data.get("sub2api_url") or "").strip(),
        api_key=str(data.get("sub2api_api_key") or "").strip(),
        concurrency=concurrency,
        priority=priority,
        group_ids=tuple(group_ids),
        models=tuple(models),
        model_mapping=tuple(mapping.items()),
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
        model_mapping=build_sub2api_model_mapping(
            list(resolved.models),
            dict(resolved.model_mapping),
        ),
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


def list_sub2api_groups(data: dict[str, str] | None = None) -> dict[str, Any]:
    payload = dict(data or {})
    settings = load_sub2api_settings()
    client = client_from_settings(
        settings,
        base_url_override=str(payload.get("sub2api_url") or "").strip(),
        api_key_override=str(payload.get("sub2api_api_key") or "").strip(),
    )
    return {"items": client.list_groups()}


def list_sub2api_models(data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(data or {})
    settings = load_sub2api_settings()
    client = client_from_settings(
        settings,
        base_url_override=str(payload.get("sub2api_url") or "").strip(),
        api_key_override=str(payload.get("sub2api_api_key") or "").strip(),
    )
    group_id = 0
    raw_group = payload.get("group_id")
    if raw_group not in (None, ""):
        try:
            group_id = max(int(raw_group), 0)
        except (TypeError, ValueError) as exc:
            raise Sub2ApiError("分组 ID 无效") from exc
    if group_id <= 0 and settings.group_ids:
        group_id = int(settings.group_ids[0])
    return {"items": client.list_models(group_id=group_id)}


def _groups_from_sub2_account(item: dict[str, Any]) -> list[dict[str, str]]:
    groups: list[dict[str, str]] = []
    seen: set[str] = set()
    raw_groups = item.get("groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            group_id = str(group.get("id") or "").strip()
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            groups.append(
                {
                    "id": group_id,
                    "name": str(group.get("name") or group.get("title") or group_id).strip() or group_id,
                }
            )
    raw_ids = item.get("group_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            group_id = str(value or "").strip()
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            groups.append({"id": group_id, "name": f"#{group_id}"})
    return groups


def _extra_usage(item: dict[str, Any]) -> dict[str, float | None]:
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    def _percent(key: str) -> float | None:
        raw = extra.get(key)
        try:
            return float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return {
        "codex_5h_used_percent": _percent("codex_5h_used_percent"),
        "codex_7d_used_percent": _percent("codex_7d_used_percent"),
    }


def _collect_sub2_accounts(client: Sub2ApiOAuthClient, wanted_ids: set[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    page = 1
    total = 1
    while page <= 5 and len(found) < len(wanted_ids):
        items, total = client.list_accounts(page=page, page_size=100)
        for item in items:
            account_id = str(item.get("id") or "").strip()
            if account_id in wanted_ids:
                found[account_id] = item
        if not items or page * 100 >= int(total or 0):
            break
        page += 1
    for account_id in list(wanted_ids - set(found)):
        try:
            numeric_id = int(account_id)
        except ValueError:
            continue
        item = client.get_account(numeric_id)
        if item:
            found[account_id] = item
    return found


def monitor_local_sub2api_accounts() -> dict[str, Any]:
    settings = require_sub2api_configured()
    client = client_from_settings(settings)
    records = AccountsService().repository.list_sub2api_authorized(platform="chatgpt")
    wanted: dict[str, Any] = {}
    local_rows: list[dict[str, Any]] = []
    for record in records:
        overview = dict(record.overview or {})
        sub2_id = str(overview.get("sub2_account_id") or "").strip()
        if not sub2_id:
            continue
        wanted[sub2_id] = record
        local_rows.append(
            {
                "account_id": record.id,
                "email": record.email,
                "sub2_account_id": sub2_id,
                "authorized_at": str(overview.get("sub2api_authorized_at") or ""),
                "authorize_status": str(overview.get("sub2api_authorize_status") or "idle"),
            }
        )
    remote = _collect_sub2_accounts(client, set(wanted)) if wanted else {}
    numeric_ids: list[int] = []
    for key in remote:
        try:
            numeric_ids.append(int(key))
        except ValueError:
            continue
    today_stats = client.batch_today_stats(numeric_ids) if numeric_ids else {}

    items: list[dict[str, Any]] = []
    summary = {
        "total": 0,
        "available": 0,
        "in_use": 0,
        "error": 0,
        "rate_limited": 0,
        "inactive": 0,
        "missing": 0,
        "paused": 0,
        "unschedulable": 0,
    }
    for row in local_rows:
        remote_item = remote.get(row["sub2_account_id"])
        availability = availability_from_sub2api_account(remote_item)
        models = models_from_sub2api_account(remote_item) if remote_item else []
        extra_usage = _extra_usage(remote_item or {})
        stats = today_stats.get(row["sub2_account_id"]) or {}
        if not stats and str(row["sub2_account_id"]).isdigit():
            stats = today_stats.get(str(int(row["sub2_account_id"]))) or {}
        current_concurrency = 0
        try:
            current_concurrency = int((remote_item or {}).get("current_concurrency") or 0)
        except (TypeError, ValueError):
            current_concurrency = 0
        in_use = current_concurrency > 0
        summary["total"] += 1
        if availability in summary:
            summary[availability] += 1
        if in_use:
            summary["in_use"] += 1
        items.append(
            {
                **row,
                "sub2_found": bool(remote_item),
                "name": str((remote_item or {}).get("name") or row["email"]),
                "status": str((remote_item or {}).get("status") or ("missing" if not remote_item else "")),
                "availability": availability,
                "schedulable": bool((remote_item or {}).get("schedulable", False)) if remote_item else False,
                "in_use": in_use,
                "current_concurrency": current_concurrency,
                "concurrency": int((remote_item or {}).get("concurrency") or 0) if remote_item else 0,
                "groups": _groups_from_sub2_account(remote_item or {}),
                "models": models,
                "models_unlimited": bool(remote_item) and not models,
                "error_message": str((remote_item or {}).get("error_message") or ""),
                "last_used_at": str((remote_item or {}).get("last_used_at") or ""),
                "rate_limited_until": str((remote_item or {}).get("rate_limit_reset_at") or ""),
                "temp_unschedulable_until": str((remote_item or {}).get("temp_unschedulable_until") or ""),
                "codex_5h_used_percent": extra_usage["codex_5h_used_percent"],
                "codex_7d_used_percent": extra_usage["codex_7d_used_percent"],
                "today_requests": int(stats.get("requests") or 0) if stats else 0,
                "today_tokens": int(stats.get("tokens") or 0) if stats else 0,
                "today_cost": float(stats.get("cost") or 0) if stats else 0.0,
                "can_reauthorize": availability in {"error", "missing"}
                or bool(str((remote_item or {}).get("error_message") or "").strip()),
            }
        )
    return {"ok": True, "configured": True, "summary": summary, "items": items}


def preview_sol_terra_mapping() -> dict[str, Any]:
    items = _load_active_free_sub2_accounts()
    return {
        "ok": True,
        "total": len(items),
        "items": items[:20],
    }


def apply_sol_terra_mapping(*, enable: bool) -> dict[str, Any]:
    settings = require_sub2api_configured()
    client = client_from_settings(settings)
    candidates = _load_active_free_sub2_accounts(client=client)
    fallback = build_sub2api_model_mapping(list(DEFAULT_OPENAI_MODELS), {}) if enable else {}
    updated_ids: list[str] = []
    skipped_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    for item in candidates:
        sub2_id = str(item.get("sub2_account_id") or "").strip()
        try:
            remote_id = int(sub2_id)
        except ValueError:
            errors.append({"id": sub2_id, "message": "Sub2 账号 ID 无效"})
            continue
        try:
            current = client.get_account(remote_id) or {}
            if str(current.get("status") or "").strip() != "active":
                errors.append({"id": sub2_id, "message": "账号已不是正常状态，已跳过"})
                continue
            if not is_sol_terra_free_target(
                current,
                local_plan_state=str(item.get("plan_state") or ""),
                has_local_record=bool(item.get("has_local_record")),
            ):
                errors.append({"id": sub2_id, "message": "不是 free 账号，已跳过"})
                continue
            credentials = dict(current.get("credentials") or {}) if isinstance(current.get("credentials"), dict) else {}
            mapping = model_mapping_from_sub2api_account(current)
            next_mapping = patch_sol_terra_model_mapping(
                mapping,
                enable=enable,
                fallback_mapping=fallback,
            )
            if next_mapping == mapping:
                skipped_ids.append(sub2_id)
                continue
            credentials["model_mapping"] = next_mapping
            client.update_account_credentials(remote_id, credentials)
            updated_ids.append(sub2_id)
        except Sub2ApiError as exc:
            errors.append({"id": sub2_id, "message": str(exc)})
        except Exception as exc:
            errors.append({"id": sub2_id, "message": str(exc) or "写入映射失败"})
    success = len(updated_ids) + len(skipped_ids)
    return {
        "ok": True,
        "enable": bool(enable),
        "total": len(candidates),
        "success": success,
        "failed": len(errors),
        "updated": len(updated_ids),
        "skipped": len(skipped_ids),
        "updated_ids": updated_ids,
        "errors": errors,
    }


def _load_active_free_sub2_accounts(client: Sub2ApiOAuthClient | None = None) -> list[dict[str, Any]]:
    settings = require_sub2api_configured()
    oauth_client = client or client_from_settings(settings)
    local_by_id: dict[str, Any] = {}
    for record in AccountsService().repository.list_sub2api_authorized(platform="chatgpt"):
        overview = dict(record.overview or {})
        sub2_id = str(overview.get("sub2_account_id") or "").strip()
        if not sub2_id:
            continue
        local_by_id[sub2_id] = record

    remote_rows: list[dict[str, Any]] = []
    try:
        remote_rows = oauth_client.list_all_accounts(account_type="")
    except Sub2ApiError:
        remote_rows = []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(remote: dict[str, Any], *, record: Any | None) -> None:
        account_id = str(remote.get("id") or "").strip()
        if not account_id or account_id in seen:
            return
        if str(remote.get("status") or "").strip() != "active":
            return
        if not is_sol_terra_free_target(
            remote,
            local_plan_state=str(getattr(record, "plan_state", "") or ""),
            has_local_record=record is not None,
        ):
            return
        seen.add(account_id)
        items.append(
            {
                "id": int(account_id) if str(account_id).isdigit() else account_id,
                "sub2_account_id": account_id,
                "email": str(getattr(record, "email", "") or remote.get("name") or account_id),
                "name": str(remote.get("name") or getattr(record, "email", "") or account_id),
                "plan_state": str(getattr(record, "plan_state", "") or ""),
                "has_local_record": record is not None,
            }
        )

    for row in remote_rows:
        account_id = str(row.get("id") or "").strip()
        record = local_by_id.get(account_id)
        remote = row
        if not isinstance(row.get("credentials"), dict):
            try:
                numeric_id = int(account_id)
            except ValueError:
                continue
            fetched = oauth_client.get_account(numeric_id)
            if fetched:
                remote = fetched
        _append(remote, record=record)

    for sub2_id, record in local_by_id.items():
        if sub2_id in seen:
            continue
        try:
            remote = oauth_client.get_account(int(sub2_id))
        except ValueError:
            continue
        if remote:
            _append(remote, record=record)
    return items


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
    proxy: str | None = None,
    proxy_rotate_callback: Callable[[], str | None] | None = None,
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
        proxy=proxy,
        proxy_rotate_callback=proxy_rotate_callback,
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
    overview = item.get("overview") or {}
    existing_id = str(overview.get("sub2_account_id") or "").strip()
    reused = False
    sub2_account_id = ""
    if existing_id:
        try:
            remote_id = int(existing_id)
        except ValueError:
            remote_id = 0
        remote = oauth_client.get_account(remote_id) if remote_id else None
        if remote:
            log(f"在 Sub2API 重新授权账号 {existing_id}")
            applied = oauth_client.apply_oauth_credentials(remote_id, tokens)
            sub2_account_id = extract_sub2api_account_id(applied) or existing_id
            reused = True
    if not reused:
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
    log(f"{'重新授权' if reused else '授权'}完成，Sub2 账号 {sub2_account_id}")
    return {
        "account_id": int(account_id),
        "sub2_account_id": sub2_account_id,
        "authorized_at": authorized_at,
        "reauthorized": reused,
        "callback": parse_oauth_callback(callback_url),
    }
