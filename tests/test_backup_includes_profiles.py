"""
백업/복원에 공유 프로필 전체(프로필 자체·토큰·허용 시리즈·둘러보기 필터·진행률·
읽음기록·설정·접속 시간대·요청 내역)가 실제로 포함되는지 확인하는 종단 회귀 테스트.
프로필 기능이 추가된 뒤에도 이 부분이 백업 범위에 안 들어가 있던 문제가 있었다.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import (
    access_requests,
    auth,
    profile_progress,
    profile_settings,
    profile_time_restrictions,
    profiles,
)
from conftest import make_chapter_zip


@pytest.fixture
def admin_client_with_profile_data(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "백업테스트웹툰" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "백업테스트웹툰" / "002.zip"))

    with TestClient(main_module.app) as client:
        password = auth.ensure_admin_password_exists()
        client.post("/api/auth/login", json={"password": password})
        client.post("/api/rescan")
        series_id = client.get("/api/series").json()[0]["id"]
        chapters = client.get(f"/api/series/{series_id}/chapters").json()["chapters"]

        created = client.post("/api/admin/profiles", json={"name": "딸"}).json()
        profile_id = created["id"]
        original_token = created["token"]

        client.put(
            f"/api/admin/profiles/{profile_id}/allowed-series", json={"series_ids": [series_id]}
        )
        client.put(
            f"/api/admin/profiles/{profile_id}/browse-filters",
            json={"filters": [{"platform": "naver", "age_rating": "전체 이용가"}]},
        )
        client.put(
            f"/api/admin/profiles/{profile_id}/time-windows",
            json={"windows": [{"day_of_week": 0, "start_minute": 60, "end_minute": 120}]},
        )
        # 프로필 쪽에서 진행률/읽음기록/설정을 남김
        client.put(
            f"/p/{original_token}/api/series/{series_id}/progress",
            json={"chapter_id": chapters[0]["id"], "page_index": 2},
        )
        client.put(
            f"/p/{original_token}/api/settings/sort_mode", json={"value": "title"}
        )
        # 대기중인 요청 내역도 하나 남김(다른 시리즈에 대한 요청이라고 가정 - series_id 재사용해도 무방)
        access_requests.create_request(profile_id, series_id)

        yield {
            "client": client, "profile_id": profile_id, "original_token": original_token,
            "series_id": series_id, "chapter_id": chapters[0]["id"],
        }

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_backup_includes_all_profile_related_data(admin_client_with_profile_data):
    ctx = admin_client_with_profile_data
    client = ctx["client"]

    """WHEN 백업을 받으면"""
    backup = client.get("/api/backup").json()

    """THEN 프로필 관련 데이터가 전부 포함되어 있다"""
    assert len(backup["profiles"]) == 1
    assert backup["profiles"][0]["token"] == ctx["original_token"]
    assert len(backup["profile_allowed_series"]) == 1
    assert len(backup["profile_browse_filters"]) == 1
    assert len(backup["profile_time_windows"]) == 1
    assert len(backup["profile_progress"]) == 1
    assert len(backup["profile_settings"]) >= 1
    assert len(backup["access_requests"]) == 1


def test_restore_brings_back_profile_token_and_all_related_data(admin_client_with_profile_data):
    """GIVEN 백업을 받아둔 뒤, 프로필을 완전히 삭제했을 때(예: DB 손상 후 재설치를 흉내)"""
    ctx = admin_client_with_profile_data
    client = ctx["client"]

    backup = client.get("/api/backup").json()
    client.delete(f"/api/admin/profiles/{ctx['profile_id']}")
    assert client.get("/api/admin/profiles").json() == []

    """WHEN 그 백업으로 복원하면"""
    r = client.post("/api/restore", json=backup)
    assert r.status_code == 200
    body = r.json()
    assert body["profiles_count"] == 1
    assert body["profile_progress_count"] == 1

    """THEN 프로필이 원래 토큰(공유 링크) 그대로 복원되고"""
    restored = client.get("/api/admin/profiles").json()
    assert len(restored) == 1
    assert restored[0]["token"] == ctx["original_token"]
    restored_id = restored[0]["id"]

    """AND 허용 시리즈/둘러보기 필터/접속 시간대/요청 내역도 그대로 복원된다"""
    assert client.get(f"/api/admin/profiles/{restored_id}/allowed-series").json() == [ctx["series_id"]]
    assert len(client.get(f"/api/admin/profiles/{restored_id}/browse-filters").json()) == 1
    assert len(client.get(f"/api/admin/profiles/{restored_id}/time-windows").json()) == 1

    """AND 예전 공유 링크(토큰)로 접속해도 이어보기 진행률이 그대로 남아있다"""
    continue_info = client.get(f"/p/{ctx['original_token']}/api/series/{ctx['series_id']}/continue").json()
    assert continue_info["chapter_id"] == ctx["chapter_id"]

    """AND 검색/정렬 설정도 프로필 쪽에 그대로 복원된다"""
    sort_setting = client.get(f"/p/{ctx['original_token']}/api/settings/sort_mode").json()
    assert sort_setting["value"] == "title"


def test_export_import_helpers_work_even_when_tables_were_never_created(library, monkeypatch):
    """GIVEN PROFILES_ENABLED를 켠 적이 한 번도 없어서(관련 테이블이 아예 없을 수 있는 상태)"""
    monkeypatch.delenv("PROFILES_ENABLED", raising=False)

    """WHEN 각 모듈의 export_all을 직접 호출하면(테이블이 없어도 스스로 만들고 진행해야 함)"""
    assert profiles.export_all() == {
        "profiles": [], "profile_browse_filters": [], "profile_allowed_series": [],
    }
    assert profile_progress.export_all() == {"profile_progress": [], "profile_read_chapters": []}
    assert profile_settings.export_all() == []
    assert profile_time_restrictions.export_all() == []
    assert access_requests.export_all() == []
