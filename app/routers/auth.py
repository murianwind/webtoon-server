"""
관리자 로그인 + 기억된 기기 관리 라우트. PROFILES_ENABLED가 꺼져있으면 main.py가 이
라우터 자체를 등록하지 않으므로, 이 파일의 존재만으로는 기존 동작에 아무 영향이 없다.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth

log = logging.getLogger("webtoon-server")
router = APIRouter()

DEVICE_COOKIE_NAME = "webtoon_device"
DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1년


class LoginIn(BaseModel):
    password: str


@router.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    client_key = request.client.host if request.client else "unknown"
    if not auth.verify_admin_password(body.password, client_key):
        raise HTTPException(401, "wrong password")

    label = request.headers.get("user-agent", "알 수 없는 기기")[:120]
    device_id = auth.create_remembered_device(label=label)
    response.set_cookie(
        DEVICE_COOKIE_NAME,
        device_id,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.get("/api/auth/devices")
def list_devices():
    return auth.list_remembered_devices()


@router.delete("/api/auth/devices/{device_id}")
def delete_device(device_id: str):
    """등록된 기기 목록에서 삭제 - 그 기기는 다음 접속 때 비밀번호를 다시 물어보게 된다.
    지금 이 요청을 보낸 기기 본인을 지우는 것도 막지 않는다(그 경우 이 요청 자체는
    성공하지만, 다음 요청부터 그 기기도 다시 로그인해야 함)."""
    auth.remove_remembered_device(device_id)
    return {"ok": True}
