"""
마지막 화를 완독한 뒤 새 화가 추가되는 시나리오 회귀 테스트.
"""

from conftest import make_chapter_zip


def test_completed_last_chapter_stays_read_after_new_chapter_added(client, library):
    """GIVEN 3화까지 있고, 3화(마지막 화)를 끝까지 다 읽었을 때"""
    for n in [1, 2, 3]:
        make_chapter_zip(str(library / "naver" / "테스트웹툰" / f"{n:03d} {n}화.zip"), page_count=3)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[2]["id"], "page_index": 2})

    """WHEN(완독 직후) 회차 목록을 보면 3화는 정확히 "읽음"으로 나온다"""
    before = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert before[2]["read"] is True
    assert before[2]["reading"] is False

    """AND 4화가 새로 추가되고 재스캔되면"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "004 4화.zip"), page_count=3)
    client.post("/api/rescan")

    """THEN 3화는 여전히 "읽음"이어야 한다(포인터가 아직 3화를 가리키고 있어도,
    완독 특수값이므로 "읽는 중"으로 잘못 보이면 안 됨) - 새로 추가된 4화는 안읽음"""
    after = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert after[2]["read"] is True
    assert after[2]["reading"] is False
    assert after[3]["read"] is False
    assert after[3]["reading"] is False


def test_normal_in_progress_chapter_still_shows_as_reading(client, library):
    """GIVEN 회차가 3개 있고, 마지막 화가 아닌 회차를 보는 중일 때(완독 아님)"""
    for n in [1, 2, 3]:
        make_chapter_zip(str(library / "naver" / "테스트웹툰" / f"{n:03d} {n}화.zip"), page_count=3)
    client.post("/api/rescan")
    sid = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{sid}/chapters").json()["chapters"]

    """WHEN 2화를 보는 중으로 저장하면(완독 특수값이 아닌 일반 페이지 위치)"""
    client.put(f"/api/series/{sid}/progress", json={"chapter_id": chapters[1]["id"], "page_index": 0})

    """THEN 2화는 정상적으로 "읽는 중"으로 나온다 - 이 회귀 테스트가 이번 수정으로
    "읽는 중" 표시 자체가 망가지지 않았는지 확인한다"""
    result = client.get(f"/api/series/{sid}/chapters").json()["chapters"]
    assert result[1]["reading"] is True
    assert result[1]["read"] is False
