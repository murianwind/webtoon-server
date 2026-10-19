"""
목록 화면 검색 필드 브라우저 테스트.

약속:
  1) 검색어가 있을 때만 필드 안에 ✕가 보이고, 누르면 검색어와 검색 결과가 초기화된다.
  2) 탭(정주행 중 / 나중에 읽기)을 눌러 옮기면 검색이 초기화된다. 단, 지금 탭엔 결과가 없고
     눌러 옮길 탭에만 있어서 숫자 배지가 강조된 경우(검색 결과를 찾아 가는 중)에는 유지한다.
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.usefixtures("webtoon_site")

SEARCH = "#searchInput"
CLEAR = "#searchClear"


def _visit(page: Page, site: str) -> None:
    page.goto(site)
    expect(page.locator(".card")).to_have_count(2)  # 정주행 중 2개가 뜰 때까지 대기


def test_clear_button_appears_only_while_there_is_a_query(webtoon_site, page: Page):
    """GIVEN 빈 검색 필드"""
    _visit(page, webtoon_site)
    expect(page.locator(CLEAR)).to_be_hidden()

    """WHEN 검색어를 입력하면"""
    page.fill(SEARCH, "열렙")

    """THEN ✕가 나타나고, 지우면 다시 사라진다"""
    expect(page.locator(CLEAR)).to_be_visible()
    page.fill(SEARCH, "")
    expect(page.locator(CLEAR)).to_be_hidden()


def test_clear_button_resets_query_and_results(webtoon_site, page: Page):
    """GIVEN 검색해서 결과가 걸러진 상태"""
    _visit(page, webtoon_site)
    page.fill(SEARCH, "열렙")
    expect(page.locator(".card")).to_have_count(1)

    """WHEN ✕를 누르면"""
    page.click(CLEAR)

    """THEN 검색어가 지워지고 전체 목록이 돌아오며, 입력창에 포커스가 간다"""
    expect(page.locator(SEARCH)).to_have_value("")
    expect(page.locator(".card")).to_have_count(2)
    expect(page.locator(CLEAR)).to_be_hidden()
    expect(page.locator(SEARCH)).to_be_focused()
    assert page.evaluate("Object.values(localStorage).includes('열렙')") is False


def test_switching_tab_clears_the_query(webtoon_site, page: Page):
    """GIVEN 정주행 중에서 "열렙"을 검색 중(결과가 있으니 나중에 읽기 배지는 강조되지 않음)"""
    _visit(page, webtoon_site)
    page.fill(SEARCH, "열렙")

    """WHEN 나중에 읽기 탭을 누르면"""
    page.click('.list-tab[data-list="later"]')

    """THEN 검색이 초기화되고 나중에 읽기의 전체 목록이 보인다"""
    expect(page.locator(SEARCH)).to_have_value("")
    expect(page.locator(CLEAR)).to_be_hidden()
    expect(page.locator(".card")).to_have_count(1)
    expect(page.locator(".card", has_text="묵향 다크레이디")).to_be_visible()


def test_switching_to_highlighted_tab_keeps_the_query(webtoon_site, page: Page):
    """GIVEN 정주행 중에서 "묵향"을 검색 - 여기엔 없고 나중에 읽기에만 있어 그 탭 배지가 강조됨"""
    _visit(page, webtoon_site)
    page.fill(SEARCH, "묵향")
    expect(page.locator('.list-tab[data-list="later"] .list-tab-count.hit')).to_be_visible()

    """WHEN 강조된 나중에 읽기 탭을 누르면"""
    page.click('.list-tab[data-list="later"]')

    """THEN 검색이 유지되어 찾던 웹툰이 바로 보인다"""
    expect(page.locator(SEARCH)).to_have_value("묵향")
    expect(page.locator(".card", has_text="묵향 다크레이디")).to_be_visible()
