"""
여러 라우터(app/routers/*.py)가 공유하는 백그라운드 작업/헬퍼 로직을 모아둔 모듈.
라우트 정의는 여기 없다 - "여러 도메인에서 같이 쓰는 것"만 담당하는 게 이 파일의 유일한
책임이다(라우트별 세부 동작은 각 라우터 파일에 있음).

이름 앞에 밑줄이 없는 함수/상수는 다른 모듈에서도 쓰는 공개 API, 밑줄이 붙은 건 이
파일 안에서만 쓰는 내부 구현이다.
"""

import asyncio
import concurrent.futures
import logging
import os

from . import catalog, covers, db, overlap, scan

log = logging.getLogger("webtoon-server")

# 외부(디스코드 등)에 공개되는 URL을 만들 때 쓰는 기준 주소. 예: https://your-domain.example.com
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# 라이브러리 자동 재스캔 주기(초). 기본 2시간. 0 이하로 설정하면 자동 재스캔을 끈다.
RESCAN_INTERVAL_SECONDS = int(os.environ.get("RESCAN_INTERVAL_SECONDS", "7200"))

BACKUP_VERSION = 2  # v2부터 read_chapters(회차별 명시 읽음 기록) 포함

SERIES_SCAN_TIMEOUT_SECONDS = int(os.environ.get("SERIES_SCAN_TIMEOUT_SECONDS", "30"))

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


def chapter_number_part(label: str) -> str:
    """라벨에서 제목 부분(' · ' 뒤)을 떼고 회차 번호 부분만 반환."""
    return label.split(" · ", 1)[0]


def resolve_read_index(chapters: list, prog: dict | None) -> int:
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


def migrate_legacy_progress_if_needed(series_id: str, chapters: list, prog: dict | None) -> None:
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
    idx = resolve_read_index(chapters, prog)
    if idx < 0:
        return
    if prog["page_index"] >= db.PAGE_FINISHED_SENTINEL:
        ids_to_mark = [chapter["id"] for chapter in chapters[: idx + 1]]
    else:
        ids_to_mark = [chapter["id"] for chapter in chapters[:idx]]
    db.mark_chapters_read(series_id, ids_to_mark)


def log_scan_result(prefix: str, series_map: dict, chapters_map: dict, added: int | None = None, removed: int | None = None) -> None:
    if added is None:
        log.info(f"{prefix} - 시리즈 {len(series_map)}개, 회차 {len(chapters_map)}개")
    elif added or removed:
        log.info(f"{prefix} - 시리즈 {len(series_map)}개 (신규 {added}, 제거 {removed}), 회차 {len(chapters_map)}개")
    else:
        log.info(f"{prefix} - 변경 없음 (시리즈 {len(series_map)}개, 회차 {len(chapters_map)}개)")


def _advance_generator(gen):
    """제너레이터를 한 칸 진행시켜서 다음 값을 반환하거나, 끝났으면 None을 반환한다.
    스레드에서 실행하기 위한 평범한 동기 함수(제너레이터 자체는 동기 코드이므로)."""
    return next(gen, None)


async def scan_all_platforms_incrementally() -> tuple[dict, dict]:
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


async def initial_scan() -> None:
    """서버 시작 시 백그라운드로 실행되는 첫 스캔."""
    log.info(f"라이브러리 스캔 시작 (경로: {scan.LIBRARY_ROOT}) - 백그라운드로 진행, 서버는 이미 요청을 받고 있음")
    try:
        series_map, chapters_map = await scan_all_platforms_incrementally()
        log_scan_result(f"라이브러리 스캔 완료 (경로: {scan.LIBRARY_ROOT})", series_map, chapters_map)
        asyncio.create_task(overlap.precompute_overlaps())
        asyncio.create_task(precompute_covers())
    except Exception:
        log.exception("초기 스캔 중 오류 발생")


async def auto_rescan_loop() -> None:
    """RESCAN_INTERVAL_SECONDS마다 반복되는 자동 재스캔 루프."""
    while True:
        await asyncio.sleep(RESCAN_INTERVAL_SECONDS)
        try:
            old_ids = set(catalog.get_series_map().keys())
            series_map, chapters_map = await scan_all_platforms_incrementally()
            added = len(set(series_map.keys()) - old_ids)
            removed = len(old_ids - set(series_map.keys()))
            log_scan_result("자동 재스캔 완료", series_map, chapters_map, added, removed)
            asyncio.create_task(overlap.precompute_overlaps())
            asyncio.create_task(precompute_covers())
        except Exception:
            # 한 번 실패해도 다음 주기에 다시 시도 - 서버가 죽으면 안 됨
            log.exception("자동 재스캔 중 오류 발생 - 다음 주기에 재시도")


