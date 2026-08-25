from __future__ import annotations

from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository
from providers.sms.herosms import HeroSMSClient


def test_sms_catalog_includes_herosms():
    repository = ProviderDefinitionsRepository()
    repository.ensure_seeded()

    definitions = repository.list_by_type("sms", enabled_only=True)

    assert {item.provider_key for item in definitions} == {"herosms"}
    herosms = next(item for item in definitions if item.provider_key == "herosms")
    field_keys = {field["key"] for field in herosms.get_fields()}
    assert "herosms_api_key" in field_keys
    assert "herosms_country" in field_keys


def test_config_options_expose_sms_providers(client):
    response = client.get("/api/config/options")
    assert response.status_code == 200
    data = response.json()
    assert {item["value"] for item in data["sms_providers"]} == {"herosms"}
    assert data["sms_settings"] == []


def test_herosms_get_balance(monkeypatch):
    client = HeroSMSClient(api_key="k")

    def fake_get(url, params=None, timeout=None):
        class Response:
            status_code = 200
            text = "ACCESS_BALANCE:12.50"

        assert params["action"] == "getBalance"
        return Response()

    monkeypatch.setattr(client.session, "get", fake_get)
    assert client.get_balance() == "12.50"


def test_sms_provider_test_returns_balance(client, monkeypatch):
    monkeypatch.setattr(
        "providers.sms.herosms.HeroSMSClient.get_balance",
        lambda self: "9.80",
    )
    response = client.post(
        "/api/provider-settings/test",
        json={
            "provider_type": "sms",
            "provider_key": "herosms",
            "auth": {"herosms_api_key": "ui-key"},
            "config": {},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "9.80" in body["message"]


def test_register_api_accepts_phone_identity_from_sms_settings(client, monkeypatch):
    captured = {}
    monkeypatch.delenv("OPAI_HEROSMS_API_KEY", raising=False)
    monkeypatch.delenv("OPAI_HEROSMS_API_KEY_FILE", raising=False)
    monkeypatch.setattr(
        "api.task_commands.command_service.create_register_task",
        lambda payload: captured.update(payload) or {"task_id": "phone-ui-task"},
    )

    saved = client.post(
        "/api/provider-settings",
        json={
            "provider_type": "sms",
            "provider_key": "herosms",
            "display_name": "HeroSMS",
            "auth_mode": "apikey",
            "enabled": True,
            "config": {"herosms_service": "oi", "herosms_country": "46"},
            "auth": {"herosms_api_key": "ui-herosms-key"},
        },
    )
    assert saved.status_code == 200

    response = client.post(
        "/api/tasks/register",
        json={"count": 1, "extra": {"identity_provider": "phone"}},
    )
    assert response.status_code == 200
    assert captured["extra"]["identity_provider"] == "phone"
    assert captured["extra"].get("herosms_api_key") in (None, "")
