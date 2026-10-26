"""
다음 화 미리 데우기(워밍) + 겹침 계산 동시 실행 제한 회귀 테스트.

약속:
  1) 회차의 페이지 수를 조회하면, 같은 시리즈의 다음 회차 zip을 서버가 백그라운드로 미리 열어
     (목록 캐시 + 첫 페이지 읽기) rclone 캐시까지 데워둔다. 같은 회차는 한 번만 데운다.
  2) 마지막 회차이거나 데우기에 실패해도 페이지 수 응답에는 아무 영향이 없다.
  3) 겹침 계산이 캐시에 없어서 직접 계산해야 할 때는 한 번에 하나씩만 돌려, 사용자의 페이지
     읽기가 쓸 스레드까지 다 점유하지 않게 한다.
"""

import asyncio
import threading
import time
import zipfile

import httpx
import pytest

from app import db, overlap, scan, warmup
from app.main import app
from conftest import make_chapter_zip


@pytest.fixture
def three_chapters(client, library):
    """회차 3개(001~003)짜리 시리즈를 스캔해 두고 (회차 id 목록, zip 경로 목록)을 돌려준다.
    재스캔 직후 백그라운드 겹침 사전계산이 테스트에 끼어들지 않도록 꺼 둔다."""
    db.set_setting("overlap_precompute_enabled", "false")
    warmup.reset()
    folder = library / "naver" / "워밍테스트"
    for n in (1, 2, 3):
        make_chapter_zip(str(folder / f"{n:03d} {n}화.zip"), page_count=3)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    ids = [c["id"] for c in chapters]
    paths = [str(folder / f"{n:03d} {n}화.zip") for n in (1, 2, 3)]
    return ids, paths


@pytest.fixture
def open_counter(monkeypatch):
    opened: list[str] = []
    real = zipfile.ZipFile

    class CountingZipFile(real):
        def __init__(self, file, *args, **kwargs):
            opened.append(str(file))
            super().__init__(file, *args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", CountingZipFile)
    return opened


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_requesting_pages_warms_the_next_chapter_once(client, three_chapters, open_counter):
    """GIVEN 회차 3개짜리 시리즈에서 1화의 페이지 수를 조회하면"""
    ids, paths = three_chapters
    scan.clear_zip_names_cache()
    open_counter.clear()
    assert client.get(f"/api/chapters/{ids[0]}/pages").json()["page_count"] == 3

    """WHEN 잠시 뒤"""
    warmed = _wait_until(lambda: open_counter.count(paths[1]) >= 1)
    _wait_until(lambda: not warmup._warm_running.locked())  # 데우기가 끝나 목록이 캐시에 들어갈 때까지

    """THEN 2화 zip이 백그라운드로 열려 있고(목록 캐시 + 첫 페이지), 3화는 건드리지 않는다"""
    assert warmed
    assert open_counter.count(paths[2]) == 0
    open_counter.clear()
    assert len(scan.list_zip_image_names(paths[1])) == 3
    assert open_counter.count(paths[1]) == 0  # 목록은 이미 캐시에 있어 zip을 다시 열지 않는다

    """그리고 같은 회차를 다시 조회해도 2화를 또 데우지 않는다"""
    client.get(f"/api/chapters/{ids[0]}/pages")
    time.sleep(0.3)
    assert open_counter.count(paths[1]) == 0


def test_last_chapter_has_nothing_to_warm(client, three_chapters, open_counter):
    """GIVEN 마지막(3화) 회차의 페이지 수를 조회할 때 THEN 정상 응답하고 데울 다음 회차가 없어 2화 zip도 열지 않는다
    (1화 zip은 재스캔 직후 백그라운드 표지 생성이 열 수 있어 검사 대상에서 뺀다)"""
    ids, paths = three_chapters
    scan.clear_zip_names_cache()
    open_counter.clear()
    r = client.get(f"/api/chapters/{ids[2]}/pages")
    time.sleep(0.3)
    assert r.status_code == 200 and r.json()["page_count"] == 3
    assert open_counter.count(paths[1]) == 0


def test_warmup_failure_does_not_affect_page_count_response(client, three_chapters, monkeypatch):
    """GIVEN 다음 화 데우기가 예외를 던지는 상황에서 WHEN 1화 페이지 수를 조회하면 THEN 응답은 정상"""
    ids, _ = three_chapters

    def boom(path):
        raise OSError("원격 읽기 실패")

    monkeypatch.setattr(warmup, "warm_chapter_zip", boom)
    r = client.get(f"/api/chapters/{ids[0]}/pages")
    assert r.status_code == 200 and r.json()["page_count"] == 3
    time.sleep(0.2)


def test_only_one_warmup_runs_at_a_time(three_chapters, monkeypatch):
    """GIVEN 다른 회차를 데우는 중에 WHEN 또 데우기를 요청하면 THEN 기다리며 스레드를 붙잡지 않고 건너뛴다"""
    _, paths = three_chapters
    started, release = threading.Event(), threading.Event()
    real_read = warmup._touch_first_page

    def slow_touch(path):
        started.set()
        release.wait(2)
        return real_read(path)

    monkeypatch.setattr(warmup, "_touch_first_page", slow_touch)
    worker = threading.Thread(target=warmup.warm_chapter_zip, args=(paths[1],))
    worker.start()
    assert started.wait(2)

    assert warmup.warm_chapter_zip(paths[2]) is False  # 이미 데우는 중이라 즉시 건너뜀(블로킹 없음)

    release.set()
    worker.join(3)
    assert warmup.warm_chapter_zip(paths[2]) is True


def test_uncached_overlap_computations_run_one_at_a_time(client, three_chapters, monkeypatch):
    """GIVEN 겹침 캐시가 없는 회차(2화, 3화)가 여러 개 있을 때"""
    ids, _ = three_chapters
    running = 0
    peak = 0
    lock = threading.Lock()

    def slow_compute(prev_path, next_path):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.15)
        with lock:
            running -= 1
        return 0

    monkeypatch.setattr(overlap, "compute_overlap_pages", slow_compute)

    """WHEN 동시에 겹침을 요청하면"""
    async def fire():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await asyncio.gather(
                c.get(f"/api/chapters/{ids[1]}/overlap"),
                c.get(f"/api/chapters/{ids[2]}/overlap"),
                c.get(f"/api/chapters/{ids[1]}/overlap"),
            )

    responses = asyncio.run(fire())

    """THEN 모두 정상 응답하고, 계산은 동시에 둘 이상 돌지 않으며, 같은 회차는 한 번만 계산한다"""
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert [r.json()["skip_pages"] for r in responses] == [0, 0, 0]
    assert peak == 1
