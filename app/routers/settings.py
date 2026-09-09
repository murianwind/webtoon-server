"""
앱 설정(검색/정렬/필터 등 기기 간 동일하게 유지할 값) 저장/조회 라우트.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from .. import db

router = APIRouter()


@router.get("/api/settings/{key}")
def read_setting(key: str):
    return {"key": key, "value": db.get_setting(key)}


class SettingIn(BaseModel):
    value: str


@router.put("/api/settings/{key}")
def write_setting(key: str, body: SettingIn):
    db.set_setting(key, body.value)
    return {"ok": True}
