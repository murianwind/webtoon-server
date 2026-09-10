"""
"지금 이 요청이 관리자인지 특정 프로필인지"와 "그 경우 진행률을 어디서 읽고 쓸지"를
한곳에 모은 모듈. series.py/chapters.py 라우트들은 db.xxx()를 직접 부르는 대신 이
모듈의 함수를 불러서, admin/profile 분기 로직이 라우트 코드 안에 흩어지지 않게 한다.

profile이 None(관리자)일 때는 전부 db.py의 기존 함수를 그대로 호출한다 - 즉 관리자
경로는 이 모듈을 거치더라도 동작이 한 치도 안 바뀐다(그냥 한 단계 위임만 추가됨).
profile이 있을 때는 profile_progress.py(완전히 별도 테이블)로 위임한다.
"""

from fastapi import HTTPException, Request

from . import db, profile_progress, profiles


def get_profile(request: Request) -> dict | None:
    return getattr(request.state, "profile", None)


def ensure_series_accessible(profile: dict | None, series_id: str) -> None:
    """profile이 있는데(공유 링크) 그 시리즈가 허용 목록에 없으면 403.
    profile이 None(관리자)이면 항상 통과 - 기존 동작 그대로.

    회차 목록/페이지/진행률처럼 실제 콘텐츠에 접근하는 라우트에서만 쓴다 - 완전히
    허용된 시리즈만 통과시키는 엄격한 검사다."""
    if profile is None:
        return
    if series_id not in profiles.get_allowed_series_ids(profile["id"]):
        raise HTTPException(403, "not allowed for this profile")


def ensure_series_previewable(profile: dict | None, series: dict) -> None:
    """표지/info.xml 미리보기처럼, "완전히 허용되지는 않았지만 미리 볼 수는 있어야
    하는" 라우트에서 쓰는 완화된 검사. 완전히 허용된 시리즈 OR 그 프로필의 둘러보기
    연령 필터에 걸리는 시리즈면 통과한다 - 둘러보기 화면에서 아직 요청 전인 시리즈의
    표지/정보를 보여주려면 이 완화된 검사가 필요하다(엄격한 검사를 쓰면 둘러보기
    항목 자체가 다 막혀버림)."""
    if profile is None:
        return
    if series["id"] in profiles.get_allowed_series_ids(profile["id"]):
        return
    if profiles.series_matches_browse_filters(series, profile["id"]):
        return
    raise HTTPException(403, "not allowed for this profile")


def get_progress(profile: dict | None, series_id: str) -> dict | None:
    if profile is None:
        return db.get_progress(series_id)
    return profile_progress.get_progress(profile["id"], series_id)


def set_progress(profile: dict | None, series_id: str, chapter_id: str, chapter_index: int, page_index: int) -> None:
    if profile is None:
        db.set_progress(series_id, chapter_id, chapter_index, page_index)
    else:
        profile_progress.set_progress(profile["id"], series_id, chapter_id, chapter_index, page_index)


def delete_progress(profile: dict | None, series_id: str) -> None:
    if profile is None:
        db.delete_progress(series_id)
    else:
        profile_progress.delete_progress(profile["id"], series_id)


def get_read_chapter_ids(profile: dict | None, series_id: str) -> set[str]:
    if profile is None:
        return db.get_read_chapter_ids(series_id)
    return profile_progress.get_read_chapter_ids(profile["id"], series_id)


def mark_chapters_read(profile: dict | None, series_id: str, chapter_ids: list[str]) -> None:
    if profile is None:
        db.mark_chapters_read(series_id, chapter_ids)
    else:
        profile_progress.mark_chapters_read(profile["id"], series_id, chapter_ids)


def mark_chapters_unread(profile: dict | None, series_id: str, chapter_ids: list[str]) -> None:
    if profile is None:
        db.mark_chapters_unread(series_id, chapter_ids)
    else:
        profile_progress.mark_chapters_unread(profile["id"], series_id, chapter_ids)


def clear_all_read_chapters(profile: dict | None, series_id: str) -> None:
    if profile is None:
        db.clear_all_read_chapters(series_id)
    else:
        profile_progress.clear_all_read_chapters(profile["id"], series_id)


def profile_path_prefix(profile: dict | None) -> str:
    """profile이 있으면 "/p/<토큰>", 없으면(관리자) 빈 문자열. <img src> 등 JS fetch를
    안 거치는 링크(커버 URL 등)를 서버가 만들어줄 때, 그 프로필의 공유 경로 안에서
    계속 열리도록 이 접두사를 붙여서 내려줘야 한다 - fetch는 프론트엔드에서 자체적으로
    감싸서 처리하지만, <img src>는 그럴 수 없어 서버가 애초에 맞는 경로로 내려줘야 한다."""
    return f"/p/{profile['token']}" if profile else ""
