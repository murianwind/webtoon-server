"""
app/profiles.py 회귀 테스트.
"""

from app import profiles


def test_create_profile_gets_a_unique_unguessable_token(library):
    """GIVEN 프로필이 없을 때"""
    profiles.init_schema()

    """WHEN 프로필 두 개를 만들면"""
    p1 = profiles.create_profile("딸")
    p2 = profiles.create_profile("아들")

    """THEN 각자 다른 토큰을 가지고, 충분히 길다(추측 어려움)"""
    assert p1["token"] != p2["token"]
    assert len(p1["token"]) > 20


def test_profile_lookup_by_token_and_id(library):
    """GIVEN 프로필이 만들어져 있을 때"""
    profiles.init_schema()
    created = profiles.create_profile("딸")

    """WHEN 토큰으로 조회하면"""
    found = profiles.get_profile_by_token(created["token"])

    """THEN 같은 프로필이 나온다"""
    assert found["id"] == created["id"]
    assert found["name"] == "딸"

    """AND 존재하지 않는 토큰은 None"""
    assert profiles.get_profile_by_token("존재하지-않는-토큰") is None


def test_reissue_token_invalidates_old_one(library):
    """GIVEN 프로필이 있을 때"""
    profiles.init_schema()
    created = profiles.create_profile("딸")
    old_token = created["token"]

    """WHEN 토큰을 재발급하면"""
    new_token = profiles.reissue_profile_token(created["id"])

    """THEN 기존 토큰으로는 더 이상 조회가 안 되고, 새 토큰으로는 조회된다"""
    assert new_token != old_token
    assert profiles.get_profile_by_token(old_token) is None
    found = profiles.get_profile_by_token(new_token)
    assert found["id"] == created["id"]
    assert found["name"] == "딸"  # 이름 등 다른 정보는 그대로 유지


def test_allowed_series_replace_semantics(library):
    """GIVEN 허용 시리즈가 설정되어 있을 때"""
    profiles.init_schema()
    created = profiles.create_profile("딸")
    profiles.set_allowed_series(created["id"], ["series-a", "series-b"])
    assert profiles.get_allowed_series_ids(created["id"]) == {"series-a", "series-b"}

    """WHEN 다시 설정하면(통째로 교체)"""
    profiles.set_allowed_series(created["id"], ["series-c"])

    """THEN 이전 목록은 사라지고 새 목록만 남는다"""
    assert profiles.get_allowed_series_ids(created["id"]) == {"series-c"}


def test_browse_filters_support_no_rating_sentinel(library):
    """GIVEN 프로필이 있을 때"""
    profiles.init_schema()
    created = profiles.create_profile("딸")

    """WHEN 연령정보 없는 것까지 포함해서 둘러보기 필터를 설정하면"""
    profiles.set_browse_filters(
        created["id"],
        [("네이버", "전체 이용가"), ("카카오", profiles.NO_AGE_RATING)],
    )

    """THEN 그대로 조회된다"""
    filters = profiles.get_browse_filters(created["id"])
    assert ("네이버", "전체 이용가") in filters
    assert ("카카오", profiles.NO_AGE_RATING) in filters


def test_delete_profile_removes_related_filters_and_allowed_series(library):
    """GIVEN 허용목록/필터가 설정된 프로필이 있을 때"""
    profiles.init_schema()
    created = profiles.create_profile("딸")
    profiles.set_allowed_series(created["id"], ["series-a"])
    profiles.set_browse_filters(created["id"], [("네이버", "전체 이용가")])

    """WHEN 프로필을 삭제하면"""
    profiles.delete_profile(created["id"])

    """THEN 프로필 자체와 연결된 데이터가 전부 사라진다"""
    assert profiles.get_profile(created["id"]) is None
    assert profiles.get_allowed_series_ids(created["id"]) == set()
    assert profiles.get_browse_filters(created["id"]) == set()
