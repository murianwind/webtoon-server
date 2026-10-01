"""
회차 제목에서 "숫자-숫자" 패턴을 날짜(예: "6-28")로 보고 지우는 규칙이, "Ep 1-1"
같은 영문 에피소드-파트 번호까지 같이 지워버리는 문제가 있었다. 영문 바로 뒤에
붙은 경우는 에피소드 번호로 보고 보존해야 한다.
"""

from app.scan import parse_chapter_label


def test_episode_part_number_after_english_marker_is_preserved():
    """GIVEN "Ep 1-1", "Ep 1-2"처럼 영문 에피소드 표시 뒤에 파트 번호가 붙은
    회차들이 여러 개 있을 때(실제로 전부 "Ep . BRAVE MAN"으로 뭉개지던 사례)"""
    cases = [
        ("0002_Ep 1-1. BRAVE MAN", "Ep 1-1. BRAVE MAN"),
        ("0003_Ep 1-2. BRAVE MAN", "Ep 1-2. BRAVE MAN"),
        ("0006_Ep 1-5. BRAVE MAN", "Ep 1-5. BRAVE MAN"),
        ("0007_Ep 2-1. 좋아요", "Ep 2-1. 좋아요"),
        ("0011_Ep 2-5. 좋아요", "Ep 2-5. 좋아요"),
    ]
    for stem, expected_label in cases:
        """THEN 번호가 지워지지 않아서, 서로 다른 회차끼리 라벨로 구분이 된다"""
        _, label = parse_chapter_label(stem, series_name="모범택시 [19세 완전판]")
        assert label == expected_label


def test_episode_part_number_preserved_even_with_extra_spaces():
    """GIVEN 영문 표시와 번호 사이에 공백이 여러 개 있을 때"""
    _, label = parse_chapter_label("012 어떤웹툰 Ep  1-1 공백두개", series_name="어떤웹툰")
    """THEN 공백 개수와 무관하게 역시 보존된다"""
    assert "1-1" in label


def test_plain_date_like_number_is_still_removed():
    """GIVEN 영문 마커 없이 순수하게 날짜로 보이는 "숫자-숫자"만 있을 때(원래 이
    규칙이 지우려던 대상)"""
    _, label = parse_chapter_label("029 썸웬투나잇 6-28", series_name="썸웬투나잇")
    """THEN 기존 그대로 지워진다(이 테스트가 깨지면 날짜 제거 기능 자체가 고장난 것)"""
    assert "6-28" not in label
