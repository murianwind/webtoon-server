"""
겹침 계산(OpenCV)과 커버 생성(Pillow)이 절대 동시에 실행되지 않는지 확인하는
회귀 테스트. 두 작업이 동시에 실행되면 서로 다른 스레드에서 네이티브 이미지 처리
코드가 동시에 실행되어, 실제 배포 환경에서 간헐적으로 세그멘테이션 폴트가 나는
문제가 있었다(파이썬 예외가 아니라서 로그에 흔적도 안 남고 프로세스가 조용히 죽음).
"""

import asyncio
from unittest.mock import patch

from app import services


def test_overlap_and_covers_never_run_concurrently(library):
    """GIVEN 겹침 계산과 커버 생성 둘 다 시간이 걸리는 작업일 때"""
    currently_running = set()
    overlap_ran_while_covers_ran = False

    async def fake_overlap():
        nonlocal overlap_ran_while_covers_ran
        currently_running.add("overlap")
        if "covers" in currently_running:
            overlap_ran_while_covers_ran = True
        await asyncio.sleep(0.05)
        currently_running.discard("overlap")

    async def fake_covers():
        nonlocal overlap_ran_while_covers_ran
        currently_running.add("covers")
        if "overlap" in currently_running:
            overlap_ran_while_covers_ran = True
        await asyncio.sleep(0.05)
        currently_running.discard("covers")

    """WHEN 스캔 후 사전계산을 실행하면(_precompute_after_scan)"""
    with patch("app.services.overlap.precompute_overlaps", fake_overlap), patch(
        "app.services.precompute_covers", fake_covers
    ):
        asyncio.run(services._precompute_after_scan())

    """THEN 둘이 겹치는(동시에 실행되는) 순간이 한 번도 없어야 한다"""
    assert overlap_ran_while_covers_ran is False


def test_precompute_after_scan_runs_overlap_before_covers(library):
    """GIVEN 두 작업의 실행 순서를 기록할 때"""
    order = []

    async def fake_overlap():
        order.append("overlap")

    async def fake_covers():
        order.append("covers")

    """WHEN _precompute_after_scan을 실행하면"""
    with patch("app.services.overlap.precompute_overlaps", fake_overlap), patch(
        "app.services.precompute_covers", fake_covers
    ):
        asyncio.run(services._precompute_after_scan())

    """THEN 겹침 계산이 먼저, 커버 생성이 그다음 순서로 실행된다"""
    assert order == ["overlap", "covers"]

