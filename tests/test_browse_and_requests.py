"""
"둘러보기" + "보여주세요" 요청 제출 흐름 통합 테스트.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles
from conftest import make_chapter_zip


@pytest.fixture
def browse_setup(library, monkeypatch):
    """네이버(연령정보 없음) 시리즈 하나를 스캔해두고, 프로필의 둘러보기 필터에
    "네이버 + 정보없음"을 포함시켜서, 그 시리즈가 둘러보기에 뜨는 상태로 만든다."""
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "둘러보기대상" / "001.zip"))

    profiles.init_schema()
    created = profiles.create_profile("딸")
    profiles.set_browse_filters(created["id"], [("naver", profiles.NO_AGE_RATING)])

    auth.init_schema()

    with TestClient(main_module.app) as admin_client:
        # TestClient(=startup 이벤트)를 연 뒤 한 번 더 호출해서 "지금부터 유효한"
        # 비밀번호를 확정해둔다(서버가 매번 새로 만드는 정책이라 반드시 이 순서여야 함).
        password = auth.ensure_admin_password_exists()
        admin_client.post("/api/auth/login", json={"password": password})
        admin_client.post("/api/rescan")
        series_id = next(s["id"] for s in admin_client.get("/api/series").json() if s["title"] == "둘러보기대상")

        profile_client = TestClient(main_module.app)
        yield {
            "admin": admin_client,
            "profile": profile_client,
            "profile_id": created["id"],
            "token": created["token"],
            "series_id": series_id,
        }

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_matching_series_appears_in_browse(browse_setup):
    """GIVEN 연령 필터를 통과하는(정보없음 포함) 시리즈가 있을 때"""
    """WHEN 그 프로필로 둘러보기를 조회하면"""
    r = browse_setup["profile"].get(f"/p/{browse_setup['token']}/api/browse")

    """THEN 그 시리즈가 표지/제목만 담겨서 나오고, 아직 요청 전이라 request_status는 None"""
    items = r.json()
    assert len(items) == 1
    assert items[0]["title"] == "둘러보기대상"
    assert items[0]["request_status"] is None
    assert "chapters" not in items[0]  # 회차 정보는 절대 노출 안 됨


def test_allowed_series_disappears_from_browse(browse_setup):
    """GIVEN 둘러보기에 뜨는 시리즈가 있을 때"""
    """WHEN 관리자가 그 시리즈를 허용 목록에 추가하면"""
    profiles.set_allowed_series(browse_setup["profile_id"], [browse_setup["series_id"]])

    """THEN 더 이상 둘러보기에 안 뜬다(이제 "내 웹툰"에 있어야 하므로)"""
    r = browse_setup["profile"].get(f"/p/{browse_setup['token']}/api/browse")
    assert r.json() == []


def test_request_then_reject_then_cannot_request_again(browse_setup):
    """GIVEN 둘러보기에 시리즈가 뜨는 상태일 때"""
    token = browse_setup["token"]
    series_id = browse_setup["series_id"]

    """WHEN "보여주세요"를 누르면"""
    r = browse_setup["profile"].post(f"/p/{token}/api/browse/{series_id}/request")
    assert r.status_code == 200

    """THEN 대기중 상태로 뜬다"""
    items = browse_setup["profile"].get(f"/p/{token}/api/browse").json()
    assert items[0]["request_status"] == "pending"

    """AND 관리자가 거부하면"""
    browse_setup["admin"].post(f"/api/admin/profiles/{browse_setup['profile_id']}/requests/{series_id}/reject")

    """THEN 거부됨으로 뜨고, 다시 요청을 시도하면 막힌다"""
    items2 = browse_setup["profile"].get(f"/p/{token}/api/browse").json()
    assert items2[0]["request_status"] == "rejected"

    r2 = browse_setup["profile"].post(f"/p/{token}/api/browse/{series_id}/request")
    assert r2.status_code == 400


def test_admin_undo_reject_allows_requesting_again(browse_setup):
    """GIVEN 거부된 요청이 있을 때"""
    token = browse_setup["token"]
    series_id = browse_setup["series_id"]
    browse_setup["profile"].post(f"/p/{token}/api/browse/{series_id}/request")
    browse_setup["admin"].post(f"/api/admin/profiles/{browse_setup['profile_id']}/requests/{series_id}/reject")

    """WHEN 관리자가 거부를 되돌리면"""
    browse_setup["admin"].post(f"/api/admin/profiles/{browse_setup['profile_id']}/requests/{series_id}/undo-reject")

    """THEN 다시 요청할 수 있다"""
    r = browse_setup["profile"].post(f"/p/{token}/api/browse/{series_id}/request")
    assert r.status_code == 200


def test_browse_is_not_available_for_admin(browse_setup):
    """GIVEN 관리자(프로필 없이) 상태일 때"""
    """WHEN 둘러보기 API를 호출하면"""
    r = browse_setup["admin"].get("/api/browse")

    """THEN 애초에 개념이 없으므로 404"""
    assert r.status_code == 404
