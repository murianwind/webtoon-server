"""
백업 / 복원 (읽음 진행률 + 검색/정렬/필터 설정 + 라이브러리 등록 상태 + 기억된 기기 +
공유 프로필 전체(프로필 자체·허용 시리즈·둘러보기 필터·진행률·설정·접속 시간대·요청
내역·등록 기기)) 라우트.
"""

import json
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from .. import (
    access_control,
    access_requests,
    auth,
    db,
    profile_devices,
    profile_progress,
    profile_settings,
    profile_time_restrictions,
    profiles,
    services,
)

log = logging.getLogger("webtoon-server")
# 백업/복원은 관리자 전용 데이터(공용 진행률·설정)를 다루므로, 공유 프로필에게는
# 이 기능 자체가 존재하지 않아야 한다 - library.py와 같은 이유로 라우터 전체에 적용.
router = APIRouter(dependencies=[Depends(access_control.require_admin)])


@router.get("/api/backup")
def export_backup():
    data = db.export_backup_data()
    profiles_data = profiles.export_all()
    profile_progress_data = profile_progress.export_all()
    profile_devices_data = profile_devices.export_all()
    payload = {
        "version": services.BACKUP_VERSION,
        "exported_at": datetime.utcnow().isoformat(),
        "progress": data["progress"],
        "app_settings": data["app_settings"],
        "read_chapters": data["read_chapters"],
        # 관리자 비밀번호(해시) 자체는 재시작마다 새로 만드는 설계라 절대 포함하지
        # 않지만, "기억된 기기" 목록은 포함한다 - 같은 배포를 DB만 복구하는 상황이면
        # 관리자 본인이 다시 로그인하지 않아도 되게 해준다.
        "remembered_devices": auth.export_remembered_devices(),
        # PROFILES_ENABLED를 지금 껐다 켰다 하는 것과 무관하게, 프로필 관련 데이터는
        # 항상 백업에 포함한다 - 나중에 다시 켰을 때 프로필 링크(토큰)를 포함한 모든
        # 설정이 그대로 복원되어야 하기 때문이다.
        "profiles": profiles_data["profiles"],
        "profile_browse_filters": profiles_data["profile_browse_filters"],
        "profile_allowed_series": profiles_data["profile_allowed_series"],
        "profile_progress": profile_progress_data["profile_progress"],
        "profile_read_chapters": profile_progress_data["profile_read_chapters"],
        "profile_settings": profile_settings.export_all(),
        "profile_time_windows": profile_time_restrictions.export_all(),
        "access_requests": access_requests.export_all(),
        # device_id는 브라우저 쿠키 값이라 "같은 배포를 DB만 복구"할 때만 의미가
        # 있다(다른 서버로 옮기면 그냥 남는 기록이 됨) - 그래도 그 흔한 경우엔
        # 이미 승인받은 가족들이 다시 승인 절차를 안 거쳐도 되게 해준다.
        "profile_devices": profile_devices_data["profile_devices"],
        "profile_device_requests": profile_devices_data["profile_device_requests"],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    filename = f"webtoon-server-backup-{date.today().isoformat()}.json"
    log.info(
        f"백업 생성 - progress {len(data['progress'])}건, settings {len(data['app_settings'])}건, "
        f"읽은 회차 {len(data['read_chapters'])}건, 프로필 {len(payload['profiles'])}개, "
        f"프로필 진행률 {len(payload['profile_progress'])}건"
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class RestorePayload(BaseModel):
    version: int | None = None
    progress: list = []
    app_settings: list = []
    read_chapters: list = []
    remembered_devices: list = []
    profiles: list = []
    profile_browse_filters: list = []
    profile_allowed_series: list = []
    profile_progress: list = []
    profile_read_chapters: list = []
    profile_settings: list = []
    profile_time_windows: list = []
    access_requests: list = []
    profile_devices: list = []
    profile_device_requests: list = []


@router.post("/api/restore")
async def import_backup(body: RestorePayload):
    progress_count, settings_count, read_count = db.import_backup_data(
        body.progress, body.app_settings, body.read_chapters
    )
    remembered_devices_count = auth.import_remembered_devices(body.remembered_devices)
    profiles_count = profiles.import_all(
        body.profiles, body.profile_browse_filters, body.profile_allowed_series
    )
    profile_progress_count = profile_progress.import_all(body.profile_progress, body.profile_read_chapters)
    profile_settings_count = profile_settings.import_all(body.profile_settings)
    profile_time_windows_count = profile_time_restrictions.import_all(body.profile_time_windows)
    access_requests_count = access_requests.import_all(body.access_requests)
    profile_devices_count = profile_devices.import_all(body.profile_devices, body.profile_device_requests)

    # 라이브러리 등록(제외 목록) 상태도 복원됐을 수 있으니 다시 스캔해서 반영
    await services.scan_all_platforms_incrementally()

    log.info(
        f"백업 복원 완료 - progress {progress_count}건, settings {settings_count}건, "
        f"읽은 회차 {read_count}건, 프로필 {profiles_count}개, 프로필 진행률 {profile_progress_count}건, "
        f"프로필 설정 {profile_settings_count}건, 접속 시간대 {profile_time_windows_count}건, "
        f"요청 내역 {access_requests_count}건, 등록 기기(관리자) {remembered_devices_count}건, "
        f"등록 기기(프로필) {profile_devices_count}건"
    )
    return {
        "ok": True,
        "progress_count": progress_count,
        "settings_count": settings_count,
        "read_chapters_count": read_count,
        "remembered_devices_count": remembered_devices_count,
        "profiles_count": profiles_count,
        "profile_progress_count": profile_progress_count,
        "profile_settings_count": profile_settings_count,
        "profile_time_windows_count": profile_time_windows_count,
        "access_requests_count": access_requests_count,
        "profile_devices_count": profile_devices_count,
    }
