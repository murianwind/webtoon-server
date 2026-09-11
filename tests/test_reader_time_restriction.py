"""
리더(회차 페이지)만 막는 시간대 제한 통합 테스트 - 목록/커버 등은 시간과 무관해야 하고,
지금 읽던 회차만 시간이 지나도 예외로 계속 봐야 한다.
"""

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import auth, profile_time_restrictions as time_restrictions, profiles
from conftest import make_chapter_zip


@pytest.fixture
def profile_with_two_chapters(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "시간제한웹툰" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "시간제한웹툰" / "002.zip"))

    auth.init_schema()
    profiles.init_schema()
    time_restrictions.init_schema()
    created = profiles.create_profile("딸")

    with TestClient(main_module.app) as admin_client:
        password = auth.ensure_admin_password_exists()
        admin_client.post("/api/auth/login", json={"password": password})
        admin_client.post("/api/rescan")
        series_id = admin_client.get("/api/series").json()[0]["id"]
        admin_client.put(
            f"/api/admin/profiles/{created['id']}/allowed-series", json={"series_ids": [series_id]}
        )
        # 월요일(0) 18~21시만 허용
        admin_client.put(
            f"/api/admin/profiles/{created['id']}/time-windows",
            json={"windows": [{"day_of_week": 0, "start_minute": 18 * 60, "end_minute": 21 * 60}]},
        )
        chapters = admin_client.get(f"/api/series/{series_id}/chapters").json()["chapters"]

        yield {
            "admin_client": admin_client,
            "profile_client": TestClient(main_module.app),
            "token": created["token"],
            "profile_id": created["id"],
            "series_id": series_id,
            "chapters": chapters,
        }

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_reader_blocked_outside_window_but_list_still_works(profile_with_two_chapters):
    ctx = profile_with_two_chapters
    token = ctx["token"]
    client = ctx["profile_client"]
    chapter_id = ctx["chapters"][0]["id"]

    with patch("app.profile_time_restrictions.datetime") as mock_dt:
        from datetime import datetime

        mock_dt.now.return_value = datetime(2026, 1, 6, 12, 0)  # 화요일(창 없음) - 막혀야 함

        """WHEN 시간대 밖에 회차 페이지를 요청하면"""
        r = client.get(f"/p/{token}/api/chapters/{chapter_id}/pages/0")
        """THEN 403으로 막힌다"""
        assert r.status_code == 403

        """AND 같은 시간에도 시리즈 목록/회차 목록은 정상 조회된다(리더만 막으므로)"""
        assert client.get(f"/p/{token}/api/series").status_code == 200
        assert client.get(f"/p/{token}/api/series/{ctx['series_id']}/chapters").status_code == 200


def test_reader_allowed_inside_window(profile_with_two_chapters):
    ctx = profile_with_two_chapters
    token = ctx["token"]
    client = ctx["profile_client"]
    chapter_id = ctx["chapters"][0]["id"]

    with patch("app.profile_time_restrictions.datetime") as mock_dt:
        from datetime import datetime

        mock_dt.now.return_value = datetime(2026, 1, 5, 19, 0)  # 월요일 19시(창 안)

        r = client.get(f"/p/{token}/api/chapters/{chapter_id}/pages/0")
        assert r.status_code == 200


def test_currently_reading_chapter_is_exempt_outside_window(profile_with_two_chapters):
    """GIVEN 창 안에 있을 때 1화를 읽기 시작해서 이어보기 포인터가 1화를 가리키고 있을 때"""
    ctx = profile_with_two_chapters
    token = ctx["token"]
    client = ctx["profile_client"]
    chapter1_id = ctx["chapters"][0]["id"]
    chapter2_id = ctx["chapters"][1]["id"]

    with patch("app.profile_time_restrictions.datetime") as mock_dt:
        from datetime import datetime

        mock_dt.now.return_value = datetime(2026, 1, 5, 19, 0)  # 창 안
        client.put(f"/p/{token}/api/series/{ctx['series_id']}/progress", json={"chapter_id": chapter1_id, "page_index": 0})

    with patch("app.profile_time_restrictions.datetime") as mock_dt:
        from datetime import datetime

        mock_dt.now.return_value = datetime(2026, 1, 5, 22, 0)  # 같은 월요일이지만 창 밖(21시 이후)

        """WHEN 창 밖에서 "지금 읽던" 1화 페이지를 요청하면"""
        r1 = client.get(f"/p/{token}/api/chapters/{chapter1_id}/pages/0")
        """THEN 예외로 통과된다"""
        assert r1.status_code == 200

        """AND 다른 회차(2화)로 넘어가려는 시도는 막힌다"""
        r2 = client.get(f"/p/{token}/api/chapters/{chapter2_id}/pages/0")
        assert r2.status_code == 403


def test_admin_is_never_affected_by_time_windows(profile_with_two_chapters):
    """GIVEN 프로필에만 시간 제한이 걸려있을 때"""
    ctx = profile_with_two_chapters
    chapter_id = ctx["chapters"][0]["id"]

    with patch("app.profile_time_restrictions.datetime") as mock_dt:
        from datetime import datetime

        mock_dt.now.return_value = datetime(2026, 1, 6, 3, 0)  # 프로필한텐 막힐 시각

        """WHEN 관리자(프로필 아님)가 같은 회차를 요청하면"""
        admin_client = ctx["admin_client"]
        """THEN 관리자는 시간과 무관하게 항상 통과한다"""
        r = admin_client.get(f"/api/chapters/{chapter_id}/pages/0")
        assert r.status_code == 200
