"""
백업/복원, 앱 설정(app_settings) 관련 회귀 테스트.
"""

from conftest import make_chapter_zip


def test_backup_and_restore_roundtrip_preserves_progress(client, library):
    """GIVEN 진행률을 저장해둔 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "002 2화.zip"))
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[1]["id"], "page_index": 0})

    """WHEN 백업을 받아서 그대로 복원하면"""
    backup = client.get("/api/backup").json()
    r = client.post("/api/restore", json=backup)

    """THEN 복원이 성공하고, 진행률도 그대로 유지된다"""
    assert r.status_code == 200
    cont = client.get(f"/api/series/{sid}/continue").json()
    assert cont["chapter_id"] == chapters[1]["id"]


def test_backup_includes_excluded_folder_list(client, library):
    """GIVEN 제외해둔 폴더가 있을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})

    """WHEN 백업을 받으면"""
    backup = client.get("/api/backup").json()

    """THEN app_settings 안에 제외 목록 정보가 포함되어 있다"""
    keys = [item["key"] for item in backup["app_settings"]]
    assert "excluded_series" in keys


def test_settings_get_put_roundtrip(client, library):
    """GIVEN 아무 설정도 없을 때"""
    """WHEN 값을 저장하면"""
    r = client.put("/api/settings/status_filter", json={"value": "unread,reading"})
    assert r.status_code == 200

    """THEN 그대로 다시 조회된다"""
    got = client.get("/api/settings/status_filter").json()
    assert got["value"] == "unread,reading"


def test_platform_filter_setting_persists_across_requests(client, library):
    """GIVEN 플랫폼 필터를 저장했을 때"""
    client.put("/api/settings/platform_filter", json={"value": "naver,kakao"})

    """WHEN 다시 조회하면(새로고침을 흉내)"""
    got = client.get("/api/settings/platform_filter").json()

    """THEN 저장한 값이 그대로 유지된다"""
    assert got["value"] == "naver,kakao"
