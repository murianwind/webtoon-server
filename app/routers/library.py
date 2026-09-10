"""
라이브러리 스캔 트리거 + 시리즈 폴더 제외/재포함 관리 라우트.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import access_control, catalog, db, overlap, scan, services

log = logging.getLogger("webtoon-server")
# 이 라우터의 모든 라우트(재스캔, 폴더 관리)는 관리자 전용이다 - 공유 프로필에게는
# 이런 기능 자체가 존재하지 않아야 하므로, 라우터 전체에 한 번에 적용한다(라우트마다
# 따로따로 확인 코드를 넣으면 하나라도 빠뜨릴 위험이 있음).
router = APIRouter(dependencies=[Depends(access_control.require_admin)])


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
    """
    제외해도 카탈로그에서 시리즈 데이터를 지우지는 않는다 - "제외"는 관리자 메인
    화면(list_series)에서만 숨기는 것이고, 이미 스캔된 실제 데이터(회차/커버/정보)는
    그대로 남아있어야 공유 프로필에게 계속 선택 후보로 줄 수 있다. 그래서 카탈로그의
    해당 엔트리에 excluded 플래그만 세워서, 다음 재스캔 전까지도 즉시 반영되게 한다.
    """
    excluded = db.get_excluded_series()
    excluded.add((body.platform, body.series))
    db.set_excluded_series(excluded)
    series_id = scan.make_id(body.platform, body.series)
    series = catalog.get_series(series_id)
    if series:
        series["excluded"] = True
    log.info(f"시리즈 폴더 스캔 제외(관리자 메인 화면에서만 숨김, 프로필 공유는 계속 가능): {body.platform}/{body.series}")
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