async def ensure_cover_cached(series: dict) -> tuple[bytes, str] | None:
    """
    이 시리즈의 커버가 캐시에 없으면 지금 만들어서 캐시해두고, 결과를 반환한다
    (실패하거나 커버로 쓸 이미지가 아예 없으면 None). 실제 커버 응답 라우트와
    스캔 후 사전계산 작업이 이 로직을 그대로 공유해서 중복을 없앤다.
    """
    series_id = series["id"]
    platform = series["platform"]

    cover_path = series.get("cover_path")
    if cover_path and await run_platform_io(platform, os.path.isfile, cover_path):
        try:
            source_mtime = await run_platform_io(platform, os.path.getmtime, cover_path)
        except OSError:
            source_mtime = 0
        cached = covers.get_cached_cover(series_id, source_mtime)
        if cached:
            return cached
        return await run_platform_io(
            platform, covers.generate_and_cache_cover_from_file, series_id, source_mtime, cover_path
        )

    if not series["chapters"]:
        return None
    first_chapter = series["chapters"][0]
    names = await run_platform_io(platform, scan.list_zip_image_names, first_chapter["path"])
    if not names:
        return None

    try:
        source_mtime = await run_platform_io(platform, os.path.getmtime, first_chapter["path"])
    except OSError:
        source_mtime = 0

    cached = covers.get_cached_cover(series_id, source_mtime)
    if cached:
        return cached
    return await run_platform_io(
        platform,
        covers.generate_and_cache_cover_from_zip,
        series_id, source_mtime, first_chapter["path"], names[0],
    )


_cover_precompute_lock = asyncio.Lock()


async def precompute_one_cover_with_timeout(series: dict) -> None:
    """폴더 재포함 등으로 시리즈 하나만 커버를 미리 만들 때 쓴다. create_task로 띄우는
    독립 작업이라 타임아웃 없이 멈춰버리면 그 스레드가 계속 남게 되므로 시간 제한을 둔다."""
    try:
        await asyncio.wait_for(ensure_cover_cached(series), timeout=SERIES_SCAN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning(f"커버 사전 생성이 {SERIES_SCAN_TIMEOUT_SECONDS}초를 넘겨 건너뜀: {series['id']} ({series['title']})")
    except Exception:
        log.exception(f"커버 사전 생성 실패: {series['id']}")


async def precompute_covers() -> None:
    """
    재스캔 직후 호출되는 백그라운드 작업. 모든 시리즈의 커버를 미리 만들어서 캐시해둔다.
    이렇게 미리 만들어두지 않으면, 첫 방문자가 목록 화면을 열 때(특히 컨테이너를 막
    띄워서 캐시가 텅 빈 상태일 때) 화면에 보이는 섬네일 수십 개가 한꺼번에
    "읽기+디코딩+리사이즈+압축" 작업을 동시에 요청하게 되어 첫 로딩이 유난히 느려진다.
    이미 캐시된 건 곧바로 건너뛰므로(ensure_cover_cached가 먼저 확인), 스캔마다
    반복 호출해도 새로 생긴/바뀐 것만 실제로 작업한다.
    """
    if _cover_precompute_lock.locked():
        return
    async with _cover_precompute_lock:
        series_list = list(catalog.get_series_map().values())
        if not series_list:
            return
        generated = 0
        for series in series_list:
            try:
                # 응답 없는 네트워크 파일 하나 때문에 이 작업 전체가 멈춰버리면 안 된다 -
                # 멈추면 이 lock을 영원히 붙잡고 있게 되어, 그 다음부터는 재스캔을 아무리
                # 해도 사전 생성 자체가 조용히 아무 일도 안 하게 되는 심각한 문제가 있었다.
                result = await asyncio.wait_for(
                    ensure_cover_cached(series), timeout=SERIES_SCAN_TIMEOUT_SECONDS
                )
                if result is not None:
                    generated += 1
            except asyncio.TimeoutError:
                log.warning(
                    f"커버 사전 생성이 {SERIES_SCAN_TIMEOUT_SECONDS}초를 넘겨 건너뜀: "
                    f"{series['id']} ({series['title']}) - 다음 재스캔에서 다시 시도됨"
                )
            except Exception:
                log.exception(f"커버 사전 생성 실패 (건너뛰고 계속): {series['id']}")
        log.info(f"커버 사전 생성 완료 - {generated}/{len(series_list)}건")


def read_chapter_page_bytes(zip_path: str, page_index: int) -> tuple[bytes, str] | None:
    """스레드에서 실행되는 부분: zip 목록 조회 + 실제 페이지 바이트 읽기를 한 번에 처리."""
    import zipfile

    names = scan.list_zip_image_names(zip_path)
    if page_index < 0 or page_index >= len(names):
        return None
    name = names[page_index]
    ext = os.path.splitext(name)[1].lower()
    with zipfile.ZipFile(zip_path) as zf:
        data = zf.read(name)
    return data, covers.IMAGE_MEDIA_TYPES.get(ext, "application/octet-stream")
