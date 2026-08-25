from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.proxy_pool import proxy_pool


router = APIRouter(prefix="/http-proxies", tags=["http-proxies"])


class HttpProxyImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500_000)
    region: str = Field(default="", max_length=64)


class HttpProxyStateRequest(BaseModel):
    is_active: bool


@router.get("")
def list_http_proxies():
    items = proxy_pool.list_items()
    return {
        "items": items,
        "total": len(items),
        "active": sum(1 for item in items if item.get("is_active")),
    }


@router.post("/import")
def import_http_proxies(body: HttpProxyImportRequest):
    result = proxy_pool.import_text(body.text, region=body.region)
    return result


@router.put("/{proxy_id}")
def update_http_proxy(proxy_id: int, body: HttpProxyStateRequest):
    item = proxy_pool.set_active(proxy_id, body.is_active)
    if not item:
        raise HTTPException(404, "代理不存在")
    return item


@router.delete("")
def delete_all_http_proxies():
    deleted = proxy_pool.delete_all()
    return {"ok": True, "deleted": deleted}


@router.delete("/{proxy_id}")
def delete_http_proxy(proxy_id: int):
    if not proxy_pool.delete(proxy_id):
        raise HTTPException(404, "代理不存在")
    return {"ok": True}


@router.post("/{proxy_id}/check")
def check_http_proxy(proxy_id: int):
    item = proxy_pool.check_one(proxy_id)
    if not item:
        raise HTTPException(404, "代理不存在")
    return item
