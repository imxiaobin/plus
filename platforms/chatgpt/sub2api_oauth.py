"""Sub2API Codex OAuth admin client.

PKCE lives on Sub2. This client only:
  1. POST /api/v1/admin/openai/generate-auth-url
  2. POST /api/v1/admin/openai/exchange-code
  3. POST /api/v1/admin/accounts  (type=oauth)

Never call local ``/oauth/token`` or ``import/codex-session``.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

GENERATE_AUTH_URL_PATH = "/api/v1/admin/openai/generate-auth-url"
EXCHANGE_CODE_PATH = "/api/v1/admin/openai/exchange-code"
CREATE_ACCOUNT_PATH = "/api/v1/admin/accounts"
GET_ACCOUNT_PATH = "/api/v1/admin/accounts/{account_id}"
UPDATE_ACCOUNT_PATH = "/api/v1/admin/accounts/{account_id}"
APPLY_OAUTH_CREDENTIALS_PATH = "/api/v1/admin/accounts/{account_id}/apply-oauth-credentials"
LIST_ACCOUNTS_PATH = "/api/v1/admin/accounts"
BATCH_TODAY_STATS_PATH = "/api/v1/admin/accounts/today-stats/batch"
LIST_GROUPS_ALL_PATH = "/api/v1/admin/groups/all"
LIST_GROUPS_PATH = "/api/v1/admin/groups"
LIST_MODELS_CANDIDATES_PATH = "/api/v1/admin/groups/{group_id}/models-list-candidates"
SOL_TERRA_SOURCE = "gpt-5.6-sol"
SOL_TERRA_TARGET = "gpt-5.6-terra"
_NON_PLAN_MARKERS = {"", "unknown", "none", "-"}

DEFAULT_OPENAI_MODELS = (
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
    "codex-auto-review",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-image-1",
    "gpt-image-1.5",
    "gpt-image-2",
)


class Sub2ApiError(RuntimeError):
    """Raised when Sub2API is misconfigured or a request fails."""


def join_sub2api_url(base_url: str, path: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise Sub2ApiError("Sub2API 地址必须是完整的 http:// 或 https:// 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Sub2ApiError("Sub2API 地址不能包含账号、查询参数或片段")
    normalized = str(path or "").strip() or "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if cleaned.endswith("/api/v1") and normalized.startswith("/api/v1/"):
        return cleaned + normalized[len("/api/v1") :]
    return cleaned + normalized


def extract_sub2api_groups(payload: Any) -> list[dict[str, str]]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("items", "groups", "list"):
                raw = data.get(key)
                if isinstance(raw, list):
                    items = raw
                    break
            if not items and data.get("id") not in (None, ""):
                items = [data]
        if not items:
            for key in ("items", "groups"):
                raw = payload.get(key)
                if isinstance(raw, list):
                    items = raw
                    break
    groups: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        group_id = _first_non_empty(item.get("id"), item.get("group_id"), item.get("groupId"))
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        groups.append(
            {
                "id": group_id,
                "name": _first_non_empty(item.get("name"), item.get("title"), item.get("label"), group_id),
                "platform": _first_non_empty(item.get("platform")),
                "status": _first_non_empty(item.get("status")),
            }
        )
    return groups


def extract_sub2api_models(payload: Any) -> list[str]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("models", "items", "list"):
                raw = data.get(key)
                if isinstance(raw, list):
                    items = raw
                    break
        if not items:
            for key in ("models", "items"):
                raw = payload.get(key)
                if isinstance(raw, list):
                    items = raw
                    break
    models: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            model_id = _first_non_empty(item.get("id"), item.get("model"), item.get("name"))
        else:
            model_id = ""
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(model_id)
    return models


def build_sub2api_model_mapping(
    allowed_models: list[str] | None = None,
    mapping: dict[str, str] | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for model in list(allowed_models or []):
        text = str(model or "").strip()
        if not text or "*" in text:
            continue
        result[text] = text
    for source, target in dict(mapping or {}).items():
        from_model = str(source or "").strip()
        to_model = str(target or "").strip()
        if not from_model or not to_model or "*" in to_model:
            continue
        result[from_model] = to_model
    return result


def extract_sub2api_account_list(payload: Any) -> tuple[list[dict[str, Any]], int]:
    items: list[Any] = []
    total = 0
    if isinstance(payload, list):
        items = payload
        total = len(items)
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            items = data
            total = int(payload.get("total") or len(items) or 0)
        elif isinstance(data, dict):
            for key in ("items", "accounts", "list"):
                raw = data.get(key)
                if isinstance(raw, list):
                    items = raw
                    break
            if not items and data.get("id") not in (None, ""):
                items = [data]
            total = int(data.get("total") or payload.get("total") or len(items) or 0)
        if not items:
            raw = payload.get("items")
            if isinstance(raw, list):
                items = raw
                total = int(payload.get("total") or len(items) or 0)
    accounts = [item for item in items if isinstance(item, dict)]
    return accounts, max(total, len(accounts))


def model_mapping_from_sub2api_account(item: dict[str, Any] | None) -> dict[str, str]:
    mapping: dict[str, Any] = {}
    row = item if isinstance(item, dict) else {}
    credentials = row.get("credentials")
    extra = row.get("extra")
    if isinstance(credentials, dict) and isinstance(credentials.get("model_mapping"), dict):
        mapping = credentials.get("model_mapping") or {}
    elif isinstance(extra, dict) and isinstance(extra.get("model_mapping"), dict):
        mapping = extra.get("model_mapping") or {}
    result: dict[str, str] = {}
    for source, target in dict(mapping or {}).items():
        from_model = str(source or "").strip()
        to_model = str(target or "").strip()
        if not from_model or not to_model or "*" in from_model or "*" in to_model:
            continue
        result[from_model] = to_model
    return result


def models_from_sub2api_account(item: dict[str, Any]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for source, target in model_mapping_from_sub2api_account(item).items():
        for part in (source, target):
            if not part or part in seen:
                continue
            seen.add(part)
            models.append(part)
    return models


def plan_type_from_sub2api_account(item: dict[str, Any] | None) -> str:
    row = item if isinstance(item, dict) else {}
    credentials = row.get("credentials") if isinstance(row.get("credentials"), dict) else {}
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    for source in (row, credentials, extra):
        if not isinstance(source, dict):
            continue
        for key in ("current_plan_type", "plan_type", "chatgpt_plan_type"):
            text = str(source.get(key) or "").strip().lower()
            if text and text not in _NON_PLAN_MARKERS:
                return text
    return ""


def is_explicit_free_sub2_account(item: dict[str, Any] | None) -> bool:
    return plan_type_from_sub2api_account(item) == "free"


_PLUS_PLAN_MARKERS = {"plus", "subscribed", "pro", "team", "business", "enterprise"}


def is_sol_terra_free_target(
    item: dict[str, Any] | None,
    *,
    local_plan_state: str = "",
    has_local_record: bool = False,
) -> bool:
    """Treat as free unless Sub2 or local graph clearly marks a paid plan.

    Local authorized accounts with no plan mark still count as free.
    Sub2-only accounts must be explicitly marked free.
    """
    remote_plan = plan_type_from_sub2api_account(item)
    local_plan = str(local_plan_state or "").strip().lower()
    if remote_plan in _PLUS_PLAN_MARKERS or local_plan in _PLUS_PLAN_MARKERS:
        return False
    if remote_plan == "free" or local_plan == "free":
        return True
    return bool(has_local_record) and not remote_plan and local_plan in {"", "unknown"}


def patch_sol_terra_model_mapping(
    mapping: dict[str, str] | None,
    *,
    enable: bool,
    fallback_mapping: dict[str, str] | None = None,
) -> dict[str, str]:
    """Add or undo gpt-5.6-sol → gpt-5.6-terra on an existing mapping."""
    out: dict[str, str] = {}
    for source, target in dict(mapping or {}).items():
        from_model = str(source or "").strip()
        to_model = str(target or "").strip()
        if not from_model or not to_model or "*" in from_model or "*" in to_model:
            continue
        out[from_model] = to_model
    if enable:
        if not out:
            for source, target in dict(fallback_mapping or {}).items():
                from_model = str(source or "").strip()
                to_model = str(target or "").strip()
                if from_model and to_model:
                    out[from_model] = to_model
        out[SOL_TERRA_SOURCE] = SOL_TERRA_TARGET
        return out
    if out.get(SOL_TERRA_SOURCE) == SOL_TERRA_TARGET:
        out[SOL_TERRA_SOURCE] = SOL_TERRA_SOURCE
    return out


def availability_from_sub2api_account(item: dict[str, Any] | None) -> str:
    if not item:
        return "missing"
    status = str(item.get("status") or "").strip().lower()
    if status == "error":
        return "error"
    if str(item.get("temp_unschedulable_until") or "").strip():
        return "paused"
    if str(item.get("rate_limit_reset_at") or item.get("rate_limited_at") or "").strip():
        return "rate_limited"
    if status == "inactive":
        return "inactive"
    if item.get("schedulable") is False:
        return "unschedulable"
    if status in {"active", ""}:
        return "available"
    return status or "unknown"


def extract_sub2api_account_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates: list[Any] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        items = data.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            candidates.append(items[0])
        account = data.get("account")
        if isinstance(account, dict):
            candidates.append(account)
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        candidates.append(data[0])
    items = payload.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        candidates.append(items[0])
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        for key in ("id", "account_id", "accountId"):
            raw = obj.get(key)
            if raw in (None, ""):
                continue
            return str(raw).strip()
    return ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def state_from_auth_url(auth_url: str) -> str:
    """Sub2 only returns auth_url + session_id; state lives in the authorize URL."""
    parsed = urlparse(str(auth_url or "").strip())
    items = parse_qs(parsed.query, keep_blank_values=True).get("state") or [""]
    return str(items[0] if items else "").strip()


def tokens_from_exchange(payload: dict[str, Any]) -> dict[str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = {}
    credentials = data.get("credentials") if isinstance(data.get("credentials"), dict) else {}

    def pick(*keys: str) -> str:
        for key in keys:
            for source in (data, credentials):
                value = source.get(key)
                if value not in (None, ""):
                    return str(value)
        return ""

    return {
        "access_token": pick("access_token"),
        "refresh_token": pick("refresh_token"),
        "id_token": pick("id_token"),
        "email": pick("email"),
        "account_id": pick("account_id", "chatgpt_account_id"),
        "expires_in": pick("expires_in"),
        "plan_type": pick("plan_type"),
    }


class Sub2ApiOAuthClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30,
        concurrency: int = 3,
        priority: int = 50,
        group_ids: list[int] | None = None,
        model_mapping: dict[str, str] | None = None,
        request_fn=None,
    ):
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout = max(float(timeout or 30), 1.0)
        self.concurrency = max(int(concurrency or 3), 1)
        self.priority = int(priority or 50)
        self.group_ids = [int(item) for item in list(group_ids or [])]
        self.model_mapping = {
            str(key).strip(): str(value).strip()
            for key, value in dict(model_mapping or {}).items()
            if str(key or "").strip() and str(value or "").strip()
        }
        self._request_fn = request_fn or requests.request
        if not self.base_url:
            raise Sub2ApiError("请先在设置中填写 Sub2API")
        if not self.api_key:
            raise Sub2ApiError("请先在设置中填写 Sub2API")

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = join_sub2api_url(self.base_url, path)
        logger.info("Sub2API %s %s", method.upper(), path)
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": self.timeout,
        }
        if method.upper() != "GET":
            kwargs["json"] = body if body is not None else {}
        if params:
            kwargs["params"] = params
        try:
            response = self._request_fn(method.upper(), url, **kwargs)
        except requests.RequestException as exc:
            raise Sub2ApiError(f"Sub2API 请求失败：{exc}") from exc

        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": str(getattr(response, "text", "") or "")[:500]}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        if status < 200 or status >= 300:
            detail = payload.get("detail") or payload.get("message") or payload.get("error") or payload
            raise Sub2ApiError(f"Sub2API 请求失败（HTTP {status}）：{detail}")
        return payload

    def generate_auth_url(self) -> dict[str, str]:
        payload = self._request("POST", GENERATE_AUTH_URL_PATH, {})
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        auth_url = _first_non_empty(
            payload.get("auth_url"),
            payload.get("url"),
            payload.get("authUrl"),
            data.get("auth_url"),
            data.get("url"),
            data.get("authUrl"),
        )
        session_id = _first_non_empty(
            payload.get("session_id"),
            payload.get("sessionId"),
            data.get("session_id"),
            data.get("sessionId"),
        )
        state = _first_non_empty(
            payload.get("state"),
            payload.get("auth_state"),
            payload.get("authState"),
            data.get("state"),
            data.get("auth_state"),
            data.get("authState"),
            state_from_auth_url(auth_url),
        )
        if not auth_url.startswith("http"):
            raise Sub2ApiError("Sub2API 未返回有效 auth_url")
        if not session_id:
            raise Sub2ApiError("Sub2API 授权会话缺少 session_id")
        if not state:
            raise Sub2ApiError("Sub2API 授权会话缺少 state")
        logger.info("Sub2API generate-auth-url ok session_id=%s...", session_id[:12])
        return {
            "auth_url": auth_url,
            "session_id": session_id,
            "state": state,
        }

    def exchange_code(self, *, session_id: str, code: str, state: str) -> dict[str, Any]:
        body = {
            "session_id": str(session_id or "").strip(),
            "code": str(code or "").strip(),
            "state": str(state or "").strip(),
        }
        if not body["session_id"] or not body["code"] or not body["state"]:
            raise Sub2ApiError("exchange-code 需要 session_id、code 和 state")
        logger.info("Sub2API exchange-code session_id=%s...", body["session_id"][:12])
        payload = self._request("POST", EXCHANGE_CODE_PATH, body)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            raise Sub2ApiError("Sub2API exchange-code 响应无效")
        return data

    def create_oauth_account(
        self,
        tokens: dict[str, str],
        *,
        name: str = "",
    ) -> dict[str, Any]:
        credentials = {
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "id_token": str(tokens.get("id_token") or "").strip(),
        }
        if not credentials["access_token"] and not credentials["refresh_token"]:
            raise Sub2ApiError("exchange 结果缺少 access_token/refresh_token")
        account_name = str(name or tokens.get("email") or "unknown").strip()[:64] or "unknown"
        body: dict[str, Any] = {
            "name": account_name,
            "platform": "openai",
            "type": "oauth",
            "credentials": credentials,
            "concurrency": self.concurrency,
            "priority": self.priority,
        }
        if self.group_ids:
            body["group_ids"] = list(self.group_ids)
        if self.model_mapping:
            credentials["model_mapping"] = dict(self.model_mapping)
        payload = self._request("POST", CREATE_ACCOUNT_PATH, body)
        account_id = extract_sub2api_account_id(payload)
        logger.info("Sub2API 创建 oauth 账号 name=%s id=%s", account_name, account_id or "-")
        return payload if isinstance(payload, dict) else {"raw": payload}

    def apply_oauth_credentials(self, account_id: int, tokens: dict[str, str]) -> dict[str, Any]:
        credentials = {
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "id_token": str(tokens.get("id_token") or "").strip(),
        }
        if not credentials["access_token"] and not credentials["refresh_token"]:
            raise Sub2ApiError("exchange 结果缺少 access_token/refresh_token")
        if self.model_mapping:
            credentials["model_mapping"] = dict(self.model_mapping)
        body = {
            "type": "oauth",
            "credentials": credentials,
        }
        payload = self._request(
            "POST",
            APPLY_OAUTH_CREDENTIALS_PATH.format(account_id=int(account_id)),
            body,
        )
        logger.info("Sub2API 重新授权 oauth 账号 id=%s", account_id)
        return payload if isinstance(payload, dict) else {"raw": payload}

    def test_connection(self) -> dict[str, str]:
        session = self.generate_auth_url()
        return {
            "ok": "1",
            "session_id": session["session_id"],
            "state": session["state"],
        }

    def list_groups(self, *, platform: str = "openai") -> list[dict[str, str]]:
        attempts: list[tuple[str, dict[str, Any] | None]] = [
            (LIST_GROUPS_ALL_PATH, {"platform": platform} if platform else None),
            (LIST_GROUPS_ALL_PATH, None),
            (
                LIST_GROUPS_PATH,
                {
                    "page": 1,
                    "page_size": 100,
                    **({"platform": platform} if platform else {}),
                },
            ),
        ]
        last_error: Sub2ApiError | None = None
        empty: list[dict[str, str]] = []
        for path, params in attempts:
            try:
                payload = self._request("GET", path, params=params)
            except Sub2ApiError as exc:
                last_error = exc
                continue
            groups = extract_sub2api_groups(payload)
            if platform:
                filtered = [
                    item
                    for item in groups
                    if not item.get("platform") or item.get("platform") == platform
                ]
                if filtered:
                    return filtered
            if groups:
                return groups
            empty = groups
        if last_error and not empty:
            raise last_error
        return empty

    def list_models(self, *, group_id: int = 0, platform: str = "openai") -> list[str]:
        group = max(int(group_id or 0), 0)
        path = LIST_MODELS_CANDIDATES_PATH.format(group_id=group)
        params = {"platform": platform} if platform else None
        try:
            payload = self._request("GET", path, params=params)
            models = extract_sub2api_models(payload)
        except Sub2ApiError:
            models = []
        if models:
            return models
        return list(DEFAULT_OPENAI_MODELS)

    def list_accounts(
        self,
        *,
        platform: str = "openai",
        account_type: str = "oauth",
        page: int = 1,
        page_size: int = 100,
        search: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, Any] = {
            "page": max(int(page or 1), 1),
            "page_size": min(max(int(page_size or 100), 1), 100),
        }
        if platform:
            params["platform"] = platform
        if account_type:
            params["type"] = account_type
        if str(search or "").strip():
            params["search"] = str(search).strip()
        payload = self._request("GET", LIST_ACCOUNTS_PATH, params=params)
        return extract_sub2api_account_list(payload)

    def list_all_accounts(
        self,
        *,
        platform: str = "openai",
        account_type: str = "oauth",
        page_size: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page = 1
        size = min(max(int(page_size or 100), 1), 100)
        while page <= max(int(max_pages or 1), 1):
            items, total = self.list_accounts(
                platform=platform,
                account_type=account_type,
                page=page,
                page_size=size,
            )
            found.extend(items)
            if not items or page * size >= int(total or 0):
                break
            page += 1
        return found

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        try:
            payload = self._request("GET", GET_ACCOUNT_PATH.format(account_id=int(account_id)))
        except Sub2ApiError:
            return None
        items, _total = extract_sub2api_account_list(payload)
        if items:
            return items[0]
        if isinstance(payload.get("data"), dict) and payload["data"].get("id") not in (None, ""):
            return payload["data"]
        if payload.get("id") not in (None, ""):
            return payload
        return None

    def update_account_credentials(self, account_id: int, credentials: dict[str, Any]) -> dict[str, Any]:
        payload = self._request(
            "PUT",
            UPDATE_ACCOUNT_PATH.format(account_id=int(account_id)),
            {"credentials": dict(credentials or {})},
        )
        logger.info("Sub2API 更新账号凭据 id=%s", account_id)
        return payload if isinstance(payload, dict) else {"raw": payload}

    def enable_account(self, account_id: int) -> dict[str, Any]:
        """启用 Sub2API 账号。"""
        payload = self._request(
            "PUT",
            UPDATE_ACCOUNT_PATH.format(account_id=int(account_id)),
            {"status": "active"},
        )
        logger.info("Sub2API 启用账号 id=%s", account_id)
        return payload if isinstance(payload, dict) else {"raw": payload}

    def batch_today_stats(self, account_ids: list[int]) -> dict[str, dict[str, Any]]:
        ids = [int(item) for item in account_ids if str(item).strip()]
        if not ids:
            return {}
        try:
            payload = self._request("POST", BATCH_TODAY_STATS_PATH, {"account_ids": ids})
        except Sub2ApiError:
            return {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        stats = data.get("stats") if isinstance(data, dict) else None
        if not isinstance(stats, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, value in stats.items():
            if isinstance(value, dict):
                result[str(key)] = value
        return result
