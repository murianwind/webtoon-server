"""
다음 화 미리 데우기(워밍). 네트워크 드라이브(rclone)는 한동안 안 읽은 파일이 캐시에서 밀려나서,
자리를 비웠다 돌아와 다음 화를 열면 그 zip을 원격에서 다시 받아오느라 한참 걸린다. 사용자가
지금 회차를 열 때 다음 회차 zip을 서버가 먼저 한 번 읽어두면(목록 캐시 + 첫 페이지 읽기) 그 파일이
rclone 캐시에 올라와서, 다음 화로 넘어갈 때는 이미 따뜻한 상태가 된다.

원칙: 어디까지나 덤이다. 실패해도 조용히 넘어가고, 사용자의 페이지 읽기를 밀어내지 않도록
동시에 하나만 돌리며(이미 데우는 중이면 기다리지 않고 건너뜀), 같은 회차는 한 번만 데운다.
"""

import asyncio
import logging
import threading
import zipfile
from collections import OrderedDict

from . import scan, services

log = logging.getLogger("webtoon-server")

WARMED_MEMORY_SIZE = 256  # 이미 데운 회차를 기억하는 개수(오래된 것부터 잊음 - 다시 데워도 해롭지 않다)

_warmed: "OrderedDict[str, None]" = OrderedDict()
_warmed_lock = threading.Lock()
_warm_running = threading.Lock()  # 데우는 작업은 동시에 하나만(기다리지 않고 건너뛴다)
_background_tasks: set[asyncio.Task] = set()  # 실행 중인 태스크가 가비지 컬렉션으로 사라지지 않게 붙잡아 둔다


def reset() -> None:
    with _warmed_lock:
        _warmed.clear()


def _claim(zip_path: str) -> bool:
    """처음 데우는 회차면 기억해 두고 True, 이미 데웠거나 데우는 중이면 False."""
    with _warmed_lock:
        if zip_path in _warmed:
            return False
        _warmed[zip_path] = None
        while len(_warmed) > WARMED_MEMORY_SIZE:
            _warmed.popitem(last=False)
        return True


def _unclaim(zip_path: str) -> None:
    with _warmed_lock:
        _warmed.pop(zip_path, None)


def _touch_first_page(zip_path: str) -> None:
    """zip을 한 번 열어 목록을 캐시에 넣고 첫 페이지를 읽는다(원격 마운트의 캐시를 데우는 것이 목적)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = scan.list_zip_image_names(zip_path, zf)
        if names:
            zf.read(names[0])


def warm_chapter_zip(zip_path: str) -> bool:
    """스레드에서 실행된다. 데웠으면 True, 다른 회차를 데우는 중이라 건너뛰었으면 False."""
    if not _warm_running.acquire(blocking=False):
        return False
    try:
        _touch_first_page(zip_path)
        return True
    finally:
        _warm_running.release()


async def _run(platform: str, zip_path: str) -> None:
    warmed = False
    try:
        warmed = await services.run_platform_io(platform, warm_chapter_zip, zip_path)
    except Exception as e:
        log.warning(f"다음 화 미리 읽기 실패 (무시하고 계속): {zip_path}: {e}")
    if not warmed:
        _unclaim(zip_path)  # 건너뛰었거나 실패했으면 다음 기회에 다시 시도할 수 있게 한다


def schedule_next_chapter_warmup(series: dict | None, index: int | None) -> None:
    """이 회차(series["chapters"][index])의 다음 회차 zip을 백그라운드로 데운다. 호출은 즉시 돌아온다."""
    if series is None or index is None:
        return
    chapters = series["chapters"]
    if index + 1 >= len(chapters):
        return
    next_path = chapters[index + 1]["path"]
    if not _claim(next_path):
        return
    task = asyncio.create_task(_run(series["platform"], next_path))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
