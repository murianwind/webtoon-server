"""
공유 프로필의 기기 승인 대기 화면(device-approval.html)이 쓰는 전용 API 2개.
main.py의 기기 게이트가 이 두 경로만 승인 여부와 무관하게 항상 통과시켜준다 -
안 그러면 대기 화면 자신이 "지금 상태가 뭔지" 확인할 방법이 없어진다.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import access_control, discord_notify, profile_devices

router = APIRouter()


@router.get("/api/device/status")
def device_status(request: Request):
    profile = access_control.get_profile(request)
    if profile is None:
        raise HTTPException(404, "not applicable outside shared profiles")
    device_id = access_control.get_profile_device_id(request)

    if profile_devices.is_device_registered(profile["id"], device_id):
        return {"status": "approved"}
    if profile_devices.get_pending_request(profile["id"], device_id):
        return {"status": "pending"}
    return {"status": "needs_request"}


@router.post("/api/device/request")
async def request_device_approval(request: Request):
    profile = access_control.get_profile(request)
    if profile is None:
        raise HTTPException(404, "not applicable outside shared profiles")
    device_id = access_control.get_profile_device_id(request)
    if not device_id:
        raise HTTPException(400, "no device id")

    label = request.headers.get("user-agent", "알 수 없는 기기")[:120]
    profile_devices.create_pending_request(profile["id"], device_id, label)
    asyncio.create_task(
        discord_notify.send(f"📱 '{profile['name']}' 프로필에서 새 기기의 접속 승인을 요청했습니다.")
    )
    return {"ok": True}
