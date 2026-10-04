"""
목록을 "정주행 중"(기본) / "나중에 읽기" 두 개로 나누는 기능(관리자 전용) 회귀 테스트.

핵심 약속 세 가지:
  1) 아무것도 안 한 시리즈(기존/새로 스캔된 것 전부)는 "정주행 중"이다 - 업데이트 직후
     목록이 텅 비어 보이는 일이 없어야 한다.
  2) 옮겨도 이어보기 위치와 읽음 기록은 전혀 건드리지 않는다. 읽어도 자동으로
     옮겨지지 않는다(나중에 읽기는 "천천히 아껴보는" 용도라서).
  3) 이 구분은 관리자 개인 정리용이라 공유 프로필에게는 기능도, 값도 보이지 않는다.
"""

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth, profiles
from conftest import make_chapter_zip


@pytest.fixture
def three_series(client, library):
    """웹툰A/B/C 세 개(각 2화)가 스캔된 상태. {제목: 시리즈ID}를 돌려준다."""
    for name in ("웹툰A", "웹툰B", "웹툰C"):
        make_chapter_zip(str(library / "naver" / name / "001.zip"))
        make_chapter_zip(str(library / "naver" / name / "002.zip"))
    client.post("/api/rescan")
    return {s["title"]: s["id"] for s in client.get("/api/series").json()}


def _list_of(client, series_id):
    return next(s["list"] for s in client.get("/api/series").json() if s["id"] == series_id)


def _move(client, series_ids, target):
    return client.put("/api/series-lists", json={"series_ids": series_ids, "list": target})


def test_every_series_starts_in_the_reading_list_by_default(client, three_series):
    """GIVEN 한 번도 목록을 옮겨본 적 없는 라이브러리"""
    """WHEN 시리즈 목록을 조회하면"""
    items = client.get("/api/series").json()

    """THEN 전부 "정주행 중"이다(기존 동작과 똑같이 전부 보이도록)"""
    assert len(items) == 3
    assert {item["list"] for item in items} == {"reading"}


def test_move_a_series_to_later_and_back(client, three_series):
    """GIVEN 정주행 중인 웹툰A"""
    a = three_series["웹툰A"]

    """WHEN 나중에 읽기로 옮기면"""
    r = _move(client, [a], "later")

    """THEN 웹툰A만 나중에 읽기가 되고, 나머지는 그대로다"""
    assert r.status_code == 200
    assert r.json() == {"moved": 1}
    assert _list_of(client, a) == "later"
    assert _list_of(client, three_series["웹툰B"]) == "reading"

    """AND 다시 정주행 중으로 옮기면 원래대로 돌아온다"""
    _move(client, [a], "reading")
    assert _list_of(client, a) == "reading"


def test_bulk_move_moves_exactly_the_given_series(client, three_series):
    """WHEN 두 개를 한꺼번에 나중에 읽기로 옮기면"""
    r = _move(client, [three_series["웹툰A"], three_series["웹툰C"]], "later")

    """THEN 그 두 개만 옮겨진다"""
    assert r.json() == {"moved": 2}
    assert _list_of(client, three_series["웹툰A"]) == "later"
    assert _list_of(client, three_series["웹툰B"]) == "reading"
    assert _list_of(client, three_series["웹툰C"]) == "later"


def test_moving_to_the_list_it_is_already_in_changes_nothing(client, three_series):
    """GIVEN 이미 나중에 읽기에 있는 웹툰A"""
    a = three_series["웹툰A"]
    _move(client, [a], "later")

    """WHEN 또 나중에 읽기로 옮기면"""
    r = _move(client, [a], "later")

    """THEN 에러 없이 처리되고, 실제로 바뀐 건 0건이다"""
    assert r.status_code == 200
    assert r.json() == {"moved": 0}
    assert _list_of(client, a) == "later"


def test_unknown_list_name_is_rejected(client, three_series):
    """WHEN 정의되지 않은 목록 이름으로 옮기려 하면"""
    r = _move(client, [three_series["웹툰A"]], "아무거나")

    """THEN 400으로 거절되고 아무것도 바뀌지 않는다"""
    assert r.status_code == 400
    assert _list_of(client, three_series["웹툰A"]) == "reading"


def test_unknown_series_ids_are_ignored_not_an_error(client, three_series):
    """WHEN 존재하지 않는 시리즈 ID가 섞여 있어도"""
    r = _move(client, [three_series["웹툰A"], "없는-시리즈-id"], "later")

    """THEN 실제로 있는 것만 옮기고, 없는 건 조용히 무시한다(유령 항목이 쌓이지 않음)"""
    assert r.status_code == 200
    assert r.json() == {"moved": 1}
    assert _list_of(client, three_series["웹툰A"]) == "later"


