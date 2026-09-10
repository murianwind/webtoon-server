"""
routers/profiles.py(관리자용 프로필 관리 API) 통합 테스트.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import access_requests, auth
from conftest import make_chapter_zip


@pytest.fixture
def admin_client(library, monkeypatch):
    """PROFILES_ENABLED=true로 띄우고, 이미 로그인까지 마친 admin 클라이언트를 준다.

    비밀번호는 서버가 매번 새로 만드는 정책이라, TestClient를 연 뒤(=startup이 이미
    한 번 만들어놓은 뒤) 한 번 더 호출해서 "지금부터 유효한" 값을 확정해둔다 - 그
    이후로는 아무도 다시 안 부르므로 이 값 그대로 로그인에 쓸 수 있다.
    """
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    with TestClient(main_module.app) as client:
        password = auth.ensure_admin_password_exists()
        client.post("/api/auth/login", json={"password": password})
        yield client

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_create_and_list_profiles(admin_client):
    """GIVEN 프로필이 없을 때"""
    """WHEN 프로필을 만들면"""
    r = admin_client.post("/api/admin/profiles", json={"name": "딸"})
    assert r.status_code == 200
    profile_id = r.json()["id"]

    """THEN 목록에서 조회되고, 허용/대기요청 개수는 0으로 시작한다"""
    listed = admin_client.get("/api/admin/profiles").json()
    match = next(p for p in listed if p["id"] == profile_id)
    assert match["name"] == "딸"
    assert match["allowed_count"] == 0
    assert match["pending_request_count"] == 0


def test_set_allowed_series_and_approve_clears_pending_request(admin_client):
    """GIVEN 프로필이 있고, 그 프로필이 어떤 시리즈를 요청해서 대기중 상태일 때"""
    profile_id = admin_client.post("/api/admin/profiles", json={"name": "딸"}).json()["id"]
    access_requests.create_request(profile_id, "series-x")
    listed = admin_client.get("/api/admin/profiles").json()
    assert next(p for p in listed if p["id"] == profile_id)["pending_request_count"] == 1

    """WHEN 관리자가 허용 목록에 그 시리즈를 추가하면(승인)"""
    r = admin_client.put(f"/api/admin/profiles/{profile_id}/allowed-series", json={"series_ids": ["series-x"]})
    assert r.status_code == 200

    """THEN 허용 목록에 반영되고, 대기중 요청은 정리된다"""
    allowed = admin_client.get(f"/api/admin/profiles/{profile_id}/allowed-series").json()
    assert allowed == ["series-x"]
    listed2 = admin_client.get("/api/admin/profiles").json()
    assert next(p for p in listed2 if p["id"] == profile_id)["pending_request_count"] == 0


def test_reject_and_undo_reject_request(admin_client):
    """GIVEN 프로필이 있을 때"""
    profile_id = admin_client.post("/api/admin/profiles", json={"name": "아들"}).json()["id"]
    access_requests.create_request(profile_id, "series-y")

    """WHEN 관리자가 거부하면"""
    admin_client.post(f"/api/admin/profiles/{profile_id}/requests/series-y/reject")
    reqs = admin_client.get(f"/api/admin/profiles/{profile_id}/requests").json()
    assert next(r for r in reqs if r["series_id"] == "series-y")["status"] == "rejected"

    """AND 다시 되돌리면"""
    admin_client.post(f"/api/admin/profiles/{profile_id}/requests/series-y/undo-reject")

    """THEN 요청 기록 자체가 없어져서 다시 요청 가능한 상태가 된다"""
    reqs2 = admin_client.get(f"/api/admin/profiles/{profile_id}/requests").json()
    assert reqs2 == []


def test_browse_filters_roundtrip(admin_client):
    """GIVEN 프로필이 있을 때"""
    profile_id = admin_client.post("/api/admin/profiles", json={"name": "딸"}).json()["id"]

    """WHEN 둘러보기 연령 필터를 설정하면"""
    admin_client.put(
        f"/api/admin/profiles/{profile_id}/browse-filters",
        json={"filters": [{"platform": "네이버", "age_rating": "전체 이용가"}]},
    )

    """THEN 그대로 조회된다"""
    got = admin_client.get(f"/api/admin/profiles/{profile_id}/browse-filters").json()
    assert got == [{"platform": "네이버", "age_rating": "전체 이용가"}]


def test_delete_profile_removes_it_from_listing(admin_client):
    """GIVEN 프로필이 있을 때"""
    profile_id = admin_client.post("/api/admin/profiles", json={"name": "삭제될프로필"}).json()["id"]

    """WHEN 삭제하면"""
    r = admin_client.delete(f"/api/admin/profiles/{profile_id}")
    assert r.status_code == 200

    """THEN 목록에서 더 이상 안 보인다"""
    listed = admin_client.get("/api/admin/profiles").json()
    assert all(p["id"] != profile_id for p in listed)


def test_reissue_token_changes_the_shareable_link(admin_client):
    """GIVEN 프로필이 있을 때"""
    created = admin_client.post("/api/admin/profiles", json={"name": "딸"}).json()
    old_token = created["token"]

    """WHEN 토큰을 재발급하면"""
    r = admin_client.post(f"/api/admin/profiles/{created['id']}/reissue-token")

    """THEN 새 토큰이 나오고, 기존 토큰과 다르다"""
    new_token = r.json()["token"]
    assert new_token != old_token


def test_series_catalog_includes_writer_and_age_rating(admin_client, library):
    """GIVEN info.xml이 있는 시리즈와 없는 시리즈가 섞여 있을 때"""
    make_chapter_zip(str(library / "naver" / "정보없음" / "001.zip"))
    kakao_dir = library / "kakao" / "정보있음"
    make_chapter_zip(str(kakao_dir / "001.zip"))
    (kakao_dir / "info.xml").write_text(
        "<ComicInfo><Writer>김작가</Writer><AgeRating>15세 이용가</AgeRating></ComicInfo>", encoding="utf-8"
    )
    admin_client.post("/api/rescan")

    """WHEN 시리즈 카탈로그(선택 화면용)를 조회하면"""
    items = admin_client.get("/api/admin/series-catalog").json()

    """THEN 저자/연령등급이 있으면 그대로, 없으면 None으로 나온다"""
    with_info = next(i for i in items if i["title"] == "정보있음")
    without_info = next(i for i in items if i["title"] == "정보없음")
    assert with_info["writer"] == "김작가"
    assert with_info["age_rating"] == "15세 이용가"
    assert without_info["writer"] is None
    assert without_info["age_rating"] is None


def test_age_ratings_endpoint_reports_has_unrated(admin_client, library):
    """GIVEN 네이버는 전부 연령정보가 없을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰" / "001.zip"))
    admin_client.post("/api/rescan")

    """WHEN 연령등급 집계를 조회하면"""
    result = admin_client.get("/api/admin/age-ratings").json()

    """THEN 네이버는 실제 등급 목록은 비어있고, has_unrated는 true다"""
    assert result["naver"]["ratings"] == []
    assert result["naver"]["has_unrated"] is True
