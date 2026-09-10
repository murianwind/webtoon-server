"""
"제외"는 관리자 메인 화면에서만 숨기는 것이고, 공유 프로필에게는 여전히 선택
후보로 줄 수 있어야 한다는 회귀 테스트.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles
from conftest import make_chapter_zip


@pytest.fixture
def admin_client_with_excluded_series(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "제외된웹툰" / "001.zip"))

    auth.init_schema()
    profiles.init_schema()

    with TestClient(main_module.app) as client:
        password = auth.ensure_admin_password_exists()
        client.post("/api/auth/login", json={"password": password})
        client.post("/api/rescan")
        client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "제외된웹툰"})
        yield client

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_excluded_series_hidden_from_admin_main_list(admin_client_with_excluded_series):
    """GIVEN 시리즈를 제외했을 때"""
    client = admin_client_with_excluded_series
    """THEN 관리자 메인 목록에는 안 보인다(기존 동작 그대로)"""
    assert client.get("/api/series").json() == []


def test_excluded_series_still_appears_in_profile_sharing_candidates(admin_client_with_excluded_series):
    """GIVEN 시리즈를 제외했을 때"""
    client = admin_client_with_excluded_series
    """WHEN 프로필 허용목록 선택용 시리즈 카탈로그를 조회하면"""
    catalog_items = client.get("/api/admin/series-catalog").json()
    """THEN 제외된 시리즈도 여전히 후보로 나온다"""
    assert any(item["title"] == "제외된웹툰" for item in catalog_items)


def test_profile_can_be_granted_an_excluded_series_and_access_it(admin_client_with_excluded_series):
    """GIVEN 제외된 시리즈가 있을 때"""
    client = admin_client_with_excluded_series
    series_id = client.get("/api/admin/series-catalog").json()[0]["id"]

    created = client.post("/api/admin/profiles", json={"name": "딸"}).json()

    """WHEN 관리자가 그 제외된 시리즈를 프로필에게 허용하면"""
    r = client.put(f"/api/admin/profiles/{created['id']}/allowed-series", json={"series_ids": [series_id]})
    assert r.status_code == 200

    """THEN 그 프로필은 "내 웹툰"에서 정상적으로 보고 접근할 수 있다"""
    # 같은 client(HTTP 클라이언트일 뿐)로도 /p/ 경로는 토큰이 접근권한이라 문제없이 확인 가능
    r2 = client.get(f"/p/{created['token']}/api/series")
    titles = [s["title"] for s in r2.json()]
    assert "제외된웹툰" in titles

    r3 = client.get(f"/p/{created['token']}/api/series/{series_id}/chapters")
    assert r3.status_code == 200
