"""
/api/lookup/latest 관련 회귀 테스트:
1) 브라우저 쿠키 없는 외부 연동(디스코드 알림 봇 등)도 비밀번호 없이 호출 가능해야 함
2) 공유 프로필은 이 API에 접근하면 안 됨(다른 시리즈 정보를 알아낼 수 있는 통로가 되므로)
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from conftest import make_chapter_zip


@pytest.fixture
def enabled_app_with_series(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "룩업테스트" / "001.zip"))

    yield main_module.app

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_lookup_latest_works_without_any_login(enabled_app_with_series):
    """GIVEN PROFILES_ENABLED가 켜져 있어 관리자 비밀번호 게이트가 동작 중일 때"""
    with TestClient(enabled_app_with_series) as client:
        client.post("/api/rescan")

        """WHEN 로그인(쿠키) 없이 lookup/latest를 호출하면(디스코드 알림 봇처럼)"""
        r = client.get("/api/lookup/latest", params={"series": "룩업테스트"})

        """THEN 401로 막히지 않고 정상 응답한다"""
        assert r.status_code == 200
        assert r.json()["chapter_label"]


def test_lookup_latest_blocked_for_shared_profiles(enabled_app_with_series):
    """GIVEN 프로필이 있을 때"""
    from app import profiles

    profiles.init_schema()
    created = profiles.create_profile("딸")

    with TestClient(enabled_app_with_series) as client:
        client.post("/api/rescan")

        """WHEN 그 프로필 링크로 lookup/latest를 호출하면"""
        r = client.get(f"/p/{created['token']}/api/lookup/latest", params={"series": "룩업테스트"})

        """THEN 404로 막힌다 - 이 API로 허용 안 된 시리즈 정보까지 알아낼 수 있으면 안 되므로"""
        assert r.status_code == 404
