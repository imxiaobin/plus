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
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

GENERATE_AUTH_URL_PATH = "/api/v1/admin/openai/generate-auth-url"
EXCHANGE_CODE_PATH = "/api/v1/admin/openai/exchange-code"
CREATE_ACCOUNT_PATH = "/api/v1/admin/accounts"

_REDACTED = "***"


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
        request_fn=None,
    ):
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout = max(float(timeout or 30), 1.0)
        self.concurrency = max(int(concurrency or 3), 1)
        self.priority = int(priority or 50)
        self.group_ids = [int(item) for item in list(group_ids or [])]
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

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = join_sub2api_url(self.base_url, path)
        logger.info("Sub2API %s %s", method.upper(), path)
        try:
            response = self._request_fn(
                method.upper(),
                url,
                headers=self._headers(),
                json=body if body is not None else {},
                timeout=self.timeout,
            )
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
        payload = self._request("POST", CREATE_ACCOUNT_PATH, body)
        account_id = extract_sub2api_account_id(payload)
        logger.info("Sub2API 创建 oauth 账号 name=%s id=%s", account_name, account_id or "-")
        return payload if isinstance(payload, dict) else {"raw": payload}

    def test_connection(self) -> dict[str, str]:
        session = self.generate_auth_url()
        return {
            "ok": "1",
            "session_id": session["session_id"],
            "state": session["state"],
        }
