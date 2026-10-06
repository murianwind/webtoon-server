"""
회차 번호 뒤에 붙은 "덧붙임"(괄호 설명, "+ 후기", 완결 표시)이 회차 제목에서 어떻게
표시되는지에 대한 회귀 테스트.

- 번호 뒤에 붙는 것은 부제든 덧붙임이든 구분점 없이 번호에 공백으로 이어 쓴다:
  "100화 아스라이 스러지는", "444화 완결", "152화 후기", "51화 시즌2 마지막화".
- 완결 표시는 괄호 안/끝에 있을 때만 끝으로 정리하고, "완결 후기"처럼 제목의 일부인
  "완결"은 건드리지 않는다.
"""

import pytest

from app.scan import parse_chapter_label


@pytest.mark.parametrize(
    "stem, expected",
    [
        # 완결 표시만 괄호로 붙은 경우 - 번호와 완결 사이에 구분점이 없어야 한다
        ("0447_444화 (완결)#188", "444화 완결"),
        ("0447_444화 완결", "444화 완결"),
        ("0447_444화 - 완결", "444화 완결"),
        # "+"로 붙은 보너스 표기 - "+" 기호는 빼고 공백으로 잇는다
        ("0154_152화 + 후기", "152화 후기"),
        # 괄호로 통째로 감싼 설명 - 괄호를 풀고 공백으로 잇는다
        ("0052_51화(시즌2 마지막화)", "51화 시즌2 마지막화"),
        # 제목의 일부인 "완결"은 제자리에 둔다(끝으로 옮기면 "후기 완결"이 되어버림)
        ("0448_완결 후기", "완결 후기"),
        # 이미 끝에 있는 "완결"은 그대로
        ("0449_후기 완결", "후기 완결"),
    ],
)
def test_annotation_suffixes_are_attached_cleanly(stem, expected):
    """GIVEN 번호 뒤에 덧붙임이 붙은 실제 파일명 / THEN 사용자가 기대한 깔끔한 제목이 된다"""
    _, label = parse_chapter_label(stem)
    assert label == expected


def test_real_subtitle_is_joined_to_the_number_without_a_dot():
    """GIVEN 진짜 부제가 "-"로 이어진 회차"""
    _, label = parse_chapter_label("103 마법사랑해 100화 - 아스라이 스러지는 (7)", series_name="마법사랑해")
    """THEN 번호와 부제 사이에 구분점 없이 공백으로 이어진다"""
    assert label == "100화 아스라이 스러지는 7"


def test_completion_inside_parens_with_subtitle_moves_marker_to_the_end():
    """GIVEN 부제 뒤 괄호 안에 완결이 함께 들어있는 회차"""
    _, label = parse_chapter_label("083 해시의 신루 83화 어디에 있느냐？ (2부 완결)", series_name="해시의 신루")
    assert label == "83화 어디에 있느냐 (2부) 완결"
