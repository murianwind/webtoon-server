"""
라이브러리 스캔 트리거 + 시리즈 폴더 제외/재포함 관리 라우트.
"""

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from .. import catalog, db, overlap, scan, services

log = logging.getLogger("webtoon-server")
router = APIRouter()


@router.post("/api/rescan")
async def rescan():
    old_ids = set(catalog.get_series_map().keys())
    series_map, chapters_map = await services.scan_all_platforms_incrementally()
    added = len(set(series_map.keys()) - old_ids)
    removed = len(old_ids - set(series_map.keys()))
    services.log_scan_result("수동 재스캔 완료", series_map, chapters_map, added, removed)
    asyncio.create_task(overlap.precompute_overlaps())
    asyncio.create_task(services.precompute_covers())
    return {"series_count": len(series_map)}


@router.get("/api/scan-status")
def scan_status():
    """설정 패널 등에 표시할 마지막 스캔 시각 + 알고 있는 전체 플랫폼 목록(아직
    시리즈가 하나도 안 뜬 플랫폼이라도, 폴더 자체는 있다는 걸 미리 알려주기 위함)."""
    return {
        "last_scan_at": catalog.get_last_scan_display(),
        "platforms": catalog.get_known_platforms(),
    }


# ---------------------------------------------------------------------------
# 시리즈 폴더 스캔 제외/포함 (플랫폼 폴더 안에 웹툰 아닌 폴더가 섞여 있을 때
# 특정 폴더만 스캔 대상에서 뺐다가 나중에 다시 넣을 수 있게 함)
# ---------------------------------------------------------------------------


@router.get("/api/series-folders")
def list_series_folders():
    """스캔 중/제외된 폴더 목록. 디스크를 다시 훑지 않고, 마지막 스캔 때 이미 기록해둔
    결과(catalog.get_all_folder_refs)를 그대로 재사용한다 - 이 목록을 열 때마다
    네트워크 드라이브까지 다시 훑으면 그만큼 느려지기 때문."""
    excluded = db.get_excluded_series()
    all_folders = [{"platform": p, "series": r} for p, r in catalog.get_all_folder_refs()]
    return {
        "included": [f for f in all_folders if (f["platform"], f["series"]) not in excluded],
        "excluded": [f for f in all_folders if (f["platform"], f["series"]) in excluded],
    }


class SeriesFolderRef(BaseModel):
    platform: str
    series: str


@router.post("/api/series-folders/exclude")
def exclude_series_folder(body: SeriesFolderRef):
    """제외는 이미 스캔되어 카탈로그에 있는 시리즈를 메모리에서 바로 빼는 것뿐이라,
    디스크를 다시 훑을 필요가 없다 - 그래서 즉시 반영된다."""
    excluded = db.get_excluded_series()
    excluded.add((body.platform, body.series))
    db.set_excluded_series(excluded)
    series_id = scan.make_id(body.platform, body.series)
    catalog.remove_series(series_id)
    log.info(f"시리즈 폴더 스캔 제외: {body.platform}/{body.series} (파일은 삭제하지 않음)")
    return {"ok": True}


@router.post("/api/series-folders/include")
async def include_series_folder(body: SeriesFolderRef):
    """재포함도 전체 재스캔이 아니라 이 폴더 하나만 다시 읽어서 카탈로그에 더한다."""
    excluded = db.get_excluded_series()
    excluded.discard((body.platform, body.series))
    db.set_excluded_series(excluded)

    result = await services.run_platform_io(body.platform, scan.scan_single_series, body.platform, body.series)
    if result:
        series_entry, chapters_map = result
        catalog.add_series(series_entry, chapters_map)
        asyncio.create_task(overlap.precompute_overlaps())
        asyncio.create_task(services.precompute_one_cover_with_timeout(series_entry))
    log.info(f"시리즈 폴더 다시 포함: {body.platform}/{body.series}")
    return {"ok": True}
