from __future__ import annotations

from application.provider_definitions import ProviderDefinitionsService
from application.platforms import PlatformsService, collect_platform_choice_options
from application.provider_settings import ProviderSettingsService
from infrastructure.config_repository import ConfigRepository

SUB2API_PUBLIC_DEFAULTS = {
    "sub2api_url": "",
    "sub2api_concurrency": "3",
    "sub2api_priority": "50",
    "sub2api_group_ids": "",
    "sub2api_models": "",
    "sub2api_model_mapping": "",
}


class ConfigService:
    def __init__(self, repository: ConfigRepository | None = None):
        self.repository = repository or ConfigRepository()
        self.provider_definitions = ProviderDefinitionsService()
        self.provider_settings = ProviderSettingsService()
        self.platforms = PlatformsService()

    def get_config(self) -> dict[str, str]:
        data = dict(self.repository.get_flat())
        for key, default in SUB2API_PUBLIC_DEFAULTS.items():
            data.setdefault(key, default)
        configured = bool(str(data.get("sub2api_api_key") or "").strip())
        data["sub2api_api_key_configured"] = "1" if configured else "0"
        data["sub2api_api_key"] = ""
        return data

    def update_config(self, data: dict[str, str]) -> dict:
        payload = {str(key): str(value) for key, value in dict(data or {}).items()}
        payload.pop("sub2api_api_key_configured", None)
        incoming_key = payload.get("sub2api_api_key")
        if incoming_key is not None and not str(incoming_key).strip():
            payload.pop("sub2api_api_key", None)
        updated = self.repository.update_flat(payload)
        return {"ok": True, "updated": updated}

    def test_sub2api_connection(self, data: dict[str, str] | None = None) -> dict:
        from application.sub2api_oauth import test_sub2api_connection

        return test_sub2api_connection(data)

    def list_sub2api_groups(self, data: dict[str, str] | None = None) -> dict:
        from application.sub2api_oauth import list_sub2api_groups

        return list_sub2api_groups(data)

    def list_sub2api_models(self, data: dict[str, str] | None = None) -> dict:
        from application.sub2api_oauth import list_sub2api_models

        return list_sub2api_models(data)

    def monitor_sub2api_accounts(self) -> dict:
        from application.sub2api_oauth import monitor_local_sub2api_accounts

        return monitor_local_sub2api_accounts()

    def preview_sol_terra_mapping(self) -> dict:
        from application.sub2api_oauth import preview_sol_terra_mapping

        return preview_sol_terra_mapping()

    def apply_sol_terra_mapping(self, *, enable: bool) -> dict:
        from application.sub2api_oauth import apply_sol_terra_mapping

        return apply_sol_terra_mapping(enable=enable)

    def get_options(self) -> dict:
        platform_options = collect_platform_choice_options(
            [item for item in self.platforms.list_platforms() if item.get("name") == "chatgpt"]
        )
        return {
            "mailbox_providers": self.provider_definitions.list_definitions("mailbox", enabled_only=True),
            "captcha_providers": self.provider_definitions.list_definitions("captcha", enabled_only=True),
            "sms_providers": self.provider_definitions.list_definitions("sms", enabled_only=True),
            "mailbox_drivers": self.provider_definitions.list_driver_templates("mailbox"),
            "captcha_drivers": self.provider_definitions.list_driver_templates("captcha"),
            "sms_drivers": self.provider_definitions.list_driver_templates("sms"),
            "captcha_policy": self.provider_settings.get_captcha_policy(),
            "mailbox_settings": self.provider_settings.list_settings("mailbox"),
            "captcha_settings": self.provider_settings.list_settings("captcha"),
            "sms_settings": self.provider_settings.list_settings("sms"),
            **platform_options,
        }
