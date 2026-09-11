"""
app/profile_time_restrictions.py 회귀 테스트.
"""

from datetime import datetime

from app import profile_time_restrictions as time_restrictions


def test_no_windows_means_unrestricted(library):
    """GIVEN 창을 하나도 설정 안 한 프로필일 때"""
    time_restrictions.init_schema()

    """THEN 어느 시각이든 항상 허용된다"""
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 5, 3, 0)) is True  # 월요일 새벽


def test_window_allows_inside_and_blocks_outside(library):
    """GIVEN 월요일(day_of_week=0) 18:00~21:00만 허용해뒀을 때"""
    time_restrictions.init_schema()
    time_restrictions.set_time_windows("p1", [{"day_of_week": 0, "start_minute": 18 * 60, "end_minute": 21 * 60}])

    """THEN 그 창 안의 시각은 허용되고"""
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 5, 19, 0)) is True  # 월 19시

    """AND 같은 요일이라도 창 밖의 시각은 막힌다"""
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 5, 22, 0)) is False  # 월 22시

    """AND 다른 요일은 창이 없으니 막힌다"""
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 6, 19, 0)) is False  # 화 19시


def test_multiple_windows_across_different_days(library):
    """GIVEN 평일 저녁 + 주말 낮, 두 개의 창을 설정했을 때"""
    time_restrictions.init_schema()
    time_restrictions.set_time_windows(
        "p1",
        [
            {"day_of_week": 5, "start_minute": 10 * 60, "end_minute": 22 * 60},  # 토요일 10~22시
            {"day_of_week": 6, "start_minute": 10 * 60, "end_minute": 22 * 60},  # 일요일 10~22시
        ],
    )

    """THEN 두 창 다 각자 허용되고, 그 사이 요일은 막힌다"""
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 10, 15, 0)) is True  # 토 15시
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 11, 15, 0)) is True  # 일 15시
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 9, 15, 0)) is False  # 금 15시


def test_windows_are_isolated_per_profile(library):
    """GIVEN 프로필 A만 시간 제한을 설정했을 때"""
    time_restrictions.init_schema()
    time_restrictions.set_time_windows("A", [{"day_of_week": 0, "start_minute": 0, "end_minute": 10}])

    """THEN 다른 프로필 B는 전혀 영향받지 않고 여전히 제한이 없다"""
    assert time_restrictions.is_within_allowed_time("B", now=datetime(2026, 1, 5, 12, 0)) is True


def test_set_time_windows_replaces_not_appends(library):
    """GIVEN 창이 이미 설정되어 있을 때"""
    time_restrictions.init_schema()
    time_restrictions.set_time_windows("p1", [{"day_of_week": 0, "start_minute": 0, "end_minute": 10}])

    """WHEN 다시 설정하면(통째로 교체)"""
    time_restrictions.set_time_windows("p1", [{"day_of_week": 1, "start_minute": 0, "end_minute": 10}])

    """THEN 이전 창은 사라지고 새 창만 남는다"""
    windows = time_restrictions.get_time_windows("p1")
    assert windows == [{"day_of_week": 1, "start_minute": 0, "end_minute": 10}]


def test_empty_list_clears_all_restrictions(library):
    """GIVEN 창이 설정되어 있을 때"""
    time_restrictions.init_schema()
    time_restrictions.set_time_windows("p1", [{"day_of_week": 0, "start_minute": 0, "end_minute": 10}])

    """WHEN 빈 목록으로 설정하면(제한 해제)"""
    time_restrictions.set_time_windows("p1", [])

    """THEN 다시 제한이 없는 상태가 된다"""
    assert time_restrictions.is_within_allowed_time("p1", now=datetime(2026, 1, 5, 3, 0)) is True
