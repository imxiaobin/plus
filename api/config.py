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

router = APIRouter(prefix="/config", tags=["config"])
service = ConfigService()


class ConfigUpdateRequest(BaseModel):
    data: dict[str, str] = Field(default_factory=dict)


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
