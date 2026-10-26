"""
"시리즈 폴더 / 백업" 패널의 목록 행 여백 브라우저 테스트.

약속: 제목이 길어도 제목과 오른쪽 버튼(제외 / 다시 추가) 사이에 최소 8px 여백이 있고,
제목이 버튼 밑으로 파고들지 않는다.
"""

import httpx
import pytest
from playwright.sync_api import Page, expect

from .conftest import LONG_TITLE

MIN_GAP_PX = 8

pytestmark = pytest.mark.usefixtures("webtoon_site")


def _row_gaps(page: Page, list_selector: str) -> list[float]:
    """목록의 각 행에서 제목 오른쪽 끝과 버튼 왼쪽 끝 사이 거리를 돌려준다."""
    return page.evaluate(
        """(selector) => [...document.querySelectorAll(selector + ' .lib-row')].map(row => {
            const name = row.querySelector('.lib-name').getBoundingClientRect();
            const button = row.querySelector('.lib-action-btn').getBoundingClientRect();
            return button.left - name.right;
        })""",
        list_selector,
    )


def _open_settings(page: Page, site: str) -> None:
    page.goto(site)
    page.locator("#settingsBtn").click()
    expect(page.locator("#settingsPanel.open")).to_be_visible()
    page.wait_for_function(
        "() => ['none', 'matrix(1, 0, 0, 1, 0, 0)'].includes(getComputedStyle(document.getElementById('settingsPanel')).transform)"
    )


def test_included_folder_rows_keep_a_gap_between_title_and_button(webtoon_site, page: Page):
    """GIVEN 긴 제목의 폴더를 포함한 스캔 중 폴더 목록이 있을 때"""
    _open_settings(page, webtoon_site)
    expect(page.locator("#includedFolderList .lib-row").first).to_be_visible()

    """WHEN 각 행의 제목과 버튼 사이 거리를 재면"""
    gaps = _row_gaps(page, "#includedFolderList")

    """THEN 모두 최소 여백 이상이다"""
    assert len(gaps) >= 3
    assert all(gap >= MIN_GAP_PX for gap in gaps), gaps


def test_excluded_folder_rows_keep_a_gap_between_title_and_button(webtoon_site, page: Page):
    """GIVEN 긴 제목의 폴더를 제외해 '제외된 폴더' 목록에 올려 두었을 때"""
    httpx.post(f"{webtoon_site}/api/series-folders/exclude", json={"platform": "카카오", "series": LONG_TITLE})
    _open_settings(page, webtoon_site)
    expect(page.locator("#excludedFolderList .lib-row").first).to_be_visible()

    """WHEN 제목과 '다시 추가' 버튼 사이 거리를 재면"""
    gaps = _row_gaps(page, "#excludedFolderList")

    """THEN 최소 여백 이상이다"""
    assert gaps and all(gap >= MIN_GAP_PX for gap in gaps), gaps
