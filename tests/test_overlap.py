"""
회차 겹침(리캡) 감지 회귀 테스트. 실제 서로 다른 세션에서 압축된 것처럼 매칭 점수가
페이지마다 들쭉날쭉해도, "위치가 순차적으로 이어지는지"로 정확히 판단해야 한다.

테스트 이미지는 일부러 단색이 아니라 그라디언트+도형을 넣어서 진짜 사진/일러스트에
가까운 텍스처를 갖게 한다 - OpenCV의 정규화 교차상관(TM_CCOEFF_NORMED)은 완전한
단색 이미지에서는 분산이 0이 되어 결과가 정의되지 않으므로, 순수 단색으로는 이
알고리즘이 원래 의도한 동작을 검증할 수 없다.
"""

import io
import os
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from app import overlap


def _make_page(width, height, seed):
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        arr[y, :, 0] = (seed * 7 + y // 3) % 255
        arr[y, :, 1] = (seed * 13 + y // 5) % 255
    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse([50, 50 + (seed % 5) * 20, 150, 150 + (seed % 5) * 20], fill=(seed * 30 % 255, 0, 0))
    draw.rectangle([200, 300, 350, 450], fill=(0, seed * 40 % 255, 0))
    return img


def _zip_pages(path, pages):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i, img in enumerate(pages):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            zf.writestr(f"{i + 1:04d}.jpg", buf.getvalue())


def test_no_overlap_when_chapters_are_unrelated(tmp_path):
    """GIVEN 서로 완전히 무관한 두 회차가 있을 때"""
    prev = tmp_path / "prev.zip"
    next_ = tmp_path / "next.zip"
    _zip_pages(str(prev), [_make_page(400, 600, seed=i) for i in range(5)])
    _zip_pages(str(next_), [_make_page(400, 600, seed=i + 100) for i in range(5)])

    """WHEN 겹침을 계산하면"""
    result = overlap.compute_overlap_pages(str(prev), str(next_))

    """THEN 0페이지로 나온다"""
    assert result == 0


def test_detects_overlap_even_with_noisy_per_page_scores(tmp_path):
    """GIVEN 다음 화 맨 앞 3페이지가 이전 화 끝 3페이지와 정확히 같은 순서로 이어지는 경우"""
    shared_pages = [_make_page(400, 600, seed=i + 1) for i in range(3)]
    prev_only = [_make_page(400, 600, seed=90)]
    next_only = [_make_page(400, 600, seed=91), _make_page(400, 600, seed=92)]

    prev = tmp_path / "prev.zip"
    next_ = tmp_path / "next.zip"
    _zip_pages(str(prev), prev_only + shared_pages)
    _zip_pages(str(next_), shared_pages + next_only)

    """WHEN 겹침을 계산하면"""
    result = overlap.compute_overlap_pages(str(prev), str(next_))

    """THEN 겹치는 3페이지가 정확히 감지된다"""
    assert result == 3


def test_overlap_stops_at_the_point_sequence_breaks(tmp_path):
    """GIVEN 앞의 2페이지만 진짜로 이어지고, 그 다음은 무관한 내용일 때"""
    shared_pages = [_make_page(400, 600, seed=1), _make_page(400, 600, seed=2)]
    unrelated_after = [_make_page(400, 600, seed=i + 200) for i in range(3)]

    prev = tmp_path / "prev.zip"
    next_ = tmp_path / "next.zip"
    _zip_pages(str(prev), [_make_page(400, 600, seed=99)] + shared_pages)
    _zip_pages(str(next_), shared_pages + unrelated_after)

    """WHEN 겹침을 계산하면"""
    result = overlap.compute_overlap_pages(str(prev), str(next_))

    """THEN 정확히 2페이지에서 멈춘다(그 뒤 무관한 내용까지 잘못 포함하지 않음)"""
    assert result == 2


def test_precompute_overlaps_collects_garbage_periodically(library, monkeypatch):
    """GIVEN 배치 크기를 일부러 작게(3건마다) 줄여두고, 겹침이 캐싱 안 된 회차 전환이
    7건(=2번은 꽉 채운 배치, 1번은 덜 채운 배치) 있을 때"""
    import asyncio as _asyncio
    from unittest.mock import patch

    from conftest import make_chapter_zip

    monkeypatch.setattr(overlap, "_GC_BATCH_SIZE", 3)

    for i in range(8):  # 8개 회차 = 회차 전환 7건
        make_chapter_zip(str(library / "naver" / "긴웹툰" / f"{i:03d}.zip"))

    from app.main import app as _app
    from fastapi.testclient import TestClient

    with TestClient(_app) as client:
        client.post("/api/rescan")

    gc_calls = []
    with patch("app.overlap.gc.collect", side_effect=lambda: gc_calls.append(1)):
        _asyncio.run(overlap.precompute_overlaps())

    """THEN 정확히 7건을 3건씩 나눠 처리했을 때 나오는 횟수(2번, 3의 배수 지점마다)만큼
    가비지 컬렉션이 호출된다 - 오래 걸리는 계산일수록 주기적으로 메모리를 정리해야
    네이티브 라이브러리 쪽 누적 위험이 줄어든다"""
    assert len(gc_calls) == 2  # computed가 3, 6일 때 호출됨(7건 중)


def test_precompute_overlaps_stops_midway_when_setting_turned_off(library, monkeypatch):
    """GIVEN 설정 확인 배치 크기를 작게(2건마다) 줄여두고, 처리 대상이 5건 있을 때"""
    import asyncio as _asyncio

    from app import db
    from conftest import make_chapter_zip
    from fastapi.testclient import TestClient

    monkeypatch.setattr(overlap, "_SETTING_CHECK_BATCH_SIZE", 2)

    for i in range(6):  # 6개 회차 = 회차 전환 5건
        make_chapter_zip(str(library / "naver" / "중단테스트" / f"{i:03d}.zip"))

    from app.main import app as _app

    with TestClient(_app) as client:
        client.post("/api/rescan")

    processed_so_far = {"n": 0}
    original_compute = overlap.compute_overlap_pages

    def fake_compute(prev_path, next_path):
        processed_so_far["n"] += 1
        if processed_so_far["n"] == 2:
            # 2건째가 끝난 시점에 설정을 꺼서, 그다음 확인 시점에 멈추는지 본다
            db.set_setting("overlap_precompute_enabled", "false")
        return original_compute(prev_path, next_path)

    """WHEN 도중에(2건 처리 후) 설정이 꺼지면"""
    with monkeypatch.context() as m:
        m.setattr(overlap, "compute_overlap_pages", fake_compute)
        _asyncio.run(overlap.precompute_overlaps())

    """THEN 5건을 다 처리하지 않고 중간에 멈춘다(꺼진 걸 감지한 시점 이후로는 처리 안 함)"""
    assert processed_so_far["n"] < 5


def test_lock_released_and_remaining_items_finish_after_midway_stop(library, monkeypatch):
    """GIVEN 도중에 설정이 꺼져서 5건 중 일부만 처리되고 멈췄을 때"""
    import asyncio as _asyncio

    from app import db
    from conftest import make_chapter_zip
    from fastapi.testclient import TestClient

    monkeypatch.setattr(overlap, "_SETTING_CHECK_BATCH_SIZE", 2)

    for i in range(6):
        make_chapter_zip(str(library / "naver" / "재개테스트" / f"{i:03d}.zip"))

    from app.main import app as _app

    with TestClient(_app) as client:
        client.post("/api/rescan")

    processed = {"n": 0}
    original_compute = overlap.compute_overlap_pages

    def fake_compute(prev_path, next_path):
        processed["n"] += 1
        if processed["n"] == 2:
            db.set_setting("overlap_precompute_enabled", "false")
        return original_compute(prev_path, next_path)

    with monkeypatch.context() as m:
        m.setattr(overlap, "compute_overlap_pages", fake_compute)
        _asyncio.run(overlap.precompute_overlaps())
    assert processed["n"] < 5  # 중간에 멈췄음을 재확인

    """THEN 멈춘 직후 락이 풀려있어야 한다(다음 재스캔이 영원히 막히면 안 됨)"""
    assert overlap._precompute_lock.locked() is False

    """AND 설정을 다시 켜고 재실행하면, 아직 못 한 나머지가 이어서 전부 처리된다
    (중간에 멈춘 게 데이터를 잃어버리거나 다음 실행을 막는 게 아니라는 뜻)"""
    db.set_setting("overlap_precompute_enabled", "true")
    _asyncio.run(overlap.precompute_overlaps())

    from app import catalog
    series = list(catalog.get_series_map().values())[0]
    chapters = series["chapters"]
    for i in range(1, len(chapters)):
        assert db.get_cached_overlap(chapters[i]["id"]) is not None


def test_off_setting_does_not_block_cover_precompute_in_same_generation(library, monkeypatch):
    """GIVEN 겹침 사전계산이 꺼져있을 때(재스캔 중이라 이번 세대에서 건너뛰어짐)"""
    from unittest.mock import patch

    from app import db, services

    db.set_setting("overlap_precompute_enabled", "false")
    covers_called = []

    async def fake_covers():
        covers_called.append(1)

    """WHEN 스캔 후 사전계산을 실행하면"""
    with patch("app.services.precompute_covers", fake_covers):
        import asyncio as _asyncio
        _asyncio.run(services._precompute_after_scan())

    """THEN 겹침은 건너뛰지만 커버 생성은 그대로 실행된다 - 이 설정은 겹침 계산만
    끄는 것이지, 화면에 커버가 아예 안 보이게 만드는 게 아니다"""
    assert covers_called == [1]

    """AND 전역 락도 정상적으로 풀려서 다음 세대가 막히지 않는다"""
    assert services._global_precompute_lock.locked() is False


def test_realtime_endpoint_works_right_after_a_midway_stop(client, library, monkeypatch):
    """GIVEN 겹침 사전계산이 도중에 멈춰서 일부 회차 전환은 아직 캐시가 없을 때"""
    from conftest import make_chapter_zip

    monkeypatch.setattr(overlap, "_SETTING_CHECK_BATCH_SIZE", 1)
    from app import db

    for i in range(4):
        make_chapter_zip(str(library / "naver" / "실시간재확인" / f"{i:03d}.zip"))
    client.post("/api/rescan")
    db.set_setting("overlap_precompute_enabled", "false")

    import asyncio as _asyncio
    _asyncio.run(overlap.precompute_overlaps())  # 설정이 꺼져있어도 직접 호출하면 도는지와
    # 무관하게, 아래에서 확인할 건 "그래도 실시간 API는 항상 정상 동작한다"는 것

    series_id = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{series_id}/chapters").json()["chapters"]

    """WHEN 아직 캐시가 없을 수 있는 회차 전환의 겹침 정보를 실시간 API로 조회하면"""
    for ch in chapters[1:]:
        r = client.get(f"/api/chapters/{ch['id']}/overlap")
        """THEN 사전계산 설정 상태와 무관하게 전부 정상 응답한다"""
        assert r.status_code == 200
        assert "skip_pages" in r.json()