def test_moving_keeps_the_reading_position_and_read_marks(client, three_series):
    """GIVEN 웹툰A를 1화 2페이지째까지 읽고, 1화를 읽음 처리한 상태"""
    a = three_series["웹툰A"]
    chapters = client.get(f"/api/series/{a}/chapters").json()["chapters"]
    client.put(f"/api/series/{a}/progress", json={"chapter_id": chapters[0]["id"], "page_index": 1})
    client.put(
        f"/api/series/{a}/read-state",
        json={"scope": "chapter", "read": True, "chapter_id": chapters[0]["id"]},
    )
    before = next(s for s in client.get("/api/series").json() if s["id"] == a)
    position_before = client.get(f"/api/series/{a}/continue").json()

    """WHEN 나중에 읽기로 갔다가 다시 정주행 중으로 돌아오면"""
    assert _move(client, [a], "later").status_code == 200
    assert _list_of(client, a) == "later"  # 실제로 옮겨졌는지부터 확인(안 옮겨졌는데 통과하면 안 됨)
    assert _move(client, [a], "reading").status_code == 200

    """THEN 이어보기 위치와 안 읽은 회차 수가 옮기기 전과 똑같다"""
    after = next(s for s in client.get("/api/series").json() if s["id"] == a)
    assert client.get(f"/api/series/{a}/continue").json() == position_before
    assert after["unread_count"] == before["unread_count"]


def test_reading_a_later_series_does_not_move_it_back(client, three_series):
    """GIVEN 나중에 읽기에 둔 웹툰A"""
    a = three_series["웹툰A"]
    _move(client, [a], "later")
    chapters = client.get(f"/api/series/{a}/chapters").json()["chapters"]

    """WHEN 그 웹툰을 열어서 읽으면(진행률 저장)"""
    client.put(f"/api/series/{a}/progress", json={"chapter_id": chapters[0]["id"], "page_index": 1})

    """THEN 자동으로 정주행 중으로 옮겨지지 않는다(조금씩 꺼내 읽고 두는 용도)"""
    assert _list_of(client, a) == "later"


def test_list_survives_exclude_then_reinclude(client, three_series):
    """GIVEN 나중에 읽기에 둔 웹툰A"""
    a = three_series["웹툰A"]
    _move(client, [a], "later")

    """WHEN 그 폴더를 제외했다가 다시 포함하면"""
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})

    """THEN 시리즈 ID가 그대로라서 나중에 읽기 상태도 유지된다"""
    assert _list_of(client, a) == "later"


def test_list_is_included_in_backup_and_restored(client, three_series):
    """GIVEN 웹툰A를 나중에 읽기에 두고 백업을 받아둔 뒤, 다시 정주행 중으로 되돌렸을 때"""
    a = three_series["웹툰A"]
    _move(client, [a], "later")
    backup = client.get("/api/backup").json()
    _move(client, [a], "reading")
    assert _list_of(client, a) == "reading"

    """WHEN 그 백업으로 복원하면"""
    assert client.post("/api/restore", json=backup).status_code == 200

    """THEN 나중에 읽기 상태가 되살아난다"""
    assert _list_of(client, a) == "later"


# ---------------------------------------------------------------------------
# 공유 프로필: 기능도 값도 노출되지 않아야 한다
# ---------------------------------------------------------------------------


@pytest.fixture
def profile_ctx(library, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("PROFILES_ENABLED", "true")
    importlib.reload(main_module)

    make_chapter_zip(str(library / "naver" / "공유웹툰" / "001.zip"))

    with TestClient(main_module.app) as client:
        auth.init_schema()
        auth.ensure_admin_password_exists()
        profiles.init_schema()
        created = profiles.create_profile("딸")
        yield {"client": client, "token": created["token"], "profile_id": created["id"]}

    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_profile_cannot_move_series_between_lists(profile_ctx):
    """WHEN 프로필이 자기 링크로 목록 이동 API를 직접 호출하면"""
    r = profile_ctx["client"].put(
        f"/p/{profile_ctx['token']}/api/series-lists",
        json={"series_ids": ["아무id"], "list": "later"},
    )
    """THEN 그런 기능이 없는 것처럼 404로 막힌다"""
    assert r.status_code == 404


def test_profile_series_listing_does_not_expose_the_admins_lists(profile_ctx):
    """GIVEN 관리자가 나중에 읽기에 둔 웹툰을 프로필에도 허용해준 상태"""
    from app import catalog, db, services

    asyncio.run(services.scan_all_platforms_incrementally())
    series_id = next(iter(catalog.get_series_map()))
    db.set_later_series_ids({series_id})
    profiles.set_allowed_series(profile_ctx["profile_id"], [series_id])

    """WHEN 프로필이 자기 목록을 조회하면"""
    items = profile_ctx["client"].get(f"/p/{profile_ctx['token']}/api/series").json()

    """THEN 목록은 보이지만, 관리자의 정리 상태("list")는 응답에 아예 없다"""
    assert len(items) == 1
    assert "list" not in items[0]
