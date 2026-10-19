"""
목록 화면의 웹툰 상세 패널(회차 사이드바) 레이아웃 브라우저 테스트.

약속:
  1) 제목은 길어도 최대 2줄로 고정되고(넘치면 말줄임), 이동 링크와 한 줄에서 다투지 않는다.
  2) "원작 페이지 열기"와 "…로 이동"은 버튼이 아니라 같은 링크 모양으로, 줄거리가 아무리 길어도
     항상 보이는 고정 행에 한 줄로 놓인다.
  3) 메타 영역과 회차 목록은 이전처럼 각자 따로 스크롤된다.
"""

import pytest
from playwright.sync_api import Page, expect

from .conftest import LONG_TITLE

pytestmark = pytest.mark.usefixtures("webtoon_site")


def _open_panel(page: Page, site: str, title: str) -> None:
    page.goto(site)
    card = page.locator(".card", has_text=title)
    card.locator(".chapter-list-toggle").click()
    expect(page.locator("#chapterSidebar.open")).to_be_visible()
    # 슬라이드 인 애니메이션(transform 전환)이 끝난 뒤에야 위치를 비교할 수 있다
    page.wait_for_function(
        "() => ['none', 'matrix(1, 0, 0, 1, 0, 0)'].includes(getComputedStyle(document.getElementById('chapterSidebar')).transform)"
    )


def test_long_title_is_clamped_to_two_lines(webtoon_site, page: Page):
    """GIVEN 제목이 두 줄을 훌쩍 넘는 웹툰"""
    """WHEN 상세 패널을 열면"""
    _open_panel(page, webtoon_site, LONG_TITLE)

    """THEN 제목은 정확히 두 줄 높이로 잘리고, 전체 제목은 툴팁으로 남는다"""
    title = page.locator("#chapterSidebarTitle")
    metrics = title.evaluate(
        "el => ({h: el.clientHeight, sh: el.scrollHeight, lh: parseFloat(getComputedStyle(el).lineHeight)})"
    )
    assert metrics["sh"] > metrics["h"], "넘치는 부분이 잘려야 한다"
    assert metrics["h"] <= metrics["lh"] * 2 + 1, "두 줄을 넘으면 안 된다"
    expect(title).to_have_attribute("title", LONG_TITLE)


def test_original_and_move_links_share_one_line_and_look_alike(webtoon_site, page: Page):
    """GIVEN 원작 주소가 있는 웹툰의 상세 패널"""
    _open_panel(page, webtoon_site, LONG_TITLE)
    origin = page.locator("#chapterSidebarOrigin")
    move = page.locator("#chapterSidebarMove")
    expect(origin).to_be_visible()
    expect(move).to_be_visible()

    """THEN 두 링크는 같은 줄에 있다"""
    origin_box, move_box = origin.bounding_box(), move.bounding_box()
    assert abs(origin_box["y"] - move_box["y"]) < 2

    """AND 이동 링크도 버튼 모양(테두리/배경)이 아니라 원작 링크와 같은 글자 모양이다"""
    style = "el => { const s = getComputedStyle(el); return [s.color, s.fontSize, s.borderTopWidth, s.backgroundColor]; }"
    assert move.evaluate(style) == origin.evaluate(style)


def test_link_row_stays_visible_while_summary_scrolls(webtoon_site, page: Page):
    """GIVEN 줄거리가 매우 길어 메타 영역이 스크롤되는 웹툰의 상세 패널"""
    _open_panel(page, webtoon_site, LONG_TITLE)
    info = page.locator("#chapterSidebarInfo")
    assert info.evaluate("el => el.scrollHeight > el.clientHeight")
    link_row = page.locator("#chapterSidebarLinks")
    before = link_row.bounding_box()

    """WHEN 메타 영역을 맨 아래까지 스크롤해도"""
    info.evaluate("el => { el.scrollTop = el.scrollHeight; }")

    """THEN 링크 행은 제자리에 보이고, 원작 링크는 메타 영역 안에 들어 있지 않다"""
    assert link_row.bounding_box() == before
    expect(page.locator("#chapterSidebarOrigin")).to_be_visible()
    assert info.locator("a").count() == 0


def test_meta_area_never_scrolls_sideways(webtoon_site, page: Page):
    """GIVEN 줄거리에 공백 없는 긴 문자열(URL 등)이 들어 있는 웹툰의 상세 패널"""
    _open_panel(page, webtoon_site, LONG_TITLE)
    info = page.locator("#chapterSidebarInfo")

    """THEN 긴 문자열은 줄바꿈되어 가로 스크롤이 생기지 않는다(세로 스크롤만 있다)"""
    assert info.evaluate("el => el.scrollWidth <= el.clientWidth")
    assert info.evaluate("el => getComputedStyle(el).overflowX") == "hidden"


def test_chapter_list_scrolls_separately_from_meta(webtoon_site, page: Page):
    """GIVEN 상세 패널이 열려 있을 때"""
    _open_panel(page, webtoon_site, LONG_TITLE)

    """THEN 메타 영역과 회차 목록은 서로 다른 스크롤 컨테이너다"""
    overflow = "el => getComputedStyle(el).overflowY"
    assert page.locator("#chapterSidebarInfo").evaluate(overflow) == "auto"
    assert page.locator("#chapterSidebarList").evaluate(overflow) == "auto"


def test_move_link_moves_the_series_to_the_other_list(webtoon_site, page: Page):
    """GIVEN 정주행 중 목록의 웹툰 상세 패널(이동 링크는 "나중에 읽기로 이동")"""
    _open_panel(page, webtoon_site, "열렙전사")
    move = page.locator("#chapterSidebarMove")
    expect(move).to_have_text("나중에 읽기로 이동")

    """WHEN 이동 링크를 누르면"""
    move.click()

    """THEN 링크 문구가 반대 목록으로 바뀌고, 정주행 중 목록에서 사라진다"""
    expect(move).to_have_text("정주행 중으로 이동")
    expect(page.locator(".card", has_text="열렙전사")).to_have_count(0)


def test_original_link_is_hidden_when_series_has_no_original_url(webtoon_site, page: Page):
    """GIVEN info.xml이 없는 웹툰의 상세 패널"""
    _open_panel(page, webtoon_site, "열렙전사")

    """THEN 원작 링크는 숨겨지고 이동 링크만 보인다"""
    expect(page.locator("#chapterSidebarOrigin")).to_be_hidden()
    expect(page.locator("#chapterSidebarMove")).to_be_visible()
