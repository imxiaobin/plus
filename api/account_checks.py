from __future__ import annotations

from fastapi import APIRouter, HTTPException
from typing import Literal

from pydantic import BaseModel, Field

from application.account_checks import AccountChecksService
from core.mihomo_client import MihomoNodeError, MihomoUnavailableError, mihomo_client
from core.proxy_pool import proxy_pool as http_proxy_pool

router = APIRouter(prefix="/accounts", tags=["account-checks"])
service = AccountChecksService()


class RefreshTokenCheckRequest(BaseModel):
    platform: Literal["chatgpt"] = "chatgpt"
    concurrency: int = Field(default=100, ge=1, le=200)
    proxy_node: str | None = None
    http_proxy_pool: bool = False
    # 401 maintenance is browser-first: run the AT check through Camoufox to
    # avoid Cloudflare 403.  ``browser=false`` remains available for protocol
    # diagnostics, but is no longer the normal maintenance path.
    browser: bool = True
    # Optional targeted checks are useful for safe retries and diagnostics;
    # omitting this field keeps the existing all-account behavior.
    account_ids: list[int] | None = Field(default=None, min_length=1, max_length=200)


@router.post("/check-refresh-tokens")
def check_refresh_tokens(body: RefreshTokenCheckRequest):
    proxy_node = str(body.proxy_node or "").strip()
    use_http_proxy_pool = bool(body.http_proxy_pool)
    if use_http_proxy_pool and proxy_node:
        raise HTTPException(400, "HTTP 代理池不能与 Mihomo 节点同时使用")
    if use_http_proxy_pool and http_proxy_pool.active_count() <= 0:
        raise HTTPException(400, "HTTP 代理池没有可用代理，请先在设置中批量导入")
    if proxy_node:
        try:
            mihomo_client.validate_node(proxy_node)
        except MihomoNodeError as exc:
            raise HTTPException(400, str(exc)) from exc
        except MihomoUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
    kwargs = {
        "proxy_node": proxy_node or None,
        "http_proxy_pool": use_http_proxy_pool,
        "browser": body.browser,
    }
    if body.account_ids:
        kwargs["account_ids"] = body.account_ids
    return service.check_refresh_tokens_async(
        body.platform,
        body.concurrency,
        **kwargs,
    )
