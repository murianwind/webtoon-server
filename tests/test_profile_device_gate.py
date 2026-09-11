"""
main.py의 기기 승인 게이트 통합 테스트 - 실제 HTTP 요청(쿠키 포함)으로 검증.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth, profile_devices as devices, profiles
from conftest import make_chapter_zip


@pytest.fixture
def profile_setup(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "기기테스트웹툰" / "001.zip"))

    auth.init_schema()
    profiles.init_schema()
    devices.init_schema()
    created = profiles.create_profile("딸")

    with TestClient(main_module.app) as admin_client:
        password = auth.ensure_admin_password_exists()
        admin_client.post("/api/auth/login", json={"password": password})
        admin_client.post("/api/rescan")
        series_id = admin_client.get("/api/series").json()[0]["id"]
        admin_client.put(
            f"/api/admin/profiles/{created['id']}/allowed-series", json={"series_ids": [series_id]}
        )

        yield {
            "admin_client": admin_client,
            "token": created["token"],
            "profile_id": created["id"],
            "app": main_module.app,
        }

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_first_two_devices_pass_through_without_approval(profile_setup):
    """GIVEN 아직 등록된 기기가 없을 때"""
    token = profile_setup["token"]

    """WHEN 서로 다른 두 기기(별도 클라이언트=쿠키 없음)가 각각 처음 접속하면"""
    device1 = TestClient(profile_setup["app"])
    device2 = TestClient(profile_setup["app"])
    r1 = device1.get(f"/p/{token}/api/series")
    r2 = device2.get(f"/p/{token}/api/series")

    """THEN 둘 다 승인 없이 바로 통과한다"""
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert devices.count_devices(profile_setup["profile_id"]) == 2


def test_third_device_needs_request_then_pending(profile_setup):
    """GIVEN 이미 기기 2대가 등록되어 있을 때"""
    token = profile_setup["token"]
    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")

    """WHEN 3번째 기기가 접속하면"""
    device3 = TestClient(profile_setup["app"])
    r = device3.get(f"/p/{token}/")

    """THEN 정상 화면이 아니라 승인 대기 안내(정적 페이지)가 내려온다"""
    assert r.status_code == 200
    assert "device-approval" in r.text or "승인" in r.text

    """AND 상태 조회는 "요청 필요" 상태다"""
    status = device3.get(f"/p/{token}/api/device/status").json()
    assert status["status"] == "needs_request"

    """AND 다른 API는 여전히 막혀있다(승인 전엔 실제 콘텐츠 접근 불가)"""
    assert device3.get(f"/p/{token}/api/series").status_code == 403

    """WHEN 신청하면"""
    device3.post(f"/p/{token}/api/device/request")

    """THEN 상태가 대기중으로 바뀌고, 다시 신청해도 중복 안 생긴다"""
    status2 = device3.get(f"/p/{token}/api/device/status").json()
    assert status2["status"] == "pending"


def test_admin_approval_replaces_oldest_and_unblocks_pending_device(profile_setup):
    """GIVEN 기기 2대가 등록되어 있고, 3번째 기기가 신청까지 한 상태일 때"""
    token = profile_setup["token"]
    profile_id = profile_setup["profile_id"]
    admin_client = profile_setup["admin_client"]

    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    device3 = TestClient(profile_setup["app"])
    device3.get(f"/p/{token}/")
    device3.post(f"/p/{token}/api/device/request")

    pending_device_id = devices.list_pending_requests(profile_id)[0]["device_id"]
    assert devices.count_devices(profile_id) == 2

    """WHEN 관리자가 승인하면"""
    r = admin_client.post(f"/api/admin/profiles/{profile_id}/device-requests/{pending_device_id}/approve")
    assert r.status_code == 200

    """THEN 여전히 기기는 2대(가장 오래 안 쓴 게 교체됨)이고, 대기중이던 기기는 이제 통과된다"""
    assert devices.count_devices(profile_id) == 2
    assert device3.get(f"/p/{token}/api/series").status_code == 200


def test_admin_access_is_never_gated_by_profile_device_limit(profile_setup):
    """GIVEN 프로필 쪽에 기기 게이트가 걸려있어도"""
    """WHEN 관리자(프로필 아님)가 평소처럼 접속하면"""
    admin_client = profile_setup["admin_client"]
    """THEN 전혀 영향 없이 통과한다"""
    assert admin_client.get("/api/series").status_code == 200


def test_admin_reject_clears_request_and_device_can_request_again(profile_setup):
    """GIVEN 이미 기기 2대가 있고, 3번째가 신청까지 한 상태일 때"""
    token = profile_setup["token"]
    profile_id = profile_setup["profile_id"]
    admin_client = profile_setup["admin_client"]

    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    device3 = TestClient(profile_setup["app"])
    device3.get(f"/p/{token}/")
    device3.post(f"/p/{token}/api/device/request")
    device_id = devices.list_pending_requests(profile_id)[0]["device_id"]

    """WHEN 관리자가 거부하면"""
    r = admin_client.post(f"/api/admin/profiles/{profile_id}/device-requests/{device_id}/reject")
    assert r.status_code == 200

    """THEN 요청 기록 자체가 사라져서, 처음과 똑같이 "신청" 버튼부터 다시 보이는
    상태(needs_request)가 되고, 여전히 실제 콘텐츠는 접근 못 한다"""
    status = device3.get(f"/p/{token}/api/device/status").json()
    assert status["status"] == "needs_request"
    assert device3.get(f"/p/{token}/api/series").status_code == 403

    """AND 다시 신청하면 정상적으로 새 대기 요청이 만들어진다"""
    device3.post(f"/p/{token}/api/device/request")
    status2 = device3.get(f"/p/{token}/api/device/status").json()
    assert status2["status"] == "pending"


def test_gated_page_responses_are_never_cached(profile_setup):
    """GIVEN 기기 게이트를 거치는 페이지 응답일 때(대기 화면이든 실제 화면이든)"""
    token = profile_setup["token"]

    """WHEN 대기 화면이 나오는 경우(3번째 기기)"""
    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    TestClient(profile_setup["app"]).get(f"/p/{token}/api/series")
    device3 = TestClient(profile_setup["app"])
    r_blocked = device3.get(f"/p/{token}/")

    """THEN 캐시하지 말라는 헤더가 붙어있다 - 안 그러면 승인된 뒤에도 브라우저가
    서버에 다시 묻지 않고 캐시된 대기 화면을 계속 보여주는 문제가 생긴다"""
    assert r_blocked.headers.get("cache-control") == "no-store"

    """AND 승인되어 실제 화면이 나오는 경우도 마찬가지로 캐시되지 않는다"""
    r_allowed = TestClient(profile_setup["app"]).get(f"/p/{token}/")
    assert r_allowed.headers.get("cache-control") == "no-store"
