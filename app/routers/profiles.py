"""
프로필 생성/수정/삭제 + 허용 시리즈/둘러보기 필터 설정 + 요청 승인/거부 (전부 관리자 전용).
PROFILES_ENABLED가 꺼져있으면 main.py가 이 라우터 자체를 등록하지 않는다.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import access_requests, profile_progress, profiles

log = logging.getLogger("webtoon-server")
router = APIRouter()


class CreateProfileIn(BaseModel):
    name: str


@router.post("/api/admin/profiles")
def create_profile(body: CreateProfileIn):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    return profiles.create_profile(body.name.strip())


@router.get("/api/admin/profiles")
def list_profiles():
    result = []
    for profile in profiles.list_profiles():
        pending = [
            r for r in access_requests.list_requests_for_profile(profile["id"])
            if r["status"] == access_requests.STATUS_PENDING
        ]
        result.append(
            {
                **profile,
                "allowed_count": len(profiles.get_allowed_series_ids(profile["id"])),
                "pending_request_count": len(pending),
            }
        )
    return result


class RenameProfileIn(BaseModel):
    name: str


@router.put("/api/admin/profiles/{profile_id}")
def rename_profile(profile_id: str, body: RenameProfileIn):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    profiles.rename_profile(profile_id, body.name.strip())
    return {"ok": True}


@router.delete("/api/admin/profiles/{profile_id}")
def delete_profile(profile_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    profiles.delete_profile(profile_id)
    profile_progress.delete_all_data_for_profile(profile_id)
    return {"ok": True}


@router.post("/api/admin/profiles/{profile_id}/reissue-token")
def reissue_token(profile_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    new_token = profiles.reissue_profile_token(profile_id)
    return {"token": new_token}


class BrowseFiltersIn(BaseModel):
    # [{"platform": "네이버", "age_rating": "전체 이용가"}, ...]
    filters: list[dict]


@router.put("/api/admin/profiles/{profile_id}/browse-filters")
def set_browse_filters(profile_id: str, body: BrowseFiltersIn):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    pairs = [(item["platform"], item["age_rating"]) for item in body.filters]
    profiles.set_browse_filters(profile_id, pairs)
    return {"ok": True}


@router.get("/api/admin/profiles/{profile_id}/browse-filters")
def get_browse_filters(profile_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    pairs = profiles.get_browse_filters(profile_id)
    return [{"platform": p, "age_rating": a} for p, a in pairs]


class AllowedSeriesIn(BaseModel):
    series_ids: list[str]


@router.put("/api/admin/profiles/{profile_id}/allowed-series")
def set_allowed_series(profile_id: str, body: AllowedSeriesIn):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    profiles.set_allowed_series(profile_id, body.series_ids)
    # 승인된 시리즈에 대한 대기중 요청은 더 이상 의미가 없으니 정리한다.
    for series_id in body.series_ids:
        access_requests.clear_request_on_approval(profile_id, series_id)
    return {"ok": True}


@router.get("/api/admin/profiles/{profile_id}/allowed-series")
def get_allowed_series(profile_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    return list(profiles.get_allowed_series_ids(profile_id))


@router.get("/api/admin/profiles/{profile_id}/requests")
def list_requests(profile_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    return access_requests.list_requests_for_profile(profile_id)


@router.post("/api/admin/profiles/{profile_id}/requests/{series_id}/reject")
def reject_request(profile_id: str, series_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    access_requests.reject_request(profile_id, series_id)
    return {"ok": True}


@router.post("/api/admin/profiles/{profile_id}/requests/{series_id}/undo-reject")
def undo_reject_request(profile_id: str, series_id: str):
    if profiles.get_profile(profile_id) is None:
        raise HTTPException(404, "profile not found")
    access_requests.clear_rejection(profile_id, series_id)
    return {"ok": True}
