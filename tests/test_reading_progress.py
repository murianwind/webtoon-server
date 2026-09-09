"""
이어보기, 진행률 저장, 회차 읽음/안읽음 관련 회귀 테스트.
"""

from conftest import make_chapter_zip


def _setup_three_chapters(library):
    for n in [1, 2, 3]:
        make_chapter_zip(str(library / "naver" / "테스트웹툰" / f"{n:03d} {n}화.zip"))


def test_continue_reading_starts_at_first_chapter_when_nothing_read(client, library):
    """GIVEN 회차가 3개 있고 아무것도 안 읽었을 때"""
    _setup_three_chapters(library)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]

    """WHEN 이어보기를 조회하면"""
    result = client.get(f"/api/series/{sid}/continue").json()

    """THEN 1화 0페이지부터 시작한다"""
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert result["chapter_id"] == chapters[0]["id"]
    assert result["page_index"] == 0


def test_save_progress_marks_earlier_chapters_as_read(client, library):
    """GIVEN 3개 회차가 있을 때"""
    _setup_three_chapters(library)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]

    """WHEN 2화를 스크롤로 보고 있다고 진행률을 저장하면"""
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[1]["id"], "page_index": 0})

    """THEN 1화는 읽음, 2화는 읽는 중, 3화는 안읽음으로 표시된다"""
    updated = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert updated[0]["read"] is True
    assert updated[1]["read"] is False and updated[1]["reading"] is True
    assert updated[2]["read"] is False and updated[2]["reading"] is False


def test_toggle_unread_on_currently_reading_chapter_moves_continue_position_back(client, library):
    """GIVEN 15화를 읽는 중인 상태에서 14화까지 이미 읽은 웹툰이 있을 때(회차 15개)"""
    for n in range(1, 16):
        make_chapter_zip(str(library / "naver" / "테스트웹툰" / f"{n:03d} {n}화.zip"))
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[14]["id"], "page_index": 5})

    """WHEN 사이드바에서 15화를 두 번, 14화를 한 번 토글하면(둘 다 안읽음이 됨)"""
    client.post(f"/api/series/{sid}/chapters/{chapters[14]['id']}/toggle-read")
    client.post(f"/api/series/{sid}/chapters/{chapters[14]['id']}/toggle-read")
    client.post(f"/api/series/{sid}/chapters/{chapters[13]['id']}/toggle-read")

    """THEN 이어보기 위치도 14화로 같이 당겨지고, 14화가 "읽는 중"으로 표시된다"""
    cont = client.get(f"/api/series/{sid}/continue").json()
    assert cont["chapter_id"] == chapters[13]["id"]
    updated = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert updated[13]["reading"] is True
    assert updated[13]["read"] is False


def test_reaching_last_page_of_last_chapter_marks_series_fully_read(client, library):
    """GIVEN 마지막 회차(3화, 3페이지)가 있을 때"""
    _setup_three_chapters(library)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]

    """WHEN 마지막 화의 마지막 페이지까지 도달하면"""
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[2]["id"], "page_index": 2})

    """THEN 전체가 완독 처리된다(다음 화가 없어 "읽는 중" 신호가 안 오므로 이 시점에 완독 처리해야 함)"""
    series = client.get("/api/series").json()[0]
    assert series["progress_display"] == "완독"


def test_read_state_mark_all_read_then_unread(client, library):
    """GIVEN 회차 3개가 있을 때"""
    _setup_three_chapters(library)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]

    """WHEN 전체읽음을 누르면"""
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": True})
    """THEN 완독으로 표시된다"""
    assert client.get("/api/series").json()[0]["progress_display"] == "완독"

    """WHEN 다시 전체안읽음을 누르면"""
    client.put(f"/api/series/{sid}/read-state", json={"scope": "all", "read": False})
    """THEN 진행률이 초기화된다(1화부터 다시)"""
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert all(not c["read"] for c in chapters)
