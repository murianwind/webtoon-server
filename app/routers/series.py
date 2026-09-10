"""
시리즈 목록/조회, 이어보기, 진행률, 읽음 상태, 회차 목록, 커버, info.xml 관련 라우트.

각 라우트는 request.state.profile을 확인해서, 공유 프로필로 들어온 요청이면
access_control을 통해 (a) 그 프로필이 이 시리즈에 접근 권한이 있는지 확인하고
(b) 진행률/읽음기록을 관리자와 완전히 분리된 프로필 전용 저장소에서 읽고 쓴다.
profile이 None(관리자)일 때는 access_control이 그대로 db.py에 위임하므로, 관리자
경로의 동작은 이 파일을 프로필 인식하게 고치기 전과 100% 동일하다.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import access_control, catalog, db, profiles as profiles_module, scan, services

router = APIRouter()


@router.get("/api/series")
def list_series(request: Request):
    profile = access_control.get_profile(request)
    allowed_ids = profiles_allowed_ids(profile)

    result = []
    for series in catalog.get_series_map().values():
        if allowed_ids is not None and series["id"] not in allowed_ids:
            continue

        chapters = series["chapters"]
        total = len(chapters)
        if profile is None:
            prog = db.get_progress(series["id"])
            services.migrate_legacy_progress_if_needed(series["id"], chapters, prog)
        read_ids = access_control.get_read_chapter_ids(profile, series["id"])
        unread = sum(1 for chapter in chapters if chapter["id"] not in read_ids)

        if total == 0:
            progress_display = ""
        elif unread == 0:
            progress_display = "완독"
        else:
            # 아직 안 읽은 회차 중 가장 앞선 것(중간에 빠졌던 회차일 수도 있음) / 마지막 회차
            next_unread = next(chapter for chapter in chapters if chapter["id"] not in read_ids)
            current_label = services.chapter_number_part(next_unread["label"])
            last_label = services.chapter_number_part(chapters[-1]["label"])
            progress_display = f"{current_label}/{last_label}"

        result.append(
            {
                "id": series["id"],
                "platform": series["platform"],
                "title": series["title"],
                "chapter_count": total,
                "unread_count": unread,
                "progress_display": progress_display,
                "latest_update": series["latest_mtime"],
                "cover_url": f"{access_control.profile_path_prefix(profile)}/api/series/{series['id']}/cover",
            }
        )
    result.sort(key=lambda item: (item["platform"], item["title"]))
    return result


def profiles_allowed_ids(profile: dict | None) -> set[str] | None:
    """profile이 없으면(관리자) None(=전체 다 허용, 필터링 안 함), 있으면 그 프로필의
    허용 목록 집합을 반환한다. list_series에서만 쓰는 작은 헬퍼라 여기 둔다."""
    if profile is None:
        return None
    return profiles_module.get_allowed_series_ids(profile["id"])


@router.get("/api/lookup/latest")
def lookup_latest(series: str, platform: str | None = None):
    """
    hermes(webtoon_checker.py 등)가 디스코드 알림에 붙일 바로가기 URL을 구할 때 쓰는 API.
    시리즈 폴더명만으로 찾을 수 있다 - platform은 선택사항이며, 여러 플랫폼에 같은 이름의
    시리즈가 있어 구분이 필요할 때만 넘기면 된다. (platform을 필수로 요구하면, 서버 쪽
    /library 폴더명을 나중에 바꿀 때마다 호출하는 쪽 코드도 같이 고쳐야 하는 문제가 있었음)

    관리자 전용 기능이라(알림 봇은 관리자 쪽 데이터만 다룸) 프로필 인식은 하지 않는다.
    """
    for candidate in catalog.get_series_map().values():
        if candidate["title"] != series:
            continue
        if platform is not None and candidate["platform"] != platform:
            continue
        if not candidate["chapters"]:
            raise HTTPException(404, "series has no chapters")
        latest = candidate["chapters"][-1]
        url = None
        if services.PUBLIC_BASE_URL:
            url = f"{services.PUBLIC_BASE_URL}/reader.html?series={candidate['id']}&chapter={latest['id']}&page=0"
        return {
            "series_id": candidate["id"],
            "chapter_id": latest["id"],
            "chapter_label": latest["label"],
            "url": url,
        }
    raise HTTPException(404, "series not found")


@router.get("/api/series/{series_id}/continue")
async def continue_reading(series_id: str, request: Request):
    """이 시리즈를 열었을 때 바로 이동해야 할 (회차, 페이지) 반환."""
    profile = access_control.get_profile(request)
    access_control.ensure_series_accessible(profile, series_id)

    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    if not series["chapters"]:
        raise HTTPException(404, "no chapters")

    prog = access_control.get_progress(profile, series_id)
    if prog:
        idx = next((i for i, ch in enumerate(series["chapters"]) if ch["id"] == prog["chapter_id"]), None)
        if idx is not None:
            # zip을 열어 페이지 수를 세는 건 네트워크 드라이브(원드라이브 등)에서 캐시가
            # 안 되어 있으면 느릴 수 있는 블로킹 작업이다. run_platform_io로 넘겨서,
            # 이 플랫폼이 네트워크 드라이브면 격리된 전용 스레드풀로 보내(로컬 작업까지
            # 덩달아 느려지지 않게), 그게 아니면 평소처럼 기본 스레드풀로 보낸다.
            page_count = len(await services.run_platform_io(series["platform"], scan.list_zip_image_names, series["chapters"][idx]["path"]))
            # 저장된 page_index가 실제 페이지 수 이상이면 "이 회차는 다 읽음" 신호 ->
            # 다음 화가 있으면 그쪽으로, 없으면(마지막 화) 마지막 페이지로 보정
            if prog["page_index"] >= page_count and idx + 1 < len(series["chapters"]):
                next_chapter = series["chapters"][idx + 1]
                return {"chapter_id": next_chapter["id"], "page_index": 0}
            clamped_page = min(prog["page_index"], max(page_count - 1, 0))
            return {"chapter_id": prog["chapter_id"], "page_index": clamped_page}

    first_chapter = series["chapters"][0]
    return {"chapter_id": first_chapter["id"], "page_index": 0}


class ProgressIn(BaseModel):
    chapter_id: str
    page_index: int = 0


@router.put("/api/series/{series_id}/progress")
async def save_progress(series_id: str, body: ProgressIn, request: Request):
    profile = access_control.get_profile(request)
    access_control.ensure_series_accessible(profile, series_id)

    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    idx = next((i for i, ch in enumerate(chapters) if ch["id"] == body.chapter_id), None)
    if idx is None:
        raise HTTPException(404, "chapter not found in series")
    # 스크롤로 여기까지 왔다는 건 이 앞 회차는 다 지나왔다는 뜻이니 명시적으로 읽음 기록
    # (지금 보고 있는 회차 자체는 "읽는 중"이지 "읽음"이 아니므로 제외)
    access_control.mark_chapters_read(profile, series_id, [ch["id"] for ch in chapters[:idx]])

    is_last_chapter = idx == len(chapters) - 1
    if is_last_chapter:
        # 마지막 화는 무한스크롤로 "다음 화에 진입"하는 신호가 절대 발생하지 않아서,
        # 그것만 보고 있으면 아무리 끝까지 읽어도 영원히 "읽는 중"에 머무르게 된다.
        # 그래서 마지막 화에 한해서는, 실제로 마지막 페이지까지 도달했으면 그 자체를
        # 완독으로 인정한다. (run_platform_io 이유는 continue_reading과 동일)
        page_count = len(await services.run_platform_io(series["platform"], scan.list_zip_image_names, chapters[idx]["path"]))
        if page_count > 0 and body.page_index >= page_count - 1:
            access_control.mark_chapters_read(profile, series_id, [chapters[idx]["id"]])
            access_control.set_progress(profile, series_id, body.chapter_id, idx, db.PAGE_FINISHED_SENTINEL)
            return {"ok": True}

    access_control.set_progress(profile, series_id, body.chapter_id, idx, max(body.page_index, 0))
    return {"ok": True}


class ReadStateIn(BaseModel):
    scope: str  # "all" | "chapter"
    read: bool
    chapter_id: str | None = None


@router.put("/api/series/{series_id}/read-state")
def set_read_state(series_id: str, body: ReadStateIn, request: Request):
    profile = access_control.get_profile(request)
    access_control.ensure_series_accessible(profile, series_id)

    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    if not chapters:
        raise HTTPException(404, "no chapters")

    if body.scope == "all":
        if body.read:
            access_control.mark_chapters_read(profile, series_id, [ch["id"] for ch in chapters])
            last = chapters[-1]
            access_control.set_progress(profile, series_id, last["id"], len(chapters) - 1, db.PAGE_FINISHED_SENTINEL)
        else:
            access_control.clear_all_read_chapters(profile, series_id)
            access_control.delete_progress(profile, series_id)
    elif body.scope == "chapter":
        if not body.chapter_id:
            raise HTTPException(400, "chapter_id is required for scope=chapter")
        idx = next((i for i, ch in enumerate(chapters) if ch["id"] == body.chapter_id), None)
        if idx is None:
            raise HTTPException(404, "chapter not found in series")

        prog = access_control.get_progress(profile, series_id)
        current_index = services.resolve_read_index(chapters, prog)

        if body.read:
            # 선택한 회차 "이전(및 선택한 회차 자체)"을 전부 읽음으로 명시 기록.
            # 다른 회차의 읽음 여부는 안 건드리므로, 이미 더 뒤까지 읽었어도 그대로 유지됨.
            access_control.mark_chapters_read(profile, series_id, [ch["id"] for ch in chapters[: idx + 1]])
            # "현재 읽는 중" 위치는 이미 그보다 더 뒤에 있었다면 되돌리지 않음
            if idx >= current_index:
                access_control.set_progress(profile, series_id, chapters[idx]["id"], idx, db.PAGE_FINISHED_SENTINEL)
        else:
            # 선택한 회차 "부터(포함)"를 읽음 기록에서 제거 (선택한 회차 자체도 안읽음이 됨)
            access_control.mark_chapters_unread(profile, series_id, [ch["id"] for ch in chapters[idx:]])
            # "현재 읽는 중" 위치가 방금 안읽음 처리한 구간 안에 있었다면 그 앞으로 당김
            if current_index >= idx:
                if idx == 0:
                    access_control.delete_progress(profile, series_id)
                else:
                    prev_chapter = chapters[idx - 1]
                    access_control.set_progress(profile, series_id, prev_chapter["id"], idx - 1, db.PAGE_FINISHED_SENTINEL)
    else:
        raise HTTPException(400, "scope must be 'all' or 'chapter'")

    return {"ok": True}


@router.get("/api/series/{series_id}/chapters")
def list_chapters(series_id: str, request: Request):
    profile = access_control.get_profile(request)
    access_control.ensure_series_accessible(profile, series_id)

    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    prog = access_control.get_progress(profile, series_id)
    if profile is None:
        services.migrate_legacy_progress_if_needed(series_id, chapters, prog)
    read_ids = access_control.get_read_chapter_ids(profile, series_id)
    current_chapter_id = prog["chapter_id"] if prog else None
    # 완독 처리(마지막 화를 끝까지 읽었을 때)는 이어보기 포인터에 PAGE_FINISHED_SENTINEL을
    # 저장해서 "이 회차는 끝까지 다 봤다"를 표시한다. 이 값이면 포인터가 그 회차를
    # 가리키고 있어도 "지금 읽는 중"이 아니라 "다 읽었다"는 뜻이므로, 아래에서 "읽는 중"
    # 판단에 포함시키지 않는다 - 안 그러면 마지막 화를 완독한 뒤 새 화가 추가돼도(포인터가
    # 아직 그 회차를 가리키고 있으니) 완독된 회차가 계속 "읽는 중"으로 잘못 보이게 된다.
    current_is_finished_sentinel = bool(prog) and prog["page_index"] == db.PAGE_FINISHED_SENTINEL

    chapters_out = []
    for chapter in chapters:
        is_reading = chapter["id"] == current_chapter_id and not current_is_finished_sentinel
        # "지금 보고 있는 회차"라는 정보가 "예전에 읽었는지"보다 더 구체적이고 우선한다 -
        # 사이드바에서 이미 읽었던 회차로 다시 돌아가서 보면(예: 10화까지 읽다가 2화를
        # 다시 열어봄), 그 회차는 "읽음" 기록이 있어도 지금 보는 중이라는 게 더 중요한
        # 정보이므로 "읽는 중"으로 표시해야 한다. 예전에는 "이미 읽음"이 우선이라 다시
        # 펴봐도 "읽는 중"이 뜰 자리가 없는 문제가 있었다.
        is_read = (chapter["id"] in read_ids) and not is_reading
        chapters_out.append(
            {
                "id": chapter["id"],
                "label": chapter["label"],
                "sort_key": chapter["sort_key"],
                "read": is_read,
                "reading": is_reading,
            }
        )

    return {
        "id": series["id"],
        "platform": series["platform"],
        "title": series["title"],
        "chapters": chapters_out,
    }


@router.post("/api/series/{series_id}/chapters/{chapter_id}/toggle-read")
def toggle_chapter_read(series_id: str, chapter_id: str, request: Request):
    """
    회차 하나만 콕 집어 읽음/안읽음을 반전시킨다 (범위 지정 없이 그 회차 자체만).
    다른 회차의 읽음 기록은 전혀 건드리지 않는다.

    다만 "이어보기(진행률 포인터)"는 이 회차별 읽음 기록과 별개로 저장되어 있어서,
    그냥 두면 여기서 안읽음으로 바꾼 회차가 이미 지나간 걸로 남아 이어보기가 엉뚱한
    (더 뒤의) 위치를 계속 가리키게 된다. 그래서 지금 이어보기 위치와 비교해서 필요하면
    포인터도 같이 당겨준다/밀어준다 - 전체읽음/부분읽음 처리(set_read_state)와 같은 원리.
    """
    profile = access_control.get_profile(request)
    access_control.ensure_series_accessible(profile, series_id)

    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    idx = next((i for i, chapter in enumerate(chapters) if chapter["id"] == chapter_id), None)
    if idx is None:
        raise HTTPException(404, "chapter not found in series")

    read_ids = access_control.get_read_chapter_ids(profile, series_id)
    prog = access_control.get_progress(profile, series_id)
    current_index = services.resolve_read_index(chapters, prog)

    if chapter_id in read_ids:
        access_control.mark_chapters_unread(profile, series_id, [chapter_id])
        now_read = False
        # 안읽음으로 바꾼 회차가 지금 이어보기 위치와 같거나 그 이전이면, 이어보기 기준점을
        # "그 이전 회차 완독"이 아니라 방금 안읽음으로 만든 이 회차 자체(0페이지)로 옮긴다.
        # 이전 회차를 가리키게 하면 그 회차는 이미 읽음 상태라 "읽는 중" 표시가 나올 자리가
        # 아예 없어져 버린다 - 방금 안읽음으로 만든 회차 쪽이 "읽는 중"으로 보여야 자연스럽다.
        if current_index >= idx:
            access_control.set_progress(profile, series_id, chapter_id, idx, 0)
    else:
        access_control.mark_chapters_read(profile, series_id, [chapter_id])
        now_read = True
        # 읽음으로 바꾼 회차가 지금 이어보기 위치보다 뒤라면, 이어보기 기준점도 여기로 당긴다.
        if idx >= current_index:
            access_control.set_progress(profile, series_id, chapter_id, idx, db.PAGE_FINISHED_SENTINEL)

    return {"ok": True, "read": now_read}


@router.get("/api/series/{series_id}/info")
def series_info(series_id: str, request: Request):
    """
    info.xml(카카오 등 일부 플랫폼에만 있음)에서 뽑아둔 작가/장르/줄거리/연재상태/연령등급/
    원작 링크를 반환. info.xml이 없는 시리즈(네이버 등)는 404.

    표지와 마찬가지로 완화된 접근 검사를 쓴다 - 둘러보기(아직 허용 전) 항목도
    정보를 미리 볼 수 있어야 하기 때문이다.
    """
    profile = access_control.get_profile(request)
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    access_control.ensure_series_previewable(profile, series)

    info = series.get("info")
    if not info:
        raise HTTPException(404, "no info available for this series")
    return info


@router.get("/api/series/{series_id}/cover")
async def series_cover(series_id: str, request: Request):
    profile = access_control.get_profile(request)
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "no cover")
    # 회차 목록/페이지 등과 달리, 표지는 둘러보기(아직 완전히 허용되지 않은 시리즈)
    # 화면에서도 미리 보여줘야 하므로 완화된 검사를 쓴다 - 실제 콘텐츠(회차)는
    # 여전히 엄격한 ensure_series_accessible로 막혀있으니 안전하다.
    access_control.ensure_series_previewable(profile, series)

    try:
        result = await asyncio.wait_for(
            services.ensure_cover_cached(series), timeout=services.SERIES_SCAN_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # 네트워크 드라이브가 응답이 없어도 브라우저가 무한정 기다리지 않고 에러로
        # 끝나게 한다 - 그래야 다음에 다시 시도할 수 있다(무한 스피너 방지).
        raise HTTPException(504, "cover generation timed out")
    if result is None:
        raise HTTPException(404, "no cover")
    data, media_type = result
    return Response(content=data, media_type=media_type)
