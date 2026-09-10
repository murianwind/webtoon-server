"""
공유 프로필의 시리즈/회차 접근 제어 + 진행률 분리 통합 테스트.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import profiles
from conftest import make_chapter_zip


@pytest.fixture
def profile_setup(library, monkeypatch):
    """PROFILES_ENABLED=true로 띄우고, 시리즈 2개(A: 허용, B: 비허용)를 스캔해서
    프로필 하나에 A만 허용해둔 상태를 만든다. (일반 client, admin_token 없는 상태로
    profile_client를 반환 - 그 프로필의 공유 링크로만 접근)."""
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "허용웹툰" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "비허용웹툰" / "001.zip"))

    profiles.init_schema()
    created = profiles.create_profile("딸")

    # TestClient(=startup 이벤트)를 열기 전에 미리 비밀번호를 만들어서 평문을 붙잡아둔다
    # (startup이 먼저 만들면 "최초 1회만 평문 공개" 설계상 다시 알아낼 방법이 없어진다).
    from app import auth as auth_module

    auth_module.init_schema()
    password = auth_module.ensure_admin_password_exists()
    assert password is not None

    with TestClient(main_module.app) as client:
        client.post("/api/auth/login", json={"password": password})
        client.post("/api/rescan")
        all_series = client.get("/api/series").json()
        allowed_id = next(s["id"] for s in all_series if s["title"] == "허용웹툰")
        blocked_id = next(s["id"] for s in all_series if s["title"] == "비허용웹툰")
        profiles.set_allowed_series(created["id"], [allowed_id])

        profile_client = TestClient(main_module.app)
        profile_client.base_url = f"{profile_client.base_url}/p/{created['token']}"
        # httpx 신버전은 base_url에 경로가 있어도 그대로 이어붙이므로 확인용으로 그대로 사용
        yield {
            "admin": client,
            "profile": profile_client,
            "profile_id": created["id"],
            "allowed_id": allowed_id,
            "blocked_id": blocked_id,
        }

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_profile_series_list_only_shows_allowed(profile_setup):
    """GIVEN 프로필에 시리즈 A만 허용되어 있을 때"""
    """WHEN 그 프로필 링크로 목록을 조회하면"""
    r = profile_setup["profile"].get("/api/series")

    """THEN 허용된 A만 보이고 B는 안 보인다"""
    titles = [s["title"] for s in r.json()]
    assert titles == ["허용웹툰"]


def test_profile_cannot_access_blocked_series_directly(profile_setup):
    """GIVEN B는 허용 안 된 시리즈일 때"""
    blocked_id = profile_setup["blocked_id"]

    """WHEN 그 시리즈 ID로 직접 접근을 시도하면(목록에 안 보여도 ID를 알아냈다고 가정)"""
    r1 = profile_setup["profile"].get(f"/api/series/{blocked_id}/chapters")
    r2 = profile_setup["profile"].get(f"/api/series/{blocked_id}/continue")
    r3 = profile_setup["profile"].get(f"/api/series/{blocked_id}/cover")

    """THEN 전부 403으로 막힌다"""
    assert r1.status_code == 403
    assert r2.status_code == 403
    assert r3.status_code == 403


def test_profile_cannot_access_chapter_of_blocked_series(profile_setup):
    """GIVEN B(비허용) 시리즈의 회차 ID를 관리자 쪽에서 알아냈을 때"""
    blocked_chapters = profile_setup["admin"].get(f"/api/series/{profile_setup['blocked_id']}/chapters").json()
    blocked_chapter_id = blocked_chapters["chapters"][0]["id"]

    """WHEN 프로필이 그 회차 ID로 직접 페이지를 요청하면(시리즈 API를 안 거치고 우회 시도)"""
    r = profile_setup["profile"].get(f"/api/chapters/{blocked_chapter_id}/pages/0")

    """THEN 여전히 403으로 막힌다 - chapters.py도 같은 권한 검사를 하므로"""
    assert r.status_code == 403


def test_profile_progress_does_not_affect_admin_progress(profile_setup):
    """GIVEN 허용된 시리즈 A가 있을 때"""
    allowed_id = profile_setup["allowed_id"]
    chapters = profile_setup["profile"].get(f"/api/series/{allowed_id}/chapters").json()["chapters"]

    """WHEN 프로필이 그 시리즈를 읽어서 진행률을 저장하면"""
    profile_setup["profile"].put(
        f"/api/series/{allowed_id}/progress", json={"chapter_id": chapters[0]["id"], "page_index": 0}
    )

    """THEN 관리자 쪽 진행률/완독 표시는 전혀 영향받지 않는다(둘 다 회차가 1개뿐이라
    완독 여부로 확인 - 프로필은 아직 안읽음이어야 하고 관리자도 그대로 안읽음이어야 함,
    즉 서로 완전히 분리되어 있어 한쪽 활동이 다른 쪽에 안 새는지가 핵심)"""
    admin_series = profile_setup["admin"].get("/api/series").json()
    admin_entry = next(s for s in admin_series if s["id"] == allowed_id)
    assert admin_entry["unread_count"] == 1  # 관리자는 안 읽은 채로 그대로


def test_shared_link_still_blocked_by_admin_password_gate_is_not_applied(profile_setup):
    """GIVEN 프로필 링크로 접근할 때(관리자 비밀번호를 전혀 모름)"""
    """WHEN 허용된 시리즈에 접근하면"""
    r = profile_setup["profile"].get(f"/api/series/{profile_setup['allowed_id']}/chapters")

    """THEN 관리자 비밀번호 없이도 정상 응답한다(로그인 게이트가 /p/ 경로에는 안 걸림)"""
    assert r.status_code == 200
