"""
앱 설정(검색/정렬/필터 등 기기 간 동일하게 유지할 값) 저장/조회 라우트.

관리자는 db.py의 app_settings(공용)에 저장하고, 공유 프로필은 profile_settings.py의
프로필 전용 테이블에 저장한다 - 그래야 한 프로필이 필터를 바꿔도 관리자나 다른
프로필의 화면에는 전혀 영향이 없다. 예전에는 이 구분이 없어서 모든 프로필과 관리자가
같은 설정을 공유해버리는 문제가 있었다.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import access_control, db, profile_settings

router = APIRouter()


@router.get("/api/settings/{key}")
def read_setting(key: str, request: Request):
    profile = access_control.get_profile(request)
    if profile is None:
        return {"key": key, "value": db.get_setting(key)}
    return {"key": key, "value": profile_settings.get_setting(profile["id"], key)}


class SettingIn(BaseModel):
    value: str


@router.put("/api/settings/{key}")
def write_setting(key: str, body: SettingIn, request: Request):
    profile = access_control.get_profile(request)
    if profile is None:
        db.set_setting(key, body.value)
    else:
        profile_settings.set_setting(profile["id"], key, body.value)
    return {"ok": True}
