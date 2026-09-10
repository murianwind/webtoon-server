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
    profile이 None(관리자)이면 항상 통과 - 기존 동작 그대로."""
    if profile is None:
        return
    if series_id not in profiles.get_allowed_series_ids(profile["id"]):
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
