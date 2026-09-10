"""
공유 프로필 전용 "둘러보기"(연령 필터 통과했지만 아직 허용 안 된 시리즈) + "보여주세요"
요청 제출 라우트. 관리자에게는 의미가 없는 개념이라(관리자는 항상 전체를 다 보므로),
profile이 없는 요청(관리자)은 전부 404로 처리한다.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import access_control, access_requests, catalog, discord_notify, profiles

router = APIRouter()


def _require_profile(request: Request) -> dict:
    profile = access_control.get_profile(request)
    if profile is None:
        raise HTTPException(404, "browse is only available for shared profiles")
    return profile


@router.get("/api/browse")
def browse(request: Request):
    """
    이 프로필의 연령 필터(platform, age_rating)를 통과하면서 아직 허용 목록에는 없는
    시리즈들을 반환한다. 표지/제목/요청 상태만 포함하고, 회차/진행률 등 실제 콘텐츠
    관련 정보는 전혀 포함하지 않는다(둘러보기는 미리보기일 뿐, 실제로 읽는 건
    허용된 뒤에만 가능해야 하므로).
    """
    profile = _require_profile(request)
    allowed_ids = profiles.get_allowed_series_ids(profile["id"])
    browse_filters = profiles.get_browse_filters(profile["id"])

    result = []
    for series in catalog.get_series_map().values():
        if series["id"] in allowed_ids:
            continue
        info = series.get("info") or {}
        age_rating = info.get("age_rating") or profiles.NO_AGE_RATING
        if (series["platform"], age_rating) not in browse_filters:
            continue

        status = access_requests.get_request_status(profile["id"], series["id"])
        result.append(
            {
                "id": series["id"],
                "platform": series["platform"],
                "title": series["title"],
                "cover_url": f"/api/series/{series['id']}/cover",
                "request_status": status,  # None | "pending" | "rejected"
            }
        )
    result.sort(key=lambda item: (item["platform"], item["title"]))
    return result


@router.post("/api/browse/{series_id}/request")
async def request_access(series_id: str, request: Request):
    """
    "보여주세요" 버튼. 이미 허용된 시리즈거나, 이미 거부된 요청이면 막는다(거부된 건
    관리자가 명시적으로 되돌려야만 다시 요청 가능 - access_requests.create_request가
    이미 같은 요청에 대해 중복 생성은 막아주지만, 여기서는 사용자에게 더 명확한
    에러를 주기 위해 먼저 확인한다).
    """
    profile = _require_profile(request)

    if series_id in profiles.get_allowed_series_ids(profile["id"]):
        raise HTTPException(400, "already allowed")

    existing_status = access_requests.get_request_status(profile["id"], series_id)
    if existing_status == access_requests.STATUS_REJECTED:
        raise HTTPException(400, "this request was rejected")

    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")

    access_requests.create_request(profile["id"], series_id)
    asyncio.create_task(
        discord_notify.send(f"📬 '{profile['name']}' 프로필이 '{series['title']}' 공개를 요청했습니다.")
    )
    return {"ok": True}
