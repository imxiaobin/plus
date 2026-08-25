"""Normalize pasted proxy strings into usable proxy URLs."""
from __future__ import annotations

import re
import socket
from urllib.parse import quote, urlsplit, urlunsplit

_SCHEME_RE = re.compile(r"^(?P<scheme>https?|socks5h?|socks4)://", re.IGNORECASE)
# host:port:user:pass  (IPv4 and hostnames; password may contain extra colons)
_COLON_AUTH_RE = re.compile(
    r"^(?P<host>[^:/]+):(?P<port>\d{1,5}):(?P<user>[^:@]+):(?P<password>.+)$"
)
_USERINFO_HOSTPORT_RE = re.compile(
    r"^(?P<user>[^:@]+):(?P<password>.+)@(?P<host>[^:/]+):(?P<port>\d{1,5})$"
)
_HOSTPORT_RE = re.compile(r"^(?P<host>[^:/]+):(?P<port>\d{1,5})$")


def redact_proxy_url(url: str) -> str:
    """Hide the password in a proxy URL for logs."""
    text = str(url or "").strip()
    if not text or "@" not in text:
        return text
    scheme, sep, rest = text.partition("://")
    if not sep:
        rest = text
        prefix = ""
    else:
        prefix = f"{scheme}://"
    userinfo, at, hostport = rest.partition("@")
    if not at or ":" not in userinfo:
        return text
    user, _, _password = userinfo.partition(":")
    return f"{prefix}{user}:***@{hostport}"


def is_extract_api_url(raw: str) -> bool:
    """True when the value should be fetched as an extract API, not used as a proxy."""
    text = str(raw or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return False
    parsed = urlsplit(text)
    path = parsed.path or ""
    return bool(parsed.query) or path not in {"", "/"}


def to_proxy_url(raw: str, *, default_scheme: str = "http") -> str | None:
    """Convert common paste formats into ``scheme://user:pass@host:port``.

    Supported:
      - ``host:port:user:pass``
      - ``user:pass@host:port``
      - ``host:port``
      - ``http://user:pass@host:port`` / socks URLs
    """
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text:
        return None

    scheme = default_scheme or "http"
    rest = text
    match = _SCHEME_RE.match(text)
    if match:
        scheme = match.group("scheme").lower()
        rest = text[match.end() :]

    parsed = _parse_endpoint(rest)
    if parsed is None:
        return None
    host, port, user, password = parsed
    return _build_proxy_url(scheme, host, port, user, password)


def _parse_endpoint(rest: str) -> tuple[str, str, str, str] | None:
    value = str(rest or "").strip()
    if not value:
        return None
    colon_auth = _COLON_AUTH_RE.fullmatch(value)
    if colon_auth:
        return (
            colon_auth.group("host"),
            colon_auth.group("port"),
            colon_auth.group("user"),
            colon_auth.group("password"),
        )
    userinfo = _USERINFO_HOSTPORT_RE.fullmatch(value)
    if userinfo:
        return (
            userinfo.group("host"),
            userinfo.group("port"),
            userinfo.group("user"),
            userinfo.group("password"),
        )
    hostport = _HOSTPORT_RE.fullmatch(value)
    if hostport:
        return hostport.group("host"), hostport.group("port"), "", ""
    return None


def _build_proxy_url(scheme: str, host: str, port: str, user: str, password: str) -> str:
    netloc = f"{host}:{port}"
    if user:
        userinfo = quote(user, safe="-._~")
        if password:
            userinfo = f"{userinfo}:{quote(password, safe='-._~')}"
        netloc = f"{userinfo}@{netloc}"
    return f"{scheme}://{netloc}"


def looks_like_socks5(host: str, port: int, *, timeout: float = 3.0) -> bool:
    """True if the port answers a SOCKS5 greeting (byte 0 == 0x05)."""
    if not host or not port:
        return False
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        sock.settimeout(timeout)
        # One method: no-auth. Auth-required servers still reply 0x05 0x02 / 0xFF.
        sock.sendall(b"\x05\x01\x00")
        reply = sock.recv(2)
        sock.close()
    except Exception:
        return False
    return bool(reply) and reply[0] == 5


def ensure_gateway_scheme(url: str, *, probe: bool = False) -> str:
    """Keep HTTP unless the paste already has a SOCKS scheme.

    BestGo-style residential gateways often answer a SOCKS greeting on the same
    port while the dashboard documents HTTP ``curl -x host:port -U user:pass``.
    Do not auto-upgrade HTTP to SOCKS5.
    """
    text = str(url or "").strip()
    if not text:
        return text
    parsed = urlsplit(text)
    scheme = (parsed.scheme or "http").lower()
    if scheme in {"socks5", "socks5h", "socks4"}:
        if scheme == "socks5":
            return urlunsplit(("socks5h", parsed.netloc, "", "", ""))
        return text
    if scheme != "http" or not probe or not parsed.hostname or not parsed.port:
        return text
    if looks_like_socks5(parsed.hostname, parsed.port):
        return urlunsplit(("socks5h", parsed.netloc, "", "", ""))
    return text


def explain_proxy_error(exc: BaseException) -> str:
    """Turn curl/proxy exceptions into an actionable Chinese message."""
    text = str(exc or "")
    lowered = text.lower()
    code = getattr(exc, "code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    if "0x02" in text or "ruleset" in lowered or "not allowed" in lowered:
        return (
            "SOCKS 认证过了但目标被拒绝。若后台测试命令是 curl -x（HTTP），"
            "不要走 SOCKS；若官方就是 SOCKS，再查子账户鉴权/白名单。"
        )
    if "whitelist" in lowered:
        return "代理商返回 IP 白名单校验失败。用户名密码代理一般不用登录后台，但若后台开了仅白名单 IP，需要把本机出口 IP 加上。"
    if (
        code in {52, 56}
        or "empty reply" in lowered
        or "connect aborted" in lowered
        or "recv failure" in lowered
    ):
        return (
            "已按后台官方方式调用 HTTP 动态代理（等同 curl -x host:port -U 账号:密码）。"
            "网关能连上，但转发请求被直接断开。"
            "请在本机终端跑后台那条测试命令；若同样 Empty reply / CONNECT aborted，"
            "是子账户没流量或后台开了 IP 白名单，不是程序没调用这条动态代理。"
        )
    if code == 97:
        return "SOCKS5 握手后无法建立目标连接。请检查白名单，或改用代理商提供的提取 API。"
    return f"代理预检失败: {exc}"


def preflight_proxy(url: str, *, timeout: int = 12) -> tuple[bool, str]:
    """Fetch a public IP through the proxy. Returns (ok, ip_or_error)."""
    from curl_cffi import requests as cffi_requests

    try:
        response = cffi_requests.get(
            "https://api.ipify.org",
            timeout=max(int(timeout), 5),
            proxies={"http": url, "https": url},
        )
        ip = str(response.text or "").strip()
        if response.status_code == 200 and ip:
            return True, ip
        return False, f"代理预检失败: HTTP {response.status_code}"
    except Exception as exc:
        return False, explain_proxy_error(exc)
