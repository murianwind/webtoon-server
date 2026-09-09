"""
라이브러리 스캔 관련 회귀 테스트: 1단계/중첩 폴더 인식, 제외/재포함, 경로 탐색 방어,
플랫폼 우선순위(SLOW_PLATFORMS)를 다룬다.
"""

import os

from conftest import make_chapter_zip


def test_scans_a_simple_one_level_series(client, library):
    """GIVEN 플랫폼 폴더 바로 아래에 시리즈 폴더가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))

    """WHEN 스캔하면"""
    client.post("/api/rescan")

    """THEN 그 시리즈가 목록에 나온다"""
    series = client.get("/api/series").json()
    assert [s["title"] for s in series] == ["테스트웹툰"]
    assert series[0]["platform"] == "naver"


def test_scans_series_nested_under_extra_folder_levels(client, library):
    """GIVEN 시리즈 폴더가 작가 폴더 등으로 한 단계 더 묶여 있을 때"""
    make_chapter_zip(str(library / "gdrive" / "01 작가" / "어떤작가" / "숨은시리즈" / "001.zip"))

    """WHEN 스캔하면"""
    client.post("/api/rescan")

    """THEN 중간 폴더 이름은 안 보이고, 실제 시리즈 폴더명만 제목으로 뜬다"""
    series = client.get("/api/series").json()
    assert [s["title"] for s in series] == ["숨은시리즈"]


def test_preserves_series_id_for_shallow_series_across_rescans(client, library):
    """GIVEN 1단계 시리즈를 스캔해서 ID를 하나 얻었으면"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))
    client.post("/api/rescan")
    first_id = client.get("/api/series").json()[0]["id"]

    """WHEN 다시 스캔해도"""
    client.post("/api/rescan")

    """THEN 같은 시리즈는 항상 같은 ID를 유지한다(진행률 등이 안 끊기려면 필수)"""
    second_id = client.get("/api/series").json()[0]["id"]
    assert first_id == second_id


def test_excluded_series_disappears_from_listing_but_stays_in_folder_list(client, library):
    """GIVEN 스캔된 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]

    """WHEN 그 폴더를 제외하면"""
    r = client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    assert r.status_code == 200

    """THEN 목록에서는 빠지지만, 설정 패널의 제외된 폴더 목록에는 남아있다(다시 포함시킬 수 있어야 하므로)"""
    assert client.get("/api/series").json() == []
    folders = client.get("/api/series-folders").json()
    assert {"platform": "naver", "series": "웹툰A"} in folders["excluded"]
    assert folders["included"] == []


def test_included_series_reappears_without_full_rescan(client, library):
    """GIVEN 제외된 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    assert client.get("/api/series").json() == []

    """WHEN 재포함시키면(전체 재스캔 없이)"""
    r = client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})
    assert r.status_code == 200

    """THEN 그 시리즈가 다시 목록에 뜬다"""
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]


def test_path_traversal_in_include_is_blocked(client, library):
    """GIVEN 라이브러리 폴더 밖에 zip이 있는 폴더가 있을 때(공격 대상)"""
    outside_dir = library.parent / "outside_secret" / "무언가"
    make_chapter_zip(str(outside_dir / "001.zip"))
    assert os.path.isdir(str(outside_dir))

    """WHEN "../"가 섞인 series 값으로 재포함을 시도하면"""
    traversal_ref = f"../{os.path.basename(str(outside_dir.parent))}/무언가"
    r = client.post("/api/series-folders/include", json={"platform": "naver", "series": traversal_ref})
    assert r.status_code == 200  # 엔드포인트 자체는 에러 없이 응답하지만(기존 동작 유지)

    """THEN 라이브러리 밖의 폴더는 절대 시리즈로 등록되지 않는다"""
    assert client.get("/api/series").json() == []


def test_local_platforms_are_scanned_before_slow_platforms(client, library, monkeypatch):
    """GIVEN 로컬 플랫폼과 네트워크(SLOW_PLATFORMS로 지정) 플랫폼이 섞여 있을 때"""
    make_chapter_zip(str(library / "zzz_local" / "로컬웹툰" / "001.zip"))
    make_chapter_zip(str(library / "aaa_slow" / "느린웹툰" / "001.zip"))
    monkeypatch.setenv("SLOW_PLATFORMS", "aaa_slow")

    """WHEN 스캔 순서를 뽑아보면"""
    from app import scan
    order = scan.list_platforms_in_scan_order()

    """THEN 이름순으로는 aaa_slow가 앞이어야 하지만, SLOW_PLATFORMS 지정 때문에 뒤로 밀린다"""
    assert order == ["zzz_local", "aaa_slow"]
