"""
공유 프로필이 관리자 전용 라우터(재스캔/폴더관리/백업/기기관리/프로필관리)를 자기
링크로 직접 호출해서 우회하지 못하는지 확인하는 보안 회귀 테스트. 이 라우터들은
전부 admin/profile 구분 없이 열려있던 실제 취약점이었다.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles


@pytest.fixture
def profile_client(library, monkeypatch):
    """관리자 데이터는 그대로 두고, 프로필 하나의 공유 링크로만 접근하는 클라이언트."""
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    auth.init_schema()
    auth.ensure_admin_password_exists()
    profiles.init_schema()
    created = profiles.create_profile("딸")

    with TestClient(main_module.app) as client:
        yield {"client": client, "token": created["token"], "profile_id": created["id"]}

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_profile_cannot_trigger_rescan(profile_client):
    """WHEN 프로필이 자기 링크로 재스캔 API를 직접 호출하면"""
    r = profile_client["client"].post(f"/p/{profile_client['token']}/api/rescan")
    """THEN 404로 막힌다(그런 기능이 있는지조차 알 수 없어야 함)"""
    assert r.status_code == 404


def test_profile_cannot_access_series_folder_management(profile_client):
    r = profile_client["client"].get(f"/p/{profile_client['token']}/api/series-folders")
    assert r.status_code == 404


def test_profile_cannot_access_backup_or_restore(profile_client):
    r1 = profile_client["client"].get(f"/p/{profile_client['token']}/api/backup")
    r2 = profile_client["client"].post(f"/p/{profile_client['token']}/api/restore", json={})
    assert r1.status_code == 404
    assert r2.status_code == 404


def test_profile_cannot_manage_other_profiles(profile_client):
    """WHEN 프로필이 자기 링크로 전체 프로필 목록/관리 API를 직접 호출하면"""
    r1 = profile_client["client"].get(f"/p/{profile_client['token']}/api/admin/profiles")
    r2 = profile_client["client"].post(
        f"/p/{profile_client['token']}/api/admin/profiles/{profile_client['profile_id']}/reissue-token"
    )
    """THEN 둘 다 404로 막힌다 - 다른 프로필을 보거나 자기 토큰을 재발급하는 것도 불가"""
    assert r1.status_code == 404
    assert r2.status_code == 404


def test_profile_cannot_access_device_management(profile_client):
    r = profile_client["client"].get(f"/p/{profile_client['token']}/api/auth/devices")
    assert r.status_code == 404


def test_admin_path_gives_401_not_404_when_unauthenticated(profile_client):
    """GIVEN 프로필 경로가 아닌 순수 관리자 경로로(로그인 없이) 요청하면"""
    r = profile_client["client"].post("/api/rescan")
    """THEN "프로필이라 그런 기능이 없다"(404)가 아니라 "로그인이 필요하다"(401)가
    나와야 한다 - 두 실패 사유가 뒤섞이면 안 되므로, 관리자 경로는 항상 이 gate를
    거쳐야지 require_admin의 404로 잘못 걸려서는 안 된다."""
    assert r.status_code == 401
