"""
백업 / 복원 (읽음 진행률 + 검색/정렬/필터 설정 + 라이브러리 등록 상태 전부) 라우트.
"""

import json
import logging
from datetime import date, datetime

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db, services

log = logging.getLogger("webtoon-server")
router = APIRouter()


@router.get("/api/backup")
def export_backup():
    data = db.export_backup_data()
    payload = {
        "version": services.BACKUP_VERSION,
        "exported_at": datetime.utcnow().isoformat(),
        "progress": data["progress"],
        "app_settings": data["app_settings"],
        "read_chapters": data["read_chapters"],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    filename = f"webtoon-server-backup-{date.today().isoformat()}.json"
    log.info(
        f"백업 생성 - progress {len(data['progress'])}건, settings {len(data['app_settings'])}건, "
        f"읽은 회차 {len(data['read_chapters'])}건"
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


@router.post("/api/restore")
async def import_backup(body: RestorePayload):
    progress_count, settings_count, read_count = db.import_backup_data(
        body.progress, body.app_settings, body.read_chapters
    )

    # 라이브러리 등록(제외 목록) 상태도 복원됐을 수 있으니 다시 스캔해서 반영
    await services.scan_all_platforms_incrementally()

    log.info(
        f"백업 복원 완료 - progress {progress_count}건, settings {settings_count}건, "
        f"읽은 회차 {read_count}건"
    )
    return {
        "ok": True,
        "progress_count": progress_count,
        "settings_count": settings_count,
        "read_chapters_count": read_count,
    }
