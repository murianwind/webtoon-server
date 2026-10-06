"""
회차 파일명에 있는 완결 표시((完)/(완)/완결)가 사라지지 않고, 항상 라벨 끝에
"완결" 하나로 통일되어 남는지 확인하는 회귀 테스트. 예전에는 이 표시를 그냥
지워버려서 "세크메트의 분노 (完) 시즌1 완결" 같은 회차 제목에서 완결 표시
자체가 통째로 사라지는 문제가 있었다.
"""

from app.scan import parse_chapter_label


def test_completion_marker_in_middle_moves_to_the_end():
    """GIVEN 완결 표시가 제목 중간(한자 표기)과 끝(한글 표기)에 중복으로 있을 때"""
    _, label = parse_chapter_label(
        "049 퇴마록  세계편 세크메트의 분노 (完) 시즌1 완결", series_name="퇴마록 : 세계편"
    )
    """THEN 둘 다 지워지지 않고, 라벨 끝에 "완결" 하나로 합쳐진다"""
    assert label == "세크메트의 분노 시즌1 완결"


def test_completion_marker_hanja_only():
    """GIVEN 한자 표기 "(完)"만 있을 때"""
    _, label = parse_chapter_label("012 어떤웹툰 최종화 (完)", series_name="어떤웹툰")
    """THEN "완결"로 통일되어 끝에 남는다"""
    assert label == "최종화 완결"


def test_completion_marker_hangul_parens_only():
    """GIVEN "(완)" 표기만 있을 때"""
    _, label = parse_chapter_label("012 어떤웹툰 최종화 (완)", series_name="어떤웹툰")
    assert label == "최종화 완결"


def test_no_completion_marker_unaffected():
    """GIVEN 완결 표시가 전혀 없는 평범한 회차일 때"""
    _, label = parse_chapter_label("012 어떤웹툰 10화", series_name="어떤웹툰")
    """THEN 아무 영향 없이 그대로 나온다"""
    assert label == "10화"


def test_completion_marker_as_entire_remaining_title():
    """GIVEN 시리즈명을 떼고 나면 "완결"이라는 표시만 남는 극단적인 경우일 때"""
    _, label = parse_chapter_label("001 어떤웹툰 완결", series_name="어떤웹툰")
    """THEN 빈 제목이 되지 않고 "완결" 자체가 라벨이 된다"""
    assert label == "완결"


def test_completion_marker_removed_from_inside_parens_leaves_no_dangling_space():
    """GIVEN 완결 표시가 다른 텍스트와 같은 괄호 안에 있을 때(예: "(2부 완결)")"""
    _, label = parse_chapter_label(
        "083 해시의 신루 83화 어디에 있느냐？ (2부 완결)", series_name="해시의 신루"
    )
    """THEN 완결만 지워지고, 닫는 괄호 앞에 어색한 공백 없이 정리되어 끝에 "완결"이 붙는다"""
    assert label == "83화 어디에 있느냐 (2부) 완결"


def test_completion_marker_alone_in_parens_removes_empty_parens():
    """GIVEN 완결 표시가 괄호 안에 단독으로 있을 때(예: "(완결)")"""
    _, label = parse_chapter_label("0447_444화 (완결)#188")
    """THEN 완결을 지운 뒤 텅 빈 "()"까지 같이 정리되고, "444화 완결"로 표시된다(번호 뒤에는
    구분점 없이 공백으로 이어진다)"""
    assert label == "444화 완결"
