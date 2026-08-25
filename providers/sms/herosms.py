"""HeroSMS client (SMS-Activate compatible handler_api.php)."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

DEFAULT_API_URL = "https://hero-sms.com/stubs/handler_api.php"
DEFAULT_SERVICE = "oi"
DEFAULT_COUNTRY = 46
STATUS_COMPLETE = 6
STATUS_CANCEL = 8
STATUS_READY = 1


class HeroSMSError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeroSMSActivation:
    activation_id: str
    phone: str


def merge_herosms_runtime_extra(extra: dict | None = None) -> dict:
    """Merge saved 手机平台配置 with per-task extra, then env fallbacks in from_config."""
    from infrastructure.provider_settings_repository import ProviderSettingsRepository

    return ProviderSettingsRepository().resolve_runtime_settings("sms", "herosms", dict(extra or {}))


def _read_api_key(extra: dict | None = None) -> str:
    extra = extra or {}
    key = str(extra.get("herosms_api_key") or os.getenv("OPAI_HEROSMS_API_KEY") or "").strip()
    if key:
        return key
    path = str(
        extra.get("herosms_api_key_file") or os.getenv("OPAI_HEROSMS_API_KEY_FILE") or ""
    ).strip()
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def herosms_is_configured(extra: dict | None = None) -> bool:
    return bool(_read_api_key(merge_herosms_runtime_extra(extra)))


def normalize_e164(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise HeroSMSError("HeroSMS 未返回手机号")
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
        if not digits:
            raise HeroSMSError(f"HeroSMS 手机号无效: {text[:8]}")
        return f"+{digits}"
    digits = re.sub(r"\D", "", text)
    if not digits:
        raise HeroSMSError(f"HeroSMS 手机号无效: {text[:8]}")
    return f"+{digits}"


class HeroSMSClient:
    def __init__(
        self,
        api_key: str,
        *,
        api_url: str = DEFAULT_API_URL,
        service: str = DEFAULT_SERVICE,
        country: int = DEFAULT_COUNTRY,
        max_price: float | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise HeroSMSError("未配置 HeroSMS API key（OPAI_HEROSMS_API_KEY）")
        self.api_url = str(api_url or DEFAULT_API_URL).strip() or DEFAULT_API_URL
        self.service = str(service or DEFAULT_SERVICE).strip() or DEFAULT_SERVICE
        self.country = int(country)
        self.max_price = max_price
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.trust_env = False

    @classmethod
    def from_config(cls, extra: dict | None = None) -> "HeroSMSClient":
        extra = merge_herosms_runtime_extra(extra)
        max_price_raw = extra.get("herosms_max_price", os.getenv("OPAI_HEROSMS_MAX_PRICE"))
        max_price = None
        try:
            if str(max_price_raw or "").strip():
                max_price = float(max_price_raw)
        except (TypeError, ValueError):
            max_price = None
        country_raw = extra.get("herosms_country", os.getenv("OPAI_HEROSMS_COUNTRY", str(DEFAULT_COUNTRY)))
        try:
            country = int(country_raw)
        except (TypeError, ValueError):
            country = DEFAULT_COUNTRY
        return cls(
            api_key=_read_api_key(extra),
            api_url=str(extra.get("herosms_api_url") or os.getenv("OPAI_HEROSMS_API_URL") or DEFAULT_API_URL),
            service=str(extra.get("herosms_service") or os.getenv("OPAI_HEROSMS_SERVICE") or DEFAULT_SERVICE),
            country=country,
            max_price=max_price,
        )

    def _request(self, action: str, **params) -> str:
        query = {"api_key": self.api_key, "action": action, **params}
        query = {key: value for key, value in query.items() if value is not None and value != ""}
        try:
            response = self.session.get(self.api_url, params=query, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise HeroSMSError(f"HeroSMS 请求失败: {exc}") from exc
        text = str(response.text or "").strip()
        if response.status_code >= 400:
            raise HeroSMSError(f"HeroSMS HTTP {response.status_code}: {text[:180]}")
        self._raise_if_error(text)
        return text

    def get_balance(self) -> str:
        text = self._request("getBalance")
        if text.startswith("ACCESS_BALANCE:"):
            return text.split(":", 1)[1].strip()
        raise HeroSMSError(f"HeroSMS 余额响应无法解析: {text[:180]}")

    @staticmethod
    def _raise_if_error(text: str) -> None:
        if text.startswith("ACCESS_") or text.startswith("STATUS_"):
            return
        known = {
            "NO_NUMBERS": "HeroSMS 当前没有可用号码",
            "BAD_KEY": "HeroSMS API key 无效",
            "NO_KEY": "HeroSMS API key 缺失",
            "NO_BALANCE": "HeroSMS 余额不足",
            "BAD_SERVICE": "HeroSMS 服务代码无效",
            "NO_ACTIVATION": "HeroSMS 激活不存在",
            "EARLY_CANCEL_DENIED": "HeroSMS 取消过早，稍后才能退款",
        }
        code = text.split(":", 1)[0].strip()
        if code in known:
            raise HeroSMSError(f"{known[code]} ({text})")
        if code in {"BAD_ACTION", "ERROR_SQL", "BANNED", "CHANNELS_LIMIT", "WRONG_MAX_PRICE"}:
            raise HeroSMSError(f"HeroSMS 错误: {text}")

    def get_number(self) -> HeroSMSActivation:
        params: dict[str, object] = {"service": self.service, "country": self.country}
        if self.max_price is not None:
            params["maxPrice"] = self.max_price
        text = self._request("getNumber", **params)
        match = re.match(r"ACCESS_NUMBER:(\d+):(.+)$", text)
        if not match:
            raise HeroSMSError(f"HeroSMS 取号响应无法解析: {text[:180]}")
        return HeroSMSActivation(activation_id=match.group(1), phone=normalize_e164(match.group(2)))

    def mark_ready(self, activation_id: str) -> None:
        try:
            self._request("setStatus", id=str(activation_id), status=STATUS_READY)
        except HeroSMSError:
            return

    def complete(self, activation_id: str) -> None:
        self._request("setStatus", id=str(activation_id), status=STATUS_COMPLETE)

    def cancel(self, activation_id: str) -> None:
        try:
            self._request("setStatus", id=str(activation_id), status=STATUS_CANCEL)
        except HeroSMSError:
            return

    def wait_for_code(
        self,
        activation_id: str,
        *,
        timeout_seconds: float = 180,
        poll_interval_seconds: float = 3.0,
        cancel_check: Callable[[], bool] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> str:
        deadline = time.monotonic() + max(float(timeout_seconds), 1.0)
        last = ""
        while time.monotonic() < deadline:
            if cancel_check and cancel_check():
                raise HeroSMSError("任务已取消，停止等待短信")
            text = self._request("getStatus", id=str(activation_id))
            last = text
            if text.startswith("STATUS_OK:"):
                code = text.split(":", 1)[1].strip()
                if re.fullmatch(r"\d{4,8}", code):
                    if log:
                        log("已从 HeroSMS 收到短信验证码")
                    return code
                raise HeroSMSError(f"HeroSMS 验证码格式异常: {code[:8]}")
            time.sleep(max(float(poll_interval_seconds), 1.0))
        raise HeroSMSError(f"等待 HeroSMS 短信超时（{int(timeout_seconds)}s，最后状态 {last or '空'}）")
