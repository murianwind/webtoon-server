"""
/api/lookup/read-state 테스트 (webtoon-manager가 읽지 않은 회차를 아카이빙에서 빼기 위해 호출):
1) 브라우저 쿠키 없는 외부 연동도 비밀번호 없이 호출 가능해야 함
2) 읽은 회차 번호만 돌려주고, '읽는 중'인 회차와 안 읽은 회차는 뺀다
3) 없는 시리즈는 404, 공유 프로필은 접근 불가
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from conftest import make_chapter_zip


@pytest.fixture
def enabled_app_with_series(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    for n in (1, 2, 3, 4):
        make_chapter_zip(str(library / "naver" / "읽음테스트" / f"{n:03d}.zip"))

    yield main_module.app

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


@pytest.fixture
def admin_client(enabled_app_with_series):
    """앱 시작 처리(스키마 초기화)를 거친, 관리자 로그인 상태의 클라이언트."""
    with TestClient(enabled_app_with_series) as client:
        yield client


def _scan_and_mark(admin_client, read_numbers):
    """관리자로 로그인해 스캔하고, 지정한 번호의 회차를 읽음 처리한 뒤 series_id를 돌려준다."""
    from app import auth, db

    admin_client.post("/api/auth/login", json={"password": auth.ensure_admin_password_exists()})
    admin_client.post("/api/rescan")
    series_id = admin_client.get("/api/lookup/latest", params={"series": "읽음테스트"}).json()["series_id"]
    chapters = admin_client.get(f"/api/series/{series_id}/chapters").json()["chapters"]
    db.mark_chapters_read(series_id, [c["id"] for c in chapters if c["sort_key"] in read_numbers])
    return series_id


def test_read_state_returns_only_read_chapter_numbers_without_login(admin_client, enabled_app_with_series):
    """GIVEN 게이트가 켜져 있고 1·2화만 읽음 처리된 시리즈가 있을 때"""
    series_id = _scan_and_mark(admin_client, {1, 2})

    """WHEN 로그인(쿠키) 없는 클라이언트가 read-state를 호출하면"""
    r = TestClient(enabled_app_with_series).get("/api/lookup/read-state", params={"series": "읽음테스트"})

    """THEN 401로 막히지 않고 읽은 회차 번호만 돌려준다"""
    assert r.status_code == 200
    assert r.json() == {"series_id": series_id, "read_numbers": [1, 2]}


def test_read_state_excludes_chapter_being_read(admin_client, enabled_app_with_series):
    """GIVEN 1·2·3화를 읽음 처리했지만 지금은 2화를 다시 열어 보는 중일 때"""
    from app import db

    series_id = _scan_and_mark(admin_client, {1, 2, 3})
    chapters = admin_client.get(f"/api/series/{series_id}/chapters").json()["chapters"]
    second = next(c for c in chapters if c["sort_key"] == 2)
    db.set_progress(series_id, second["id"], 1, 5)

    """WHEN read-state를 호출하면"""
    r = TestClient(enabled_app_with_series).get("/api/lookup/read-state", params={"series": "읽음테스트"})

    """THEN '읽는 중'인 2화는 읽음에서 빠진다"""
    assert r.json()["read_numbers"] == [1, 3]


def test_read_state_unknown_series_is_404(admin_client, enabled_app_with_series):
    """GIVEN 스캔된 시리즈가 있을 때 WHEN 없는 이름으로 호출하면 THEN 404"""
    _scan_and_mark(admin_client, set())
    r = TestClient(enabled_app_with_series).get("/api/lookup/read-state", params={"series": "없는작품"})
    assert r.status_code == 404


def test_read_state_blocked_for_shared_profiles(admin_client):
    """GIVEN 프로필이 있을 때 WHEN 그 프로필 링크로 호출하면 THEN 404 - 허용 안 된 시리즈 정보를 알아낼 통로가 되면 안 되므로"""
    from app import profiles

    profiles.init_schema()
    created = profiles.create_profile("딸")
    _scan_and_mark(admin_client, {1})

    r = admin_client.get(f"/p/{created['token']}/api/lookup/read-state", params={"series": "읽음테스트"})
    assert r.status_code == 404
