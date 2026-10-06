"""
회차 라벨에서 "번호 부분"만 뽑는 chapter_number_part가, 번호 뒤 부제가 구분점 없이 공백으로
이어진 라벨에서도 목록 카드의 진행 표시("1화/32화")를 깨뜨리지 않는지 확인한다.
예전에는 " · "를 기준으로 잘랐기 때문에, 구분점을 없애면 부제까지 통째로 나오게 된다.
"""

import pytest

from app import services
from conftest import make_chapter_zip


@pytest.mark.parametrize(
    "label, expected",
    [
        ("100화 아스라이 스러지는 7", "100화"),  # 부제는 떼고 번호만
        ("444화 완결", "444화"),
        ("152화 후기", "152화"),
        ("51화 시즌2 마지막화", "51화"),
        ("Extra story 1화", "Extra story 1화"),  # 번호 앞의 부제/시즌 표시는 번호의 일부
        ("3부 233화 후기", "3부 233화"),
        ("100화", "100화"),  # 부제가 없으면 그대로
        # "N화"가 없는 라벨은 통째로가 번호 부분이다(기존 동작)
        ("Ep 1-1. BRAVE MAN", "Ep 1-1. BRAVE MAN"),
        ("프롤로그", "프롤로그"),
        ("세크메트의 분노 시즌1 완결", "세크메트의 분노 시즌1 완결"),
    ],
)
def test_number_part_is_everything_up_to_the_first_episode_number(label, expected):
    assert services.chapter_number_part(label) == expected


def test_progress_display_stays_short_when_chapters_have_subtitles(client, library):
    """GIVEN 회차마다 부제가 붙은 웹툰(번호 뒤에 공백으로 이어진 라벨)"""
    make_chapter_zip(str(library / "naver" / "웹툰" / "001 웹툰 1화 - 첫번째 이야기.zip"))
    make_chapter_zip(str(library / "naver" / "웹툰" / "002 웹툰 2화 - 두번째 이야기.zip"))
    client.post("/api/rescan")

    """WHEN 아직 하나도 안 읽은 상태로 목록을 조회하면"""
    item = client.get("/api/series").json()[0]

    """THEN 카드에는 부제 없이 "읽을 차례/마지막" 번호만 나온다"""
    assert item["progress_display"] == "1화/2화"
