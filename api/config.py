from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from application.config import ConfigService
from application.sub2api_oauth import Sub2ApiError, Sub2ApiNotConfiguredError

router = APIRouter(prefix="/config", tags=["config"])
service = ConfigService()


class ConfigUpdateRequest(BaseModel):
    data: dict[str, str] = Field(default_factory=dict)


class Sub2ApiTestRequest(BaseModel):
    sub2api_url: str = ""
    sub2api_api_key: str = ""
    group_id: int = 0


@router.get("")
def get_config():
    return service.get_config()


@router.get("/options")
def get_config_options():
    return service.get_options()


@router.put("")
def update_config(body: ConfigUpdateRequest):
    return service.update_config(body.data)


@router.post("/sub2api/test")
def test_sub2api_connection(body: Sub2ApiTestRequest | None = None):
    payload = body.model_dump() if body else {}
    try:
        return service.test_sub2api_connection(payload)
    except (Sub2ApiNotConfiguredError, Sub2ApiError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sub2api/groups")
def list_sub2api_groups(body: Sub2ApiTestRequest | None = None):
    payload = body.model_dump() if body else {}
    try:
        return service.list_sub2api_groups(payload)
    except (Sub2ApiNotConfiguredError, Sub2ApiError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sub2api/models")
def list_sub2api_models(body: Sub2ApiTestRequest | None = None):
    payload = body.model_dump() if body else {}
    try:
        return service.list_sub2api_models(payload)
    except (Sub2ApiNotConfiguredError, Sub2ApiError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


class Sub2ApiSolTerraRequest(BaseModel):
    enable: bool


@router.get("/sub2api/monitor")
def monitor_sub2api_accounts():
    try:
        return service.monitor_sub2api_accounts()
    except (Sub2ApiNotConfiguredError, Sub2ApiError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sub2api/sol-terra-mapping")
def preview_sol_terra_mapping():
    try:
        return service.preview_sol_terra_mapping()
    except (Sub2ApiNotConfiguredError, Sub2ApiError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sub2api/sol-terra-mapping")
def apply_sol_terra_mapping(body: Sub2ApiSolTerraRequest):
    try:
        return service.apply_sol_terra_mapping(enable=body.enable)
    except (Sub2ApiNotConfiguredError, Sub2ApiError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
