"""
app/profile_progress.py 회귀 테스트. 핵심 확인 포인트: 서로 다른 프로필의 진행률이
완전히 분리되어야 하고, 관리자(db.py)의 기존 progress 테이블과는 아예 다른 테이블이라
서로 영향이 없어야 한다.
"""

from app import db, profile_progress as pp


def test_progress_is_isolated_per_profile(library):
    """GIVEN 두 프로필이 같은 시리즈를 볼 때"""
    pp.init_schema()

    """WHEN 서로 다른 위치까지 진행률을 저장하면"""
    pp.set_progress("profile-a", "series-1", "chapter-5", 4, 0)
    pp.set_progress("profile-b", "series-1", "chapter-2", 1, 0)

    """THEN 각자의 진행률만 조회된다(서로 안 겹침)"""
    assert pp.get_progress("profile-a", "series-1")["chapter_id"] == "chapter-5"
    assert pp.get_progress("profile-b", "series-1")["chapter_id"] == "chapter-2"


def test_progress_does_not_touch_admin_progress_table(library):
    """GIVEN 관리자(공용) 진행률이 이미 저장되어 있을 때"""
    db.init_schema()
    db.set_progress("series-1", "chapter-9", 8, 0)

    """WHEN 프로필 진행률을 같은 series_id로 저장해도"""
    pp.init_schema()
    pp.set_progress("profile-a", "series-1", "chapter-1", 0, 0)

    """THEN 관리자 쪽 진행률은 전혀 안 바뀐다 - 완전히 다른 테이블이므로"""
    admin_prog = db.get_progress("series-1")
    assert admin_prog["chapter_id"] == "chapter-9"


def test_read_chapters_isolated_per_profile(library):
    """GIVEN 두 프로필이 있을 때"""
    pp.init_schema()

    """WHEN 각자 다른 회차를 읽음으로 표시하면"""
    pp.mark_chapters_read("profile-a", "series-1", ["ch1", "ch2"])
    pp.mark_chapters_read("profile-b", "series-1", ["ch1"])

    """THEN 각자의 읽음 목록만 조회된다"""
    assert pp.get_read_chapter_ids("profile-a", "series-1") == {"ch1", "ch2"}
    assert pp.get_read_chapter_ids("profile-b", "series-1") == {"ch1"}


def test_mark_unread_only_affects_that_profile(library):
    """GIVEN 두 프로필이 같은 회차를 읽음으로 표시했을 때"""
    pp.init_schema()
    pp.mark_chapters_read("profile-a", "series-1", ["ch1"])
    pp.mark_chapters_read("profile-b", "series-1", ["ch1"])

    """WHEN 한 프로필만 안읽음으로 되돌리면"""
    pp.mark_chapters_unread("profile-a", "series-1", ["ch1"])

    """THEN 그 프로필만 안읽음이 되고, 다른 프로필은 그대로 읽음이다"""
    assert pp.get_read_chapter_ids("profile-a", "series-1") == set()
    assert pp.get_read_chapter_ids("profile-b", "series-1") == {"ch1"}


def test_delete_all_data_for_profile_clears_progress_and_read_chapters(library):
    """GIVEN 진행률/읽음기록이 있는 프로필이 있을 때"""
    pp.init_schema()
    pp.set_progress("profile-a", "series-1", "chapter-1", 0, 0)
    pp.mark_chapters_read("profile-a", "series-1", ["ch1"])

    """WHEN 프로필 삭제에 맞춰 데이터를 정리하면"""
    pp.delete_all_data_for_profile("profile-a")

    """THEN 진행률과 읽음기록 모두 사라진다"""
    assert pp.get_progress("profile-a", "series-1") is None
    assert pp.get_read_chapter_ids("profile-a", "series-1") == set()
