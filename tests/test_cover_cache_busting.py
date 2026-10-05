"""
커버(썸네일)가 바뀌면 브라우저가 옛 이미지를 계속 쓰지 않고 새로 받아오는지 확인하는
회귀 테스트. 커버 응답은 브라우저에 7일 캐싱되는데(Cache-Control: max-age=604800), 커버
주소가 항상 똑같으면 서버가 새 커버를 만들어도 브라우저는 요청 자체를 안 해서, 새로고침해도
옛 커버가 그대로 보였다. 그래서 커버가 바뀌면 주소(?v=)가 같이 바뀌어야 한다.
"""

import io
import os
import zipfile

from PIL import Image

from app import covers
from conftest import make_chapter_zip


def _cover_url(client):
    return client.get("/api/series").json()[0]["cover_url"]


def _write_cover_jpg(folder):
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), (200, 30, 30)).save(buf, format="JPEG")
    path = folder / "cover.jpg"
    path.write_bytes(buf.getvalue())
    return path


def test_cover_url_stays_the_same_while_nothing_changes(client, library):
    """GIVEN 커버를 한 번도 안 바꾼 웹툰"""
    make_chapter_zip(str(library / "naver" / "웹툰" / "001.zip"))
    client.post("/api/rescan")
    first = _cover_url(client)

    """WHEN 재스캔을 해도"""
    client.post("/api/rescan")

    """THEN 주소가 그대로라서, 브라우저의 7일 캐시가 계속 유효하다(성능 유지)"""
    assert _cover_url(client) == first


def test_cover_url_changes_when_a_cover_file_is_added(client, library):
    """GIVEN 커버 파일 없이 첫 페이지로 만든 커버를 쓰던 웹툰"""
    series_dir = library / "naver" / "웹툰"
    make_chapter_zip(str(series_dir / "001.zip"))
    client.post("/api/rescan")
    before = _cover_url(client)

    """WHEN cover.jpg를 추가하고 재스캔하면"""
    _write_cover_jpg(series_dir)
    client.post("/api/rescan")
    after = _cover_url(client)

    """THEN 같은 경로에 버전만 바뀐 새 주소가 되어, 브라우저가 새 커버를 받아간다"""
    assert after != before
    assert after.split("?")[0] == before.split("?")[0]


def test_cover_url_changes_even_if_cover_has_same_mtime_as_first_chapter(client, library):
    """GIVEN 새 cover.jpg의 수정시각이 첫 회차 zip과 우연히 똑같은 경우(복사 도구가
    수정시각을 보존할 때 생김)"""
    series_dir = library / "naver" / "웹툰"
    chapter = series_dir / "001.zip"
    make_chapter_zip(str(chapter))
    client.post("/api/rescan")
    before = _cover_url(client)

    cover = _write_cover_jpg(series_dir)
    mtime = os.path.getmtime(chapter)
    os.utime(cover, (mtime, mtime))
    client.post("/api/rescan")

    """THEN 수정시각만으로는 구분이 안 돼도, 커버 원본이 바뀐 것은 주소에 반영된다"""
    assert _cover_url(client) != before


def test_successful_cover_is_cached_by_the_browser_for_a_week(client, library):
    """GIVEN 커버가 정상적으로 만들어지는 웹툰"""
    make_chapter_zip(str(library / "naver" / "웹툰" / "001.zip"))
    client.post("/api/rescan")

    """WHEN 커버를 받으면"""
    response = client.get(_cover_url(client))

    """THEN 7일 캐시 헤더가 그대로 붙는다(주소가 바뀌지 않는 한 다시 받지 않음)"""
    assert response.status_code == 200
    assert "max-age=604800" in response.headers["cache-control"]


def test_missing_cover_error_is_never_cached_by_the_browser(client, library):
    """GIVEN 커버로 쓸 이미지가 하나도 없는 웹툰(이미지 없는 빈 zip)"""
    empty_zip = library / "naver" / "웹툰" / "001.zip"
    empty_zip.parent.mkdir(parents=True)
    zipfile.ZipFile(empty_zip, "w").close()
    client.post("/api/rescan")

    """WHEN 커버를 요청하면"""
    response = client.get(_cover_url(client))

    """THEN 404이고, 에러 응답은 캐싱하지 않는다(나중에 이미지가 생겨도 7일간 안 보이면 안 됨)"""
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_cover_url_helper_keeps_profile_prefix():
    """프로필 화면의 커버 주소는 /p/<토큰> 접두사가 붙은 채로 버전도 같이 붙는다"""
    series = {"id": "abc123", "cover_path": None, "cover_mtime": 1.0}
    url = covers.cover_url(series, "/p/토큰")
    assert url.startswith("/p/토큰/api/series/abc123/cover?v=")
