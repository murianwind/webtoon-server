"""
webtoon-server FastAPI 앱.

이 파일은 라우트 정의와 앱 생명주기(시작 시 스캔, 자동 재스캔 루프)만 담당한다.
실제 로직은 각자 책임이 분리된 모듈에 있다:
  - db.py       읽음 진행률 / 설정 / 제외목록 / 겹침캐시 / 백업·복원 (SQLite)
  - catalog.py  스캔 결과를 담아두는 메모리 상태
  - scan.py     파일시스템 스캔 + 회차 라벨 파싱
  - overlap.py  화 전환 겹침(리캡) 감지 + 백그라운드 사전계산
  - covers.py   시리즈 커버 썸네일 생성/캐싱
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import zipfile
from datetime import date, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import catalog, covers, db, overlap, scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("webtoon-server")

# 외부(디스코드 등)에 공개되는 URL을 만들 때 쓰는 기준 주소. 예: https://your-domain.example.com
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# 라이브러리 자동 재스캔 주기(초). 기본 2시간. 0 이하로 설정하면 자동 재스캔을 끈다.
RESCAN_INTERVAL_SECONDS = int(os.environ.get("RESCAN_INTERVAL_SECONDS", "7200"))

BACKUP_VERSION = 2  # v2부터 read_chapters(회차별 명시 읽음 기록) 포함

# SLOW_PLATFORMS(네트워크 드라이브)로 지정된 플랫폼의 파일 I/O는 이 전용 스레드풀로만
# 보낸다. asyncio.to_thread()가 쓰는 기본 스레드풀은 앱 전체가 공유하는 자원이라, 응답
# 없는 네트워크 호출 하나가 스레드를 계속 붙잡고 있으면(타임아웃으로 "기다리는 걸
# 포기"해도 그 스레드 자체는 여전히 멈춰있을 수 있음) 그 풀을 나눠 쓰는 로컬 파일
# 읽기까지 차례를 못 받아 전체 서비스가 느려진다. 완전히 분리된 풀을 쓰면, 네트워크
# 쪽이 전부 막혀버려도 로컬 쪽 작업은 전혀 영향을 안 받는다.
_NETWORK_IO_WORKERS = int(os.environ.get("NETWORK_IO_WORKERS", "4"))
_network_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_NETWORK_IO_WORKERS, thread_name_prefix="network-io"
)


async def run_platform_io(platform: str, func, *args):
    """
    platform이 SLOW_PLATFORMS(네트워크 드라이브)면 격리된 전용 스레드풀로, 아니면
    평소처럼 asyncio.to_thread()(기본 스레드풀)로 보낸다.
    """
    if scan.is_slow_platform(platform):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_network_executor, func, *args)
    return await asyncio.to_thread(func, *args)


import re

app = FastAPI(title="webtoon-server")

# 커버/페이지 이미지는 내용이 거의 안 바뀌니 캐싱이 오히려 유리해서 캐시 금지 대상에서 뺀다.
_CACHEABLE_IMAGE_PATH = re.compile(r"^/api/series/[^/]+/cover$|^/api/chapters/[^/]+/pages/\d+$")


@app.middleware("http")
async def no_cache_for_dynamic_api(request, call_next):
    """
    /api/* 응답은 매번 최신 상태를 반영해야 하는 동적 데이터라(읽음 진행률, 안읽음 개수 등),
    브라우저가 임의로 캐싱하면 안 된다. 실제로 브라우저 자체 뒤로가기로 목록 화면에 돌아왔을 때
    fetch 자체는 다시 일어나면서도 브라우저가 이전 응답을 재사용해 안읽음 개수가 갱신 안
    되는 문제가 있었다 - 이 헤더가 없으면 캐시 여부가 브라우저 판단에 맡겨지는 게 원인이었다.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/") and not _CACHEABLE_IMAGE_PATH.match(path):
        response.headers["Cache-Control"] = "no-store"
    return response


def _chapter_number_part(label: str) -> str:
    """라벨에서 제목 부분(' · ' 뒤)을 떼고 회차 번호 부분만 반환."""
    return label.split(" · ", 1)[0]


def _resolve_read_index(chapters: list, prog: dict | None) -> int:
    """
    저장된 진행률(prog)의 chapter_id가 "지금" 스캔 결과에서 몇 번째 위치인지 다시 찾는다.
    (레거시 마이그레이션과 "현재 읽는 중" 위치 판단에만 쓰인다 - 읽음/안읽음 자체는 이제
    회차별 명시 기록(read_chapters)으로 판단하므로 인덱스 밀림 문제에서 자유롭다.)
    """
    if not prog:
        return -1
    for i, chapter in enumerate(chapters):
        if chapter["id"] == prog["chapter_id"]:
            return i
    return -1  # 저장된 회차가 더 이상 없으면(파일 삭제 등) 진행 없음으로 취급


def _migrate_legacy_progress_if_needed(series_id: str, chapters: list, prog: dict | None) -> None:
    """
    예전 버전은 "몇 번째 회차까지 읽었다"는 단일 커서(chapter_index)만 저장했다. 그 방식은
    나중에 빠졌던 회차가 사이사이에 채워지면, 그 순간의 커서보다 앞이라는 이유만으로 실제로는
    한 번도 안 본 회차까지 "이미 읽음"으로 잘못 취급해버리는 문제가 있었다. 그래서 회차별로
    명시적으로 기록하는 방식(read_chapters)으로 바꿨는데, 기존에 이미 진행률이 쌓여있던
    사용자의 데이터가 갑자기 전부 안읽음으로 보이면 안 되니, 최초 1회만 예전 커서를 기준으로
    read_chapters를 채워 넣어 자연스럽게 이어지게 한다.
    """
    if not prog or db.get_read_chapter_ids(series_id):
        return  # 진행률이 없거나 이미 새 방식으로 기록된 적 있으면 할 필요 없음
    idx = _resolve_read_index(chapters, prog)
    if idx < 0:
        return
    if prog["page_index"] >= db.PAGE_FINISHED_SENTINEL:
        ids_to_mark = [chapter["id"] for chapter in chapters[: idx + 1]]
    else:
        ids_to_mark = [chapter["id"] for chapter in chapters[:idx]]
    db.mark_chapters_read(series_id, ids_to_mark)


def _log_scan_result(prefix: str, series_map: dict, chapters_map: dict, added: int | None = None, removed: int | None = None) -> None:
    if added is None:
        log.info(f"{prefix} - 시리즈 {len(series_map)}개, 회차 {len(chapters_map)}개")
    elif added or removed:
        log.info(f"{prefix} - 시리즈 {len(series_map)}개 (신규 {added}, 제거 {removed}), 회차 {len(chapters_map)}개")
    else:
        log.info(f"{prefix} - 변경 없음 (시리즈 {len(series_map)}개, 회차 {len(chapters_map)}개)")


SERIES_SCAN_TIMEOUT_SECONDS = int(os.environ.get("SERIES_SCAN_TIMEOUT_SECONDS", "30"))


def _advance_generator(gen):
    """제너레이터를 한 칸 진행시켜서 다음 값을 반환하거나, 끝났으면 None을 반환한다.
    스레드에서 실행하기 위한 평범한 동기 함수(제너레이터 자체는 동기 코드이므로)."""
    return next(gen, None)


async def _scan_all_platforms_incrementally() -> tuple[dict, dict]:
    """
    플랫폼을 하나씩(로컬 먼저, 네트워크 드라이브는 나중에) 스캔한다. 플랫폼 안에서도
    "폴더 구조를 전부 훑고 나서 하나씩 스캔"이 아니라 "폴더 하나를 발견하는 즉시 그
    자리에서 스캔해서 반영"하는 스트리밍 방식이다 - 네트워크 드라이브에 폴더가 아주
    많으면 "폴더 구조 전체를 훑는 것" 자체가 오래 걸릴 수 있는데, 그걸 다 기다렸다가
    스캔을 시작하면 첫 결과가 나오기까지도 그만큼 오래 걸리기 때문이다.

    시리즈 하나(정확히는 "다음 항목을 찾는 것")가 응답 없이 멈춰버리면 시간 제한을 걸어
    포기하고 그 플랫폼의 나머지는 다음 재스캔에서 이어서 시도한다. (이 시간 제한은
    asyncio 쪽에서 "기다리는 걸 그만두는" 것이라, 정말로 응답이 안 오는 네트워크 호출
    자체는 스레드 안에서 계속 멈춰있을 수 있다 - 그래서 네트워크 플랫폼의 파일 I/O는
    run_platform_io()로 로컬과 격리된 전용 스레드풀에 보내서, 이런 일이 반복돼도 로컬
    플랫폼 작업까지 덩달아 느려지지 않게 한다.)
    """
    platforms = await asyncio.to_thread(scan.list_platforms_in_scan_order)
    # 폴더 이름만 훑는 거라 거의 즉시 끝남 - 실제 시리즈 스캔이 끝나기 전에 먼저 기록해둬서,
    # 프론트엔드가 "이런 플랫폼이 있다"를 미리 보여줄 수 있게 한다(플랫폼 필터 탭 등).
    catalog.set_known_platforms(platforms)

    for platform in platforms:
        gen = scan.iter_platform_series_streaming(platform)
        seen_ids = set()
        seen_refs = []
        completed = False
        while True:
            try:
                item = await asyncio.wait_for(
                    run_platform_io(platform, _advance_generator, gen),
                    timeout=SERIES_SCAN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                log.warning(
                    f"'{platform}' 탐색 중 한 항목이 {SERIES_SCAN_TIMEOUT_SECONDS}초를 넘겨 "
                    f"이번 스캔은 여기서 중단 (마운트 상태 확인 필요, 다음 재스캔에서 처음부터 재시도됨)"
                )
                break
            except Exception:
                log.exception(f"'{platform}' 탐색 중 오류 발생 - 이번 스캔은 여기서 중단")
                break
            if item is None:
                completed = True  # 이 플랫폼은 끝까지 다 훑었음(중단이 아니라)
                break
            series_ref, series_entry, chapters_map = item
            seen_refs.append(series_ref)  # 제외된 폴더도 "발견됨" 자체는 계속 기록(설정 패널용)
            catalog.set_platform_folder_refs(platform, list(seen_refs))
            if series_entry:  # 제외되지 않아서 실제로 스캔된 경우만 카탈로그에 반영
                catalog.add_series(series_entry, chapters_map)
                seen_ids.add(series_entry["id"])

        # 이번 스캔에서 다시 나타나지 않은(삭제되었거나 새로 제외된) 기존 시리즈는 정리.
        # 중간에 타임아웃/오류로 멈췄다면 seen_ids가 이번에 실제로 확인된 것까지만 담고
        # 있어서, 아직 못 훑은 뒷부분의 기존 시리즈까지 정리해버리면 안 되므로, 끝까지
        # 완주했을 때만(completed) 안전하게 정리한다.
        if completed:
            catalog.prune_platform_series(platform, seen_ids)
        log.info(f"  - '{platform}' 스캔 완료 (시리즈 {len(seen_ids)}개, 폴더 발견 즉시 반영됨)")
    return catalog.get_series_map(), catalog.get_chapters_map()


# ---------------------------------------------------------------------------
# 앱 생명주기: 시작 시 스캔, 자동/수동 재스캔
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup_scan():
    db.init_schema()
    # 첫 스캔도 백그라운드로 돌린다 - 네트워크 드라이브(원드라이브 등)가 섞여 있으면
    # 전체 스캔에 시간이 걸릴 수 있는데, 그걸 여기서 기다리면 컨테이너 시작 자체가
    # 그만큼 느려진다. create_task로 넘기면 서버는 바로 요청을 받기 시작하고,
    # 플랫폼별로 스캔이 끝나는 대로 목록에 자연스럽게 반영된다.
    asyncio.create_task(_initial_scan())

    if RESCAN_INTERVAL_SECONDS > 0:
        log.info(f"자동 재스캔 활성화 - {RESCAN_INTERVAL_SECONDS / 60:.0f}분마다 실행")
        asyncio.create_task(_auto_rescan_loop())
    else:
        log.info("자동 재스캔 비활성화됨 (RESCAN_INTERVAL_SECONDS <= 0)")


async def _initial_scan() -> None:
    log.info(f"라이브러리 스캔 시작 (경로: {scan.LIBRARY_ROOT}) - 백그라운드로 진행, 서버는 이미 요청을 받고 있음")
    try:
        series_map, chapters_map = await _scan_all_platforms_incrementally()
        _log_scan_result(f"라이브러리 스캔 완료 (경로: {scan.LIBRARY_ROOT})", series_map, chapters_map)
        asyncio.create_task(overlap.precompute_overlaps())
    except Exception:
        log.exception("초기 스캔 중 오류 발생")


async def _auto_rescan_loop():
    while True:
        await asyncio.sleep(RESCAN_INTERVAL_SECONDS)
        try:
            old_ids = set(catalog.get_series_map().keys())
            series_map, chapters_map = await _scan_all_platforms_incrementally()
            added = len(set(series_map.keys()) - old_ids)
            removed = len(old_ids - set(series_map.keys()))
            _log_scan_result("자동 재스캔 완료", series_map, chapters_map, added, removed)
            asyncio.create_task(overlap.precompute_overlaps())
        except Exception:
            # 한 번 실패해도 다음 주기에 다시 시도 - 서버가 죽으면 안 됨
            log.exception("자동 재스캔 중 오류 발생 - 다음 주기에 재시도")


@app.post("/api/rescan")
async def rescan():
    old_ids = set(catalog.get_series_map().keys())
    series_map, chapters_map = await _scan_all_platforms_incrementally()
    added = len(set(series_map.keys()) - old_ids)
    removed = len(old_ids - set(series_map.keys()))
    _log_scan_result("수동 재스캔 완료", series_map, chapters_map, added, removed)
    asyncio.create_task(overlap.precompute_overlaps())
    return {"series_count": len(series_map)}


@app.get("/api/scan-status")
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


@app.get("/api/series-folders")
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


@app.post("/api/series-folders/exclude")
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


@app.post("/api/series-folders/include")
async def include_series_folder(body: SeriesFolderRef):
    """재포함도 전체 재스캔이 아니라 이 폴더 하나만 다시 읽어서 카탈로그에 더한다."""
    excluded = db.get_excluded_series()
    excluded.discard((body.platform, body.series))
    db.set_excluded_series(excluded)

    result = await run_platform_io(body.platform, scan.scan_single_series, body.platform, body.series)
    if result:
        series_entry, chapters_map = result
        catalog.add_series(series_entry, chapters_map)
        asyncio.create_task(overlap.precompute_overlaps())
    log.info(f"시리즈 폴더 다시 포함: {body.platform}/{body.series}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 시리즈 목록 / 조회
# ---------------------------------------------------------------------------


@app.get("/api/series")
def list_series():
    result = []
    for series in catalog.get_series_map().values():
        chapters = series["chapters"]
        total = len(chapters)
        prog = db.get_progress(series["id"])
        _migrate_legacy_progress_if_needed(series["id"], chapters, prog)
        read_ids = db.get_read_chapter_ids(series["id"])
        unread = sum(1 for chapter in chapters if chapter["id"] not in read_ids)

        if total == 0:
            progress_display = ""
        elif unread == 0:
            progress_display = "완독"
        else:
            # 아직 안 읽은 회차 중 가장 앞선 것(중간에 빠졌던 회차일 수도 있음) / 마지막 회차
            next_unread = next(chapter for chapter in chapters if chapter["id"] not in read_ids)
            current_label = _chapter_number_part(next_unread["label"])
            last_label = _chapter_number_part(chapters[-1]["label"])
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
                "cover_url": f"/api/series/{series['id']}/cover",
            }
        )
    result.sort(key=lambda item: (item["platform"], item["title"]))
    return result


@app.get("/api/lookup/latest")
def lookup_latest(series: str, platform: str | None = None):
    """
    hermes(webtoon_checker.py 등)가 디스코드 알림에 붙일 바로가기 URL을 구할 때 쓰는 API.
    시리즈 폴더명만으로 찾을 수 있다 - platform은 선택사항이며, 여러 플랫폼에 같은 이름의
    시리즈가 있어 구분이 필요할 때만 넘기면 된다. (platform을 필수로 요구하면, 서버 쪽
    /library 폴더명을 나중에 바꿀 때마다 호출하는 쪽 코드도 같이 고쳐야 하는 문제가 있었음)
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
        if PUBLIC_BASE_URL:
            url = f"{PUBLIC_BASE_URL}/reader.html?series={candidate['id']}&chapter={latest['id']}&page=0"
        return {
            "series_id": candidate["id"],
            "chapter_id": latest["id"],
            "chapter_label": latest["label"],
            "url": url,
        }
    raise HTTPException(404, "series not found")


@app.get("/api/series/{series_id}/continue")
async def continue_reading(series_id: str):
    """이 시리즈를 열었을 때 바로 이동해야 할 (회차, 페이지) 반환."""
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    if not series["chapters"]:
        raise HTTPException(404, "no chapters")

    prog = db.get_progress(series_id)
    if prog:
        idx = next((i for i, ch in enumerate(series["chapters"]) if ch["id"] == prog["chapter_id"]), None)
        if idx is not None:
            # zip을 열어 페이지 수를 세는 건 네트워크 드라이브(원드라이브 등)에서 캐시가
            # 안 되어 있으면 느릴 수 있는 블로킹 작업이다. run_platform_io로 넘겨서,
            # 이 플랫폼이 네트워크 드라이브면 격리된 전용 스레드풀로 보내(로컬 작업까지
            # 덩달아 느려지지 않게), 그게 아니면 평소처럼 기본 스레드풀로 보낸다.
            page_count = len(await run_platform_io(series["platform"], scan.list_zip_image_names, series["chapters"][idx]["path"]))
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


@app.put("/api/series/{series_id}/progress")
async def save_progress(series_id: str, body: ProgressIn):
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    idx = next((i for i, ch in enumerate(chapters) if ch["id"] == body.chapter_id), None)
    if idx is None:
        raise HTTPException(404, "chapter not found in series")
    # 스크롤로 여기까지 왔다는 건 이 앞 회차는 다 지나왔다는 뜻이니 명시적으로 읽음 기록
    # (지금 보고 있는 회차 자체는 "읽는 중"이지 "읽음"이 아니므로 제외)
    db.mark_chapters_read(series_id, [ch["id"] for ch in chapters[:idx]])

    is_last_chapter = idx == len(chapters) - 1
    if is_last_chapter:
        # 마지막 화는 무한스크롤로 "다음 화에 진입"하는 신호가 절대 발생하지 않아서,
        # 그것만 보고 있으면 아무리 끝까지 읽어도 영원히 "읽는 중"에 머무르게 된다.
        # 그래서 마지막 화에 한해서는, 실제로 마지막 페이지까지 도달했으면 그 자체를
        # 완독으로 인정한다. (run_platform_io 이유는 continue_reading과 동일)
        page_count = len(await run_platform_io(series["platform"], scan.list_zip_image_names, chapters[idx]["path"]))
        if page_count > 0 and body.page_index >= page_count - 1:
            db.mark_chapters_read(series_id, [chapters[idx]["id"]])
            db.set_progress(series_id, body.chapter_id, idx, db.PAGE_FINISHED_SENTINEL)
            return {"ok": True}

    db.set_progress(series_id, body.chapter_id, idx, max(body.page_index, 0))
    return {"ok": True}


class ReadStateIn(BaseModel):
    scope: str  # "all" | "chapter"
    read: bool
    chapter_id: str | None = None


@app.put("/api/series/{series_id}/read-state")
def set_read_state(series_id: str, body: ReadStateIn):
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    if not chapters:
        raise HTTPException(404, "no chapters")

    if body.scope == "all":
        if body.read:
            db.mark_chapters_read(series_id, [ch["id"] for ch in chapters])
            last = chapters[-1]
            db.set_progress(series_id, last["id"], len(chapters) - 1, db.PAGE_FINISHED_SENTINEL)
        else:
            db.clear_all_read_chapters(series_id)
            db.delete_progress(series_id)
    elif body.scope == "chapter":
        if not body.chapter_id:
            raise HTTPException(400, "chapter_id is required for scope=chapter")
        idx = next((i for i, ch in enumerate(chapters) if ch["id"] == body.chapter_id), None)
        if idx is None:
            raise HTTPException(404, "chapter not found in series")

        prog = db.get_progress(series_id)
        current_index = _resolve_read_index(chapters, prog)

        if body.read:
            # 선택한 회차 "이전(및 선택한 회차 자체)"을 전부 읽음으로 명시 기록.
            # 다른 회차의 읽음 여부는 안 건드리므로, 이미 더 뒤까지 읽었어도 그대로 유지됨.
            db.mark_chapters_read(series_id, [ch["id"] for ch in chapters[: idx + 1]])
            # "현재 읽는 중" 위치는 이미 그보다 더 뒤에 있었다면 되돌리지 않음
            if idx >= current_index:
                db.set_progress(series_id, chapters[idx]["id"], idx, db.PAGE_FINISHED_SENTINEL)
        else:
            # 선택한 회차 "부터(포함)"를 읽음 기록에서 제거 (선택한 회차 자체도 안읽음이 됨)
            db.mark_chapters_unread(series_id, [ch["id"] for ch in chapters[idx:]])
            # "현재 읽는 중" 위치가 방금 안읽음 처리한 구간 안에 있었다면 그 앞으로 당김
            if current_index >= idx:
                if idx == 0:
                    db.delete_progress(series_id)
                else:
                    prev_chapter = chapters[idx - 1]
                    db.set_progress(series_id, prev_chapter["id"], idx - 1, db.PAGE_FINISHED_SENTINEL)
    else:
        raise HTTPException(400, "scope must be 'all' or 'chapter'")

    return {"ok": True}


@app.get("/api/series/{series_id}/chapters")
def list_chapters(series_id: str):
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    prog = db.get_progress(series_id)
    _migrate_legacy_progress_if_needed(series_id, chapters, prog)
    read_ids = db.get_read_chapter_ids(series_id)
    current_chapter_id = prog["chapter_id"] if prog else None

    chapters_out = []
    for chapter in chapters:
        is_read = chapter["id"] in read_ids
        # "읽는 중"은 순서와 무관하게 지금 보고 있던 바로 그 회차 하나 - 이미 읽음으로
        # 기록된 회차라면(예: 완독 처리) 굳이 읽는 중으로 겹쳐 표시하지 않음
        is_reading = (not is_read) and chapter["id"] == current_chapter_id
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


@app.post("/api/series/{series_id}/chapters/{chapter_id}/toggle-read")
def toggle_chapter_read(series_id: str, chapter_id: str):
    """
    회차 하나만 콕 집어 읽음/안읽음을 반전시킨다 (범위 지정 없이 그 회차 자체만).
    다른 회차의 읽음 기록은 전혀 건드리지 않는다.

    다만 "이어보기(진행률 포인터)"는 이 회차별 읽음 기록과 별개로 저장되어 있어서,
    그냥 두면 여기서 안읽음으로 바꾼 회차가 이미 지나간 걸로 남아 이어보기가 엉뚱한
    (더 뒤의) 위치를 계속 가리키게 된다. 그래서 지금 이어보기 위치와 비교해서 필요하면
    포인터도 같이 당겨준다/밀어준다 - 전체읽음/부분읽음 처리(set_read_state)와 같은 원리.
    """
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    chapters = series["chapters"]
    idx = next((i for i, chapter in enumerate(chapters) if chapter["id"] == chapter_id), None)
    if idx is None:
        raise HTTPException(404, "chapter not found in series")

    read_ids = db.get_read_chapter_ids(series_id)
    prog = db.get_progress(series_id)
    current_index = _resolve_read_index(chapters, prog)

    if chapter_id in read_ids:
        db.mark_chapters_unread(series_id, [chapter_id])
        now_read = False
        # 안읽음으로 바꾼 회차가 지금 이어보기 위치와 같거나 그 이전이면, 이어보기 기준점을
        # "그 이전 회차 완독"이 아니라 방금 안읽음으로 만든 이 회차 자체(0페이지)로 옮긴다.
        # 이전 회차를 가리키게 하면 그 회차는 이미 읽음 상태라 "읽는 중" 표시가 나올 자리가
        # 아예 없어져 버린다 - 방금 안읽음으로 만든 회차 쪽이 "읽는 중"으로 보여야 자연스럽다.
        if current_index >= idx:
            db.set_progress(series_id, chapter_id, idx, 0)
    else:
        db.mark_chapters_read(series_id, [chapter_id])
        now_read = True
        # 읽음으로 바꾼 회차가 지금 이어보기 위치보다 뒤라면, 이어보기 기준점도 여기로 당긴다.
        if idx >= current_index:
            db.set_progress(series_id, chapter_id, idx, db.PAGE_FINISHED_SENTINEL)

    return {"ok": True, "read": now_read}


@app.get("/api/series/{series_id}/info")
def series_info(series_id: str):
    """
    info.xml(카카오 등 일부 플랫폼에만 있음)에서 뽑아둔 작가/장르/줄거리/연재상태/연령등급/
    원작 링크를 반환. info.xml이 없는 시리즈(네이버 등)는 404.
    """
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    info = series.get("info")
    if not info:
        raise HTTPException(404, "no info available for this series")
    return info


@app.get("/api/series/{series_id}/cover")
async def series_cover(series_id: str):
    series = catalog.get_series(series_id)
    if not series:
        raise HTTPException(404, "no cover")
    platform = series["platform"]

    cover_path = series.get("cover_path")
    if cover_path and await run_platform_io(platform, os.path.isfile, cover_path):
        try:
            source_mtime = await run_platform_io(platform, os.path.getmtime, cover_path)
        except OSError:
            source_mtime = 0
        cached = covers.get_cached_cover(series_id, source_mtime)
        if cached:
            data, media_type = cached
        else:
            # 네트워크 드라이브면 파일을 읽어서 리사이즈하는 데 시간이 걸릴 수 있으니
            # run_platform_io로 넘겨서, 그 사이 다른(특히 로컬) 요청까지 같이 멈추지 않게 한다.
            data, media_type = await run_platform_io(
                platform, covers.generate_and_cache_cover_from_file, series_id, source_mtime, cover_path
            )
        return Response(content=data, media_type=media_type)

    if not series["chapters"]:
        raise HTTPException(404, "no cover")
    first_chapter = series["chapters"][0]
    names = await run_platform_io(platform, scan.list_zip_image_names, first_chapter["path"])
    if not names:
        raise HTTPException(404, "no cover image")

    try:
        source_mtime = await run_platform_io(platform, os.path.getmtime, first_chapter["path"])
    except OSError:
        source_mtime = 0

    cached = covers.get_cached_cover(series_id, source_mtime)
    if cached:
        data, media_type = cached
    else:
        data, media_type = await run_platform_io(
            platform,
            covers.generate_and_cache_cover_from_zip,
            series_id, source_mtime, first_chapter["path"], names[0],
        )

    return Response(content=data, media_type=media_type)


# ---------------------------------------------------------------------------
# 앱 설정 (검색/정렬/필터 등 기기 간 동일하게 유지할 값 저장)
# ---------------------------------------------------------------------------


@app.get("/api/settings/{key}")
def read_setting(key: str):
    return {"key": key, "value": db.get_setting(key)}


class SettingIn(BaseModel):
    value: str


@app.put("/api/settings/{key}")
def write_setting(key: str, body: SettingIn):
    db.set_setting(key, body.value)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 백업 / 복원 (읽음 진행률 + 검색/정렬/필터 설정 + 라이브러리 등록 상태 전부)
# ---------------------------------------------------------------------------


@app.get("/api/backup")
def export_backup():
    data = db.export_backup_data()
    payload = {
        "version": BACKUP_VERSION,
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


@app.post("/api/restore")
async def import_backup(body: RestorePayload):
    progress_count, settings_count, read_count = db.import_backup_data(
        body.progress, body.app_settings, body.read_chapters
    )

    # 라이브러리 등록(제외 목록) 상태도 복원됐을 수 있으니 다시 스캔해서 반영
    await _scan_all_platforms_incrementally()

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


# ---------------------------------------------------------------------------
# zip 내부 이미지 / 화 전환 겹침
# ---------------------------------------------------------------------------


@app.get("/api/chapters/{chapter_id}/pages")
async def chapter_pages(chapter_id: str):
    zip_path = catalog.get_chapter_zip_path(chapter_id)
    if not zip_path:
        raise HTTPException(404, "chapter not found")
    series, _ = catalog.find_chapter_position(chapter_id)
    platform = series["platform"] if series else ""
    names = await run_platform_io(platform, scan.list_zip_image_names, zip_path)
    return {"page_count": len(names)}


@app.get("/api/chapters/{chapter_id}/overlap")
async def chapter_overlap(chapter_id: str):
    """
    이 회차 맨 앞부분이 바로 이전 회차(같은 시리즈, 정렬상 직전) 끝부분과 겹치는
    페이지 수를 반환. 결과는 DB에 캐싱되어 다음부터는 즉시 응답한다.
    """
    zip_path = catalog.get_chapter_zip_path(chapter_id)
    if not zip_path:
        raise HTTPException(404, "chapter not found")

    series, index = catalog.find_chapter_position(chapter_id)
    prev_chapter = None
    if series is not None and index is not None and index > 0:
        prev_chapter = series["chapters"][index - 1]

    if not prev_chapter:
        return {"skip_pages": 0}

    cached = db.get_cached_overlap(chapter_id)
    if cached is not None:
        return {"skip_pages": cached}

    # 이미지 두 장의 zip을 열어 비교하는 무거운 작업 - 네트워크 드라이브면 특히 오래 걸릴
    # 수 있어 격리된 스레드풀로 넘긴다.
    platform = series["platform"]
    skip_pages = await run_platform_io(platform, overlap.compute_overlap_pages, prev_chapter["path"], zip_path)
    db.set_cached_overlap(chapter_id, prev_chapter["id"], skip_pages)
    if skip_pages > 0:
        log.info(f"화 전환 겹침 감지: {chapter_id} 앞부분 {skip_pages}페이지가 이전 화와 중복 (자동 건너뜀)")
    return {"skip_pages": skip_pages}


def _read_chapter_page_bytes(zip_path: str, page_index: int) -> tuple[bytes, str] | None:
    """스레드에서 실행되는 부분: zip 목록 조회 + 실제 페이지 바이트 읽기를 한 번에 처리."""
    names = scan.list_zip_image_names(zip_path)
    if page_index < 0 or page_index >= len(names):
        return None
    name = names[page_index]
    ext = os.path.splitext(name)[1].lower()
    with zipfile.ZipFile(zip_path) as zf:
        data = zf.read(name)
    return data, covers.IMAGE_MEDIA_TYPES.get(ext, "application/octet-stream")


@app.get("/api/chapters/{chapter_id}/pages/{page_index}")
async def chapter_page(chapter_id: str, page_index: int):
    zip_path = catalog.get_chapter_zip_path(chapter_id)
    if not zip_path:
        raise HTTPException(404, "chapter not found")
    # 실제로 이미지 데이터를 읽는 부분 - 리더가 스크롤하면서 계속 호출하는 가장 빈번한
    # 요청이다. 네트워크 드라이브의 회차를 읽을 때 이게 막히면 서비스 전체 체감 지연이
    # 제일 커서, 반드시 격리된 전용 스레드풀(run_platform_io)로 보내 로컬 회차 열람에는
    # 절대 영향이 없게 한다.
    series, _ = catalog.find_chapter_position(chapter_id)
    platform = series["platform"] if series else ""
    result = await run_platform_io(platform, _read_chapter_page_bytes, zip_path, page_index)
    if result is None:
        raise HTTPException(404, "page not found")
    data, media_type = result
    return Response(content=data, media_type=media_type)


# ---------------------------------------------------------------------------
# 정적 프론트엔드 (API 라우트 전부 등록된 다음 마지막에 마운트)
# ---------------------------------------------------------------------------

STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
