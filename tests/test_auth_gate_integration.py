"""
PROFILES_ENABLED=true일 때의 인증 게이트 통합 테스트.

PROFILES_ENABLED는 main.py가 모듈을 처음 import할 때 딱 한 번 환경변수를 읽어서
라우터를 조건부로 등록하는 값이라(운영 코드를 "매 요청마다 다시 확인"하는 방식 대신
"시작할 때 한 번 정해지는" 방식으로 일부러 그렇게 둠), 테스트에서 이 값을 다르게
쓰려면 환경변수를 설정한 뒤 importlib.reload로 모듈을 다시 실행시켜야 한다.
"""

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles


@pytest.fixture
def enabled_app(library, monkeypatch):
    """PROFILES_ENABLED=true로 main 모듈을 다시 로드해서 그 app을 돌려준다.
    테스트가 끝나면 다시 비활성 상태로 리로드해서 다른 테스트에 영향을 안 준다."""
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)
    yield main_module.app

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


@pytest.fixture
def enabled_client(enabled_app):
    with TestClient(enabled_app) as c:
        yield c


def test_profiles_disabled_by_default_allows_everything_through(client):
    """GIVEN PROFILES_ENABLED를 아예 설정하지 않은 기본 상태일 때"""
    """WHEN 루트로 접속하면"""
    r = client.get("/api/series")
    """THEN 비밀번호 없이 바로 통과한다(기존 동작 그대로)"""
    assert r.status_code == 200


def test_enabled_blocks_unauthenticated_api_access(enabled_client):
    """GIVEN PROFILES_ENABLED가 켜져 있고, 이 기기가 아직 로그인 안 했을 때"""
    """WHEN 관리자용 API를 호출하면"""
    r = enabled_client.get("/api/series")
    """THEN 401로 막힌다"""
    assert r.status_code == 401


def test_login_with_wrong_password_fails_and_stays_blocked(enabled_client):
    """GIVEN 최초 실행으로 관리자 비밀번호가 생성되어 있을 때"""
    time.sleep(0.3)  # startup 이벤트에서 ensure_admin_password_exists가 끝날 시간
    assert auth.has_admin_password() is True

    """WHEN (비밀번호를 모르는 상태이므로) 잘못된 비밀번호로 로그인을 시도하면 실패하고"""
    bad = enabled_client.post("/api/auth/login", json={"password": "완전히-틀린값"})
    assert bad.status_code == 401

    """AND 그 후에도 여전히 API는 막혀있다"""
    assert enabled_client.get("/api/series").status_code == 401


def test_login_success_sets_cookie_and_unlocks_access(enabled_app):
    """GIVEN 비밀번호를 미리 알고 있는 상태일 때(직접 생성해서 확인)"""
    auth.init_schema()
    password = auth.ensure_admin_password_exists()
    assert password is not None

    with TestClient(enabled_app) as client:
        """WHEN 로그인 전엔 API가 막혀있고"""
        assert client.get("/api/series").status_code == 401

        """AND 올바른 비밀번호로 로그인하면"""
        r = client.post("/api/auth/login", json={"password": password})
        assert r.status_code == 200

        """THEN 그 이후로는(같은 클라이언트=쿠키 유지) API가 정상적으로 통과된다"""
        assert client.get("/api/series").status_code == 200


def test_remembered_device_can_be_revoked_from_device_list(enabled_app):
    """GIVEN 로그인해서 기기가 기억된 상태일 때"""
    auth.init_schema()
    password = auth.ensure_admin_password_exists()

    with TestClient(enabled_app) as client:
        client.post("/api/auth/login", json={"password": password})
        assert client.get("/api/series").status_code == 200

        devices = client.get("/api/auth/devices").json()
        assert len(devices) == 1
        device_id = devices[0]["device_id"]

        """WHEN 관리자가 그 기기를 등록 목록에서 삭제하면"""
        client.delete(f"/api/auth/devices/{device_id}")

        """THEN 같은 쿠키를 들고 있어도(TestClient가 쿠키를 계속 보냄) 더 이상 통과 안 된다"""
        assert client.get("/api/series").status_code == 401


def test_shared_profile_link_bypasses_admin_password_entirely(enabled_app):
    """GIVEN 프로필이 하나 만들어져 있을 때(관리자 비밀번호는 전혀 모르는 상태)"""
    profiles.init_schema()
    created = profiles.create_profile("딸")

    with TestClient(enabled_app) as client:
        """WHEN 로그인 없이 그 프로필의 공유 링크로 API를 호출하면"""
        r = client.get(f"/p/{created['token']}/api/series")

        """THEN 비밀번호 없이도 통과한다 - 링크 자체가 접근 권한이므로"""
        assert r.status_code == 200

        """AND 존재하지 않는(추측한) 토큰은 여전히 막힌다"""
        r2 = client.get("/p/아무렇게나-찍은-토큰/api/series")
        assert r2.status_code == 404
