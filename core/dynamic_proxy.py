"""Task-scoped dynamic proxy manager for registration tasks.

Accepts either:

- an extract API URL (``http://host/gen?...``) that returns ``IP:PORT`` lines
- a rotating residential gateway such as ``host:port:user:pass``

Calling ``get_proxy()`` returns a usable proxy URL. For extract APIs each call
fetches a new exit. For a gateway the same entry is reused; the provider rotates
the exit IP on new connections. The manager is also handed to the platform as
``proxy_rotate_callback`` so a Cloudflare challenge rebuilds the session.
"""
from __future__ import annotations

import threading
from typing import Optional

import requests

from core.proxy_url import is_extract_api_url, preflight_proxy, to_proxy_url


class DynamicProxyManager:
    """Resolve a rotating-residential proxy from an extract API or gateway paste."""

    def __init__(self, api_url: str, *, timeout: int = 12):
        self._api_url = str(api_url or "").strip()
        self._timeout = max(int(timeout or 12), 3)
        self._lock = threading.Lock()
        self._current: Optional[str] = None
        self._mode = "extract_api" if is_extract_api_url(self._api_url) else "gateway"
        self._gateway_url = (
            None if self._mode == "extract_api" else to_proxy_url(self._api_url)
        )

    @property
    def api_url(self) -> str:
        return self._api_url

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def gateway_url(self) -> Optional[str]:
        return self._gateway_url

    def prepare(self) -> tuple[bool, str]:
        """Preflight the HTTP rotating gateway before registration."""
        if self._mode != "gateway":
            return True, ""
        if not self._gateway_url:
            return False, "动态 IP 网关格式无法解析，请使用 host:port:user:pass"
        with self._lock:
            self._current = self._gateway_url
        ok, detail = preflight_proxy(self._gateway_url)
        if ok:
            return True, f"代理预检通过，出口 IP {detail}"
        return False, detail

    def get_proxy(self) -> Optional[str]:
        """Return a proxy URL for the next worker or rotate callback."""
        if not self._api_url:
            return None
        if self._mode == "gateway":
            if not self._gateway_url:
                return None
            with self._lock:
                self._current = self._gateway_url
            return self._gateway_url
        return self._fetch_extract_proxy()

    def rotate(self) -> Optional[str]:
        """Alias used as the platform ``proxy_rotate_callback``."""
        return self.get_proxy()

    def current(self) -> Optional[str]:
        with self._lock:
            return self._current

    def _fetch_extract_proxy(self) -> Optional[str]:
        url = self._api_url
        # The gateway split token often arrives with a literal backslash-r
        # because the caller pasted it from a README; normalise it to a real CRLF.
        if "\\r\\n" in url or "\\n" in url:
            url = url.replace("\\r\\n", "\r\n").replace("\\n", "\n")
        try:
            resp = requests.get(url, timeout=self._timeout)
            resp.raise_for_status()
            text = resp.text.strip()
        except Exception:
            return None
        if not text:
            return None
        # The extract API usually returns one IP:PORT per line; a JSON array or
        # ``{"data": [...]}`` is handled the same way.
        candidates = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith(("{", "["))
        ]
        if not candidates:
            return None
        proxy = to_proxy_url(candidates[0])
        if not proxy:
            return None
        with self._lock:
            self._current = proxy
        return proxy
