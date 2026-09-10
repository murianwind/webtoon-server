"""
1) 프로필별 설정(검색 필터 등) 분리
2) 둘러보기 항목의 info/cover 미리보기 접근 허용
회귀 테스트.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles
from conftest import make_chapter_zip


@pytest.fixture
def two_profiles_and_admin(library, monkeypatch):
    """관리자 + 프로필 2개(A, B)를 모두 로그인된 상태로 준비."""
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    kakao_dir = library / "kakao" / "정보있는웹툰"
    make_chapter_zip(str(kakao_dir / "001.zip"))
    (kakao_dir / "info.xml").write_text(
        "<ComicInfo><Writer>루드비코</Writer><Genre>공포,스릴러</Genre>"
        "<AgeRating>전체 이용가</AgeRating><Summary>히키코모리의 외출</Summary></ComicInfo>",
        encoding="utf-8",
    )

    auth.init_schema()

    profiles.init_schema()
    profile_a = profiles.create_profile("A")
    profile_b = profiles.create_profile("B")
    profiles.set_browse_filters(profile_a["id"], [("kakao", "전체 이용가")])

    with TestClient(main_module.app) as admin_client:
        # TestClient(=startup 이벤트)를 연 뒤 한 번 더 호출해서 "지금부터 유효한"
        # 비밀번호를 확정해둔다(서버가 매번 새로 만드는 정책이라 반드시 이 순서여야 함).
        password = auth.ensure_admin_password_exists()
        admin_client.post("/api/auth/login", json={"password": password})
        admin_client.post("/api/rescan")
        series_id = admin_client.get("/api/series").json()[0]["id"]

        client_a = TestClient(main_module.app)
        client_b = TestClient(main_module.app)
        yield {
            "admin": admin_client,
            "a": client_a,
            "b": client_b,
            "token_a": profile_a["token"],
            "token_b": profile_b["token"],
            "series_id": series_id,
        }

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_status_filter_is_isolated_between_profiles(two_profiles_and_admin):
    """GIVEN 프로필 A와 B가 있을 때"""
    token_a = two_profiles_and_admin["token_a"]
    token_b = two_profiles_and_admin["token_b"]

    """WHEN A가 상태 필터를 "읽는 중"으로 저장하면"""
    two_profiles_and_admin["a"].put(f"/p/{token_a}/api/settings/status_filter", json={"value": "reading"})

    """THEN B는 전혀 영향받지 않고, 관리자도 영향받지 않는다"""
    b_value = two_profiles_and_admin["b"].get(f"/p/{token_b}/api/settings/status_filter").json()["value"]
    assert b_value is None
    admin_value = two_profiles_and_admin["admin"].get("/api/settings/status_filter").json()["value"]
    assert admin_value is None

    """AND A 본인은 저장한 값을 그대로 다시 받는다"""
    a_value = two_profiles_and_admin["a"].get(f"/p/{token_a}/api/settings/status_filter").json()["value"]
    assert a_value == "reading"


def test_browse_item_cover_and_info_are_accessible_before_approval(two_profiles_and_admin):
    """GIVEN 아직 허용되지 않았지만 둘러보기 필터에는 걸리는 시리즈가 있을 때"""
    token_a = two_profiles_and_admin["token_a"]
    series_id = two_profiles_and_admin["series_id"]

    """WHEN 그 시리즈의 커버/정보를 직접 요청하면(둘러보기 카드가 하는 것과 동일)"""
    cover_res = two_profiles_and_admin["a"].get(f"/p/{token_a}/api/series/{series_id}/cover")
    info_res = two_profiles_and_admin["a"].get(f"/p/{token_a}/api/series/{series_id}/info")

    """THEN 403으로 막히지 않고, 실제 info.xml 내용이 그대로 나온다"""
    assert cover_res.status_code == 200
    assert info_res.status_code == 200
    info = info_res.json()
    assert info["writer"] == "루드비코"
    assert info["genre"] == "공포,스릴러"
    assert info["age_rating"] == "전체 이용가"
    assert info["summary"] == "히키코모리의 외출"


def test_browse_item_chapters_still_blocked_before_approval(two_profiles_and_admin):
    """GIVEN 둘러보기 필터에는 걸리지만 아직 허용 안 된 시리즈가 있을 때"""
    token_a = two_profiles_and_admin["token_a"]
    series_id = two_profiles_and_admin["series_id"]

    """WHEN 회차 목록처럼 실제 콘텐츠 API를 요청하면"""
    r = two_profiles_and_admin["a"].get(f"/p/{token_a}/api/series/{series_id}/chapters")

    """THEN 커버/정보는 미리보기가 되어도, 실제 콘텐츠는 여전히 403으로 막힌다"""
    assert r.status_code == 403


def test_browse_item_not_matching_filter_still_blocked_from_preview(two_profiles_and_admin):
    """GIVEN 프로필 B는 둘러보기 필터가 아예 없을 때"""
    token_b = two_profiles_and_admin["token_b"]
    series_id = two_profiles_and_admin["series_id"]

    """WHEN B가 그 시리즈의 커버/정보를 직접 요청하면(필터에 안 걸리므로 둘러보기에도 안 보일 시리즈)"""
    cover_res = two_profiles_and_admin["b"].get(f"/p/{token_b}/api/series/{series_id}/cover")

    """THEN 미리보기 완화 검사도 통과 못 하고 403으로 막힌다 - 필터에 안 걸리면 미리보기 권한도 없음"""
    assert cover_res.status_code == 403
