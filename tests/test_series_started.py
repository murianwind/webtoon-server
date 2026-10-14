"""
목록의 "읽는 중" 필터가 회차 목록(사이드바)의 "읽는 중" 표시와 어긋나지 않도록, 서버가 각 시리즈에
"지금 읽고 있는 회차가 있는지"(reading_now)를 내려주는지 확인하는 회귀 테스트.

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


def test_untouched_series_is_not_being_read(client, one_series):
    """GIVEN 한 번도 열어본 적 없는 웹툰"""
    """THEN 지금 읽고 있지 않은 것으로 표시된다"""
    assert _item(client, one_series["id"])["reading_now"] is False


def test_series_with_a_saved_reading_position_is_being_read_even_if_no_chapter_is_finished(client, one_series):
    """GIVEN 새 웹툰의 1화를 읽는 중(이어보기 위치만 저장되고, 읽음 처리된 회차는 아직 0개)"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][0]["id"], "page_index": 1})

    item = _item(client, sid)

    """THEN 읽은 회차는 0개 그대로지만, 읽기를 시작한 웹툰으로 표시된다"""
    assert item["unread_count"] == item["chapter_count"]
    assert item["reading_now"] is True


def test_marking_everything_unread_clears_being_read(client, one_series):
    """GIVEN 읽기 시작한 웹툰"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][0]["id"], "page_index": 1})
    assert _item(client, sid)["reading_now"] is True

    """WHEN 전체 읽지 않음으로 표시하면(이어보기 위치도 같이 지워짐)"""
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": False})

    """THEN 다시 읽기 시작하지 않은 상태가 된다"""
    assert _item(client, sid)["reading_now"] is False


def test_saved_position_pointing_to_a_vanished_chapter_is_not_being_read(client, library, one_series):
    """GIVEN 2화를 읽는 중이던 웹툰(이어보기 위치가 2화를 가리킴)"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][1]["id"], "page_index": 1})
    assert _item(client, sid)["reading_now"] is True

    """WHEN 그 회차 파일의 이름이 바뀐 뒤 재스캔하면(회차 ID는 파일명으로 만들어서, 이름이 바뀌면 ID도 바뀜 -
    이어보기 위치와 읽음 기록은 이제 목록에 없는 옛 ID를 가리키게 된다)"""
    series_dir = library / "naver" / "웹툰"
    os.rename(series_dir / "002.zip", series_dir / "002 다시받음.zip")
    client.post("/api/rescan")
    item = _item(client, sid)

    """THEN 저장된 위치가 가리키는 회차가 이제 없으므로, 읽기 시작한 웹툰으로 세지 않는다(회차 목록에
    읽는 중 표시가 없는데 읽는 중 필터에만 걸리는 일이 없도록). 이름이 안 바뀐 1화의 읽음 기록은 그대로다."""
    assert item["reading_now"] is False
    assert item["unread_count"] == 2  # 3개 중 1화만 읽음(2화를 읽는 중이면 그 앞 1화는 읽음 처리됨)


