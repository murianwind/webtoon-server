"""
목록의 "읽는 중" 필터가 "읽다 만 웹툰"을 놓치지 않도록, 서버가 각 시리즈에 "읽기 시작했는지"(이어보기
위치가 저장돼 있는지)를 내려주는지 확인하는 회귀 테스트.

배경: 시리즈의 "읽는 중"은 "한 회차라도 읽음 처리된 경우"로만 판단했는데, 회차는 그 앞 회차를 지나가야
읽음이 된다. 그래서 새 웹툰의 1화를 읽고 있어도(이어보기 위치는 저장됨) 읽은 회차는 0개라서
"읽지 않음"으로 분류되어 "읽는 중" 필터에 안 나왔다.
"""

import asyncio
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles
from conftest import make_chapter_zip


@pytest.fixture
def one_series(client, library):
    for chapter in ("001", "002", "003"):
        make_chapter_zip(str(library / "naver" / "웹툰" / f"{chapter}.zip"))
    client.post("/api/rescan")
    item = client.get("/api/series").json()[0]
    chapters = client.get(f"/api/series/{item['id']}/chapters").json()["chapters"]
    return {"id": item["id"], "chapters": chapters}


def _item(client, series_id):
    return next(s for s in client.get("/api/series").json() if s["id"] == series_id)


def test_untouched_series_has_not_started(client, one_series):
    """GIVEN 한 번도 열어본 적 없는 웹툰"""
    """THEN 읽기 시작하지 않은 것으로 표시된다"""
    assert _item(client, one_series["id"])["started"] is False


def test_series_with_a_saved_reading_position_counts_as_started_even_if_no_chapter_is_finished(client, one_series):
    """GIVEN 새 웹툰의 1화를 읽는 중(이어보기 위치만 저장되고, 읽음 처리된 회차는 아직 0개)"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][0]["id"], "page_index": 1})

    item = _item(client, sid)

    """THEN 읽은 회차는 0개 그대로지만, 읽기를 시작한 웹툰으로 표시된다"""
    assert item["unread_count"] == item["chapter_count"]
    assert item["started"] is True


def test_marking_everything_unread_resets_started(client, one_series):
    """GIVEN 읽기 시작한 웹툰"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][0]["id"], "page_index": 1})
    assert _item(client, sid)["started"] is True

    """WHEN 전체 읽지 않음으로 표시하면(이어보기 위치도 같이 지워짐)"""
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": False})

    """THEN 다시 읽기 시작하지 않은 상태가 된다"""
    assert _item(client, sid)["started"] is False


def test_saved_position_pointing_to_a_vanished_chapter_does_not_count_as_started(client, library, one_series):
    """GIVEN 2화를 읽는 중이던 웹툰(이어보기 위치가 2화를 가리킴)"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][1]["id"], "page_index": 1})
    assert _item(client, sid)["started"] is True

    """WHEN 그 회차 파일의 이름이 바뀐 뒤 재스캔하면(회차 ID는 파일명으로 만들어서, 이름이 바뀌면 ID도 바뀜 -
    이어보기 위치와 읽음 기록은 이제 목록에 없는 옛 ID를 가리키게 된다)"""
    series_dir = library / "naver" / "웹툰"
    os.rename(series_dir / "002.zip", series_dir / "002 다시받음.zip")
    client.post("/api/rescan")
    item = _item(client, sid)

    """THEN 저장된 위치가 가리키는 회차가 이제 없으므로, 읽기 시작한 웹툰으로 세지 않는다(회차 목록에
    읽는 중 표시가 없는데 읽는 중 필터에만 걸리는 일이 없도록). 이름이 안 바뀐 1화의 읽음 기록은 그대로다."""
    assert item["started"] is False
    assert item["unread_count"] == 2  # 3개 중 1화만 읽음(2화를 읽는 중이면 그 앞 1화는 읽음 처리됨)


def test_position_on_a_chapter_that_still_exists_stays_started_after_other_files_are_renamed(client, library, one_series):
    """GIVEN 2화를 읽는 중이던 웹툰"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][1]["id"], "page_index": 1})

    """WHEN 읽던 회차가 아닌 다른 회차 파일만 이름이 바뀌면"""
    series_dir = library / "naver" / "웹툰"
    os.rename(series_dir / "003.zip", series_dir / "003 다시받음.zip")
    client.post("/api/rescan")

    """THEN 읽던 회차는 그대로 있으니 계속 읽는 중이다"""
    assert _item(client, sid)["started"] is True


def test_started_is_also_reported_for_shared_profiles(library, monkeypatch):
    """GIVEN 공유 프로필이 허용된 웹툰의 1화를 읽는 중일 때"""
    import app.main as main_module
    from app import catalog, services

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)
    for chapter in ("001", "002"):
        make_chapter_zip(str(library / "naver" / "공유웹툰" / f"{chapter}.zip"))

    with TestClient(main_module.app) as client:
        auth.init_schema()
        auth.ensure_admin_password_exists()
        profiles.init_schema()
        asyncio.run(services.scan_all_platforms_incrementally())
        series_id = next(iter(catalog.get_series_map()))
        profile = profiles.create_profile("딸")
        profiles.set_allowed_series(profile["id"], [series_id])
        base = f"/p/{profile['token']}"

        before = client.get(f"{base}/api/series").json()[0]
        chapters = client.get(f"{base}/api/series/{series_id}/chapters").json()["chapters"]
        client.put(f"{base}/api/series/{series_id}/progress", json={"chapter_id": chapters[0]["id"], "page_index": 0})
        after = client.get(f"{base}/api/series").json()[0]

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)

    """THEN 프로필의 읽기 시작 여부는 그 프로필의 이어보기 위치를 따른다"""
    assert before["started"] is False
    assert after["started"] is True
