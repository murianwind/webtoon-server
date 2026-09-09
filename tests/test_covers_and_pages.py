"""
커버 생성/캐싱, 회차 페이지 서빙 관련 회귀 테스트.
"""

import time

from conftest import make_chapter_zip


def test_cover_is_generated_from_first_chapter_first_page(client, library):
    """GIVEN cover.jpg가 따로 없는 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]

    """WHEN 커버를 요청하면"""
    r = client.get(f"/api/series/{sid}/cover")

    """THEN 1화 첫 페이지로 만든 이미지가 응답된다"""
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_cover_is_precomputed_right_after_rescan(client, library):
    """GIVEN 새로 스캔된 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))
    client.post("/api/rescan")

    """WHEN 스캔 직후(사전생성이 끝날 시간을 잠깐 기다린 뒤) 서버 내부 캐시를 보면"""
    time.sleep(0.3)
    from app import covers
    sid = client.get("/api/series").json()[0]["id"]

    """THEN 사용자가 요청하기 전에 이미 캐시에 들어가 있다(첫 로딩 지연 방지)"""
    assert sid in covers._cache


def test_chapter_page_count_matches_actual_image_count(client, library):
    """GIVEN 5페이지짜리 회차가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"), page_count=5)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapter_id = client.get(f"/api/series/{sid}/chapters").json()["chapters"][0]["id"]

    """WHEN 페이지 수를 조회하면"""
    r = client.get(f"/api/chapters/{chapter_id}/pages")

    """THEN 정확히 5로 나온다"""
    assert r.json()["page_count"] == 5


def test_individual_page_is_served_as_image(client, library):
    """GIVEN 회차가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"), page_count=3)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapter_id = client.get(f"/api/series/{sid}/chapters").json()["chapters"][0]["id"]

    """WHEN 두 번째 페이지를 요청하면"""
    r = client.get(f"/api/chapters/{chapter_id}/pages/1")

    """THEN 이미지가 정상적으로 응답된다"""
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_cover_and_page_responses_are_cacheable_but_series_list_is_not(client, library):
    """GIVEN 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]

    """WHEN 커버와 목록 API의 캐시 헤더를 비교하면"""
    cover_resp = client.get(f"/api/series/{sid}/cover")
    list_resp = client.get("/api/series")

    """THEN 커버는 브라우저 캐싱이 허용되고, 목록은 매번 최신을 받아야 하므로 금지된다"""
    assert "max-age" in cover_resp.headers["cache-control"]
    assert list_resp.headers["cache-control"] == "no-store"