def test_position_on_a_chapter_that_still_exists_stays_being_read_after_other_files_are_renamed(client, library, one_series):
    """GIVEN 2화를 읽는 중이던 웹툰"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][1]["id"], "page_index": 1})

    """WHEN 읽던 회차가 아닌 다른 회차 파일만 이름이 바뀌면"""
    series_dir = library / "naver" / "웹툰"
    os.rename(series_dir / "003.zip", series_dir / "003 다시받음.zip")
    client.post("/api/rescan")

    """THEN 읽던 회차는 그대로 있으니 계속 읽는 중이다"""
    assert _item(client, sid)["reading_now"] is True


def test_being_read_is_also_reported_for_shared_profiles(library, monkeypatch):
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
    assert before["reading_now"] is False
    assert after["reading_now"] is True


# ---------------------------------------------------------------------------
# 전부 읽음 처리된 웹툰의 회차를 다시 열어서 읽는 경우, 그리고 목록과 사이드바의 일치
# ---------------------------------------------------------------------------


def _all_read_series(client, one_series):
    """전체 회차가 읽음 처리된(완독) 상태로 만든다."""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": True})
    return sid


def test_fully_read_series_stays_finished_while_nothing_is_being_reread(client, one_series):
    """GIVEN 전부 읽은 웹툰(마지막 회차까지 완독 처리됨)"""
    sid = _all_read_series(client, one_series)

    item = _item(client, sid)

    """THEN 완독이고, 지금 읽고 있는 회차는 없다"""
    assert item["unread_count"] == 0
    assert item["reading_now"] is False
    assert item["progress_display"] == "완독"


def test_fully_read_series_is_being_read_again_when_a_chapter_is_reopened(client, one_series):
    """GIVEN 전부 읽은 웹툰(예: 학사검전 - 150화까지 읽음 기록이 있고 마지막 회차도 읽음 처리됨)"""
    sid = _all_read_series(client, one_series)
    last = one_series["chapters"][-1]

    """WHEN 마지막 회차를 다시 열어서 읽기 시작하면(이어보기 위치가 그 회차의 중간으로 저장됨)"""
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": last["id"], "page_index": 1})
    item = _item(client, sid)
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]

    """THEN 회차 목록에 "읽는 중"으로 표시되는 것과 같이, 읽음 기록이 전부 있어도 읽는 중으로 본다"""
    assert [ch["id"] for ch in chapters if ch["reading"]] == [last["id"]]
    assert item["reading_now"] is True
    assert item["unread_count"] == 0
    """AND 카드에는 "완독"이 아니라 읽고 있는 회차가 보인다(읽는 중 목록에 "완독"이 적힌 카드가 나오면 모순)"""
    assert item["progress_display"] == f"{last['label']}/{last['label']}"


def test_marking_everything_read_again_finishes_the_reread(client, one_series):
    """GIVEN 다시 읽는 중인 완독 웹툰"""
    sid = _all_read_series(client, one_series)
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": one_series["chapters"][-1]["id"], "page_index": 1})
    assert _item(client, sid)["reading_now"] is True

    """WHEN 전체 읽음으로 표시하면"""
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": True})

    """THEN 다시 완독 상태가 된다"""
    item = _item(client, sid)
    assert item["reading_now"] is False
    assert item["progress_display"] == "완독"


def _scenario_untouched(client, sid, chapters):
    pass


def _scenario_reading_first(client, sid, chapters):
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[0]["id"], "page_index": 1})


def _scenario_reading_middle(client, sid, chapters):
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[1]["id"], "page_index": 2})


def _scenario_finished_everything(client, sid, chapters):
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": True})


def _scenario_reread_after_finishing(client, sid, chapters):
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": True})
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[-1]["id"], "page_index": 1})


def _scenario_unread_from_middle(client, sid, chapters):
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": True})
    client.put(f"/api/series/{sid}/read-state", json={"scope": "chapter", "read": False, "chapter_id": chapters[1]["id"]})


def _scenario_read_only_the_first_chapter(client, sid, chapters):
    client.put(f"/api/series/{sid}/read-state", json={"scope": "chapter", "read": True, "chapter_id": chapters[0]["id"]})


@pytest.mark.parametrize(
    "scenario",
    [
        _scenario_untouched,
        _scenario_reading_first,
        _scenario_reading_middle,
        _scenario_finished_everything,
        _scenario_reread_after_finishing,
        _scenario_unread_from_middle,
        _scenario_read_only_the_first_chapter,
    ],
)
def test_list_and_sidebar_always_agree_on_whether_something_is_being_read(client, one_series, scenario):
    """GIVEN 여러 가지 읽기 상황 / THEN 목록의 reading_now와 회차 목록의 "읽는 중" 표시 유무가 항상 같다
    (같은 판단을 두 곳에서 따로 하다가 어긋났던 문제의 재발 방지)"""
    sid = one_series["id"]
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": False})
    scenario(client, sid, one_series["chapters"])

    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    sidebar_shows_reading = any(ch["reading"] for ch in chapters)

    assert _item(client, sid)["reading_now"] is sidebar_shows_reading
