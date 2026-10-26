"""
회차 zip 열기 횟수 줄이기 회귀 테스트 - 네트워크 드라이브(rclone)에서는 zip을 한 번 여는 것도
원격 읽기라서, 페이지 한 장을 읽을 때마다 zip을 두 번 열고 목록을 매번 다시 만들면 유휴 뒤
첫 회차 열기가 매우 느려진다.

약속:
  1) 같은 zip의 이미지 목록은 파일이 바뀌지 않는 한 다시 zip을 열지 않고 돌려준다.
  2) 파일이 바뀌면(크기/수정시각) 목록을 다시 읽는다.
  3) 페이지 한 장을 읽을 때 zip은 딱 한 번만 연다(캐시가 비어 있어도).
"""

import os
import zipfile

import pytest

from app import scan, services
from conftest import make_chapter_zip


@pytest.fixture
def open_counter(monkeypatch):
    """scan/services가 쓰는 zipfile.ZipFile 생성 횟수를 경로별로 센다."""
    opened: list[str] = []
    real = zipfile.ZipFile

    class CountingZipFile(real):
        def __init__(self, file, *args, **kwargs):
            opened.append(str(file))
            super().__init__(file, *args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", CountingZipFile)
    scan.clear_zip_names_cache()
    return opened


def test_listing_same_zip_twice_opens_it_once(tmp_path, open_counter):
    """GIVEN 3페이지짜리 회차 zip이 있을 때"""
    path = str(tmp_path / "001.zip")
    make_chapter_zip(path, page_count=3)
    open_counter.clear()

    """WHEN 이미지 목록을 두 번 요청하면"""
    first = scan.list_zip_image_names(path)
    second = scan.list_zip_image_names(path)

    """THEN 같은 결과를 주되 zip은 한 번만 열린다"""
    assert first == second == ["0001.jpg", "0002.jpg", "0003.jpg"]
    assert open_counter.count(path) == 1


def test_listing_is_refreshed_when_zip_changes(tmp_path, open_counter):
    """GIVEN 목록이 캐시된 zip이 있고"""
    path = str(tmp_path / "001.zip")
    make_chapter_zip(path, page_count=2)
    assert len(scan.list_zip_image_names(path)) == 2

    """WHEN 같은 경로의 zip이 5페이지짜리로 교체되면(크기/수정시각이 달라짐)"""
    make_chapter_zip(path, page_count=5)
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    """THEN 오래된 캐시가 아니라 새 목록이 나온다"""
    assert len(scan.list_zip_image_names(path)) == 5


def test_listing_keeps_natural_order_and_skips_non_images(tmp_path, open_counter):
    """GIVEN 이미지가 아닌 파일과 폴더 항목, 10 이상 번호가 섞인 zip이 있을 때"""
    path = str(tmp_path / "001.zip")
    with zipfile.ZipFile(path, "w") as zf:
        for name in ("10.jpg", "2.jpg", "1.jpg", "readme.txt", "sub/"):
            zf.writestr(name, b"x")

    """WHEN 목록을 두 번(두 번째는 캐시에서) 요청하면"""
    first = scan.list_zip_image_names(path)
    second = scan.list_zip_image_names(path)

    """THEN 자연 정렬(1,2,10)이고 이미지만 남는다"""
    assert first == second == ["1.jpg", "2.jpg", "10.jpg"]


def test_reading_a_page_opens_zip_once_even_when_cache_is_cold(tmp_path, open_counter):
    """GIVEN 목록이 아직 캐시되지 않은 zip이 있을 때"""
    path = str(tmp_path / "001.zip")
    make_chapter_zip(path, page_count=3)
    open_counter.clear()

    """WHEN 페이지 한 장을 읽으면"""
    result = services.read_chapter_page_bytes(path, 1)

    """THEN zip은 한 번만 열리고(예전에는 목록용 + 읽기용으로 두 번) 올바른 페이지가 나온다"""
    assert result is not None
    data, media_type = result
    assert media_type == "image/jpeg" and data[:2] == b"\xff\xd8"
    assert open_counter.count(path) == 1


def test_reading_a_page_when_cache_is_warm_also_opens_zip_once(tmp_path, open_counter):
    """GIVEN 목록이 이미 캐시된 zip이 있을 때"""
    path = str(tmp_path / "001.zip")
    make_chapter_zip(path, page_count=3)
    scan.list_zip_image_names(path)
    open_counter.clear()

    """WHEN 페이지를 두 장 읽으면"""
    services.read_chapter_page_bytes(path, 0)
    services.read_chapter_page_bytes(path, 2)

    """THEN 한 장당 zip을 한 번씩만 연다(데이터를 읽으려면 열어야 하므로)"""
    assert open_counter.count(path) == 2


def test_reading_page_out_of_range_returns_none(tmp_path, open_counter):
    """GIVEN 3페이지짜리 zip에서 WHEN 범위를 벗어난 페이지를 읽으면 THEN None"""
    path = str(tmp_path / "001.zip")
    make_chapter_zip(path, page_count=3)
    assert services.read_chapter_page_bytes(path, 3) is None
    assert services.read_chapter_page_bytes(path, -1) is None
