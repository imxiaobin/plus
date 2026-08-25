"""代理池 - 从数据库读取代理，支持轮询、批量导入和按区域选取"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from sqlmodel import Session, delete, select

from core.datetime_utils import serialize_datetime
from core.proxy_url import preflight_proxy, redact_proxy_url, to_proxy_url

from .db import ProxyModel, engine


class ProxyPool:
    def __init__(self):
        self._index = 0
        self._lock = threading.Lock()

    def get_next(self, region: str = "") -> Optional[str]:
        """获取下一个可用代理。

        优先级:
          1. 动态代理 provider（如果已配置且启用）
          2. 静态代理池里 region 匹配的代理
          3. 静态代理池里**任意**可用代理（软回退——region 不匹配总比无代理强）
        """
        # 1. 尝试动态代理
        try:
            from core.proxy_providers import get_dynamic_proxy
            dynamic = get_dynamic_proxy()
            if dynamic:
                return dynamic
        except Exception:
            pass

        # 2/3. 静态代理池：先按 region 严格匹配，没有再回退到任意代理
        with Session(engine) as s:
            all_active = s.exec(
                select(ProxyModel).where(ProxyModel.is_active == True)
            ).all()
            if not all_active:
                return None
            preferred = (
                [p for p in all_active if (p.region or "") == region]
                if region
                else list(all_active)
            )
            pool = preferred if preferred else list(all_active)
            pool.sort(
                key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
                reverse=True,
            )
            with self._lock:
                idx = self._index % len(pool)
                self._index += 1
            return pool[idx].url

    def get_next_static(self, region: str = "") -> Optional[str]:
        """Round-robin the imported HTTP proxy list; skip provider-settings dynamic IP."""
        with Session(engine) as s:
            all_active = s.exec(
                select(ProxyModel).where(ProxyModel.is_active == True)
            ).all()
            if not all_active:
                return None
            preferred = (
                [p for p in all_active if (p.region or "") == region]
                if region
                else list(all_active)
            )
            pool = preferred if preferred else list(all_active)
            with self._lock:
                idx = self._index % len(pool)
                self._index += 1
            return pool[idx].url

    def active_count(self) -> int:
        with Session(engine) as s:
            return len(s.exec(select(ProxyModel).where(ProxyModel.is_active == True)).all())

    def list_items(self) -> list[dict]:
        with Session(engine) as s:
            rows = list(s.exec(select(ProxyModel)).all())
        rows.sort(key=lambda row: int(row.id or 0), reverse=True)
        return [_proxy_to_dict(row) for row in rows]

    def import_text(self, text: str, *, region: str = "") -> dict:
        candidates = list(_iter_import_candidates(text))
        imported = 0
        skipped = 0
        invalid: list[str] = []
        with Session(engine) as s:
            existing = {str(row.url) for row in s.exec(select(ProxyModel)).all()}
            seen: set[str] = set()
            for raw in candidates:
                url = to_proxy_url(raw)
                if not url:
                    invalid.append(raw[:160])
                    continue
                if url in existing or url in seen:
                    skipped += 1
                    continue
                seen.add(url)
                s.add(ProxyModel(url=url, region=str(region or "").strip(), is_active=True))
                imported += 1
            s.commit()
        return {
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "total": self.active_count(),
        }

    def set_active(self, proxy_id: int, is_active: bool) -> dict | None:
        with Session(engine) as s:
            row = s.get(ProxyModel, int(proxy_id))
            if not row:
                return None
            row.is_active = bool(is_active)
            s.add(row)
            s.commit()
            s.refresh(row)
            return _proxy_to_dict(row)

    def delete(self, proxy_id: int) -> bool:
        with Session(engine) as s:
            row = s.get(ProxyModel, int(proxy_id))
            if not row:
                return False
            s.delete(row)
            s.commit()
            return True

    def delete_all(self) -> int:
        with Session(engine) as s:
            count = len(list(s.exec(select(ProxyModel)).all()))
            if count:
                s.exec(delete(ProxyModel))
                s.commit()
        with self._lock:
            self._index = 0
        return count

    def check_one(self, proxy_id: int) -> dict | None:
        with Session(engine) as s:
            row = s.get(ProxyModel, int(proxy_id))
            if not row:
                return None
            url = str(row.url)
        ok, detail = preflight_proxy(url)
        if ok:
            self.report_success(url)
        else:
            self.report_fail(url)
        with Session(engine) as s:
            row = s.get(ProxyModel, int(proxy_id))
            payload = _proxy_to_dict(row) if row else {"id": proxy_id}
        payload["ok"] = ok
        payload["detail"] = detail if ok else detail
        if ok:
            payload["detail"] = f"出口 IP {detail}"
        return payload

    def report_success(self, url: str) -> None:
        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if p:
                p.success_count += 1
                p.last_checked = datetime.now(timezone.utc)
                s.add(p)
                s.commit()

    def report_fail(self, url: str) -> None:
        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if p:
                p.fail_count += 1
                p.last_checked = datetime.now(timezone.utc)
                # 连续失败超过10次自动禁用
                if p.fail_count > 0 and p.success_count == 0 and p.fail_count >= 5:
                    p.is_active = False
                s.add(p)
                s.commit()

    def check_all(self) -> dict:
        """检测所有代理可用性"""
        import requests
        with Session(engine) as s:
            proxies = s.exec(select(ProxyModel)).all()
        results = {"ok": 0, "fail": 0}
        for p in proxies:
            try:
                r = requests.get("https://httpbin.org/ip",
                                 proxies={"http": p.url, "https": p.url},
                                 timeout=8)
                if r.status_code == 200:
                    self.report_success(p.url)
                    results["ok"] += 1
                    continue
            except Exception:
                pass
            self.report_fail(p.url)
            results["fail"] += 1
        return results


def _iter_import_candidates(text: str):
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in raw.split("\n"):
        piece = line.strip()
        if not piece or piece.startswith("#"):
            continue
        yield piece


def _proxy_to_dict(row: ProxyModel) -> dict:
    parsed = urlsplit(str(row.url or ""))
    return {
        "id": row.id,
        "url": redact_proxy_url(str(row.url or "")),
        "host": parsed.hostname or "",
        "port": parsed.port,
        "user": parsed.username or "",
        "region": row.region or "",
        "is_active": bool(row.is_active),
        "success_count": int(row.success_count or 0),
        "fail_count": int(row.fail_count or 0),
        "last_checked": serialize_datetime(row.last_checked) if row.last_checked else None,
    }


proxy_pool = ProxyPool()
