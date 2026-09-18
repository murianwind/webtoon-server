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


def test_next_generation_skips_while_previous_generation_still_running(library):
    """GIVEN 이전 세대(재스캔1)의 사전계산이 아직 끝나지 않았을 때(느린 원격 마운트 등으로
    한 번의 사전계산이 재스캔 주기보다 오래 걸리는 상황을 흉내냄)"""
    concurrently_active = 0
    max_concurrent = 0

    async def slow_overlap():
        nonlocal concurrently_active, max_concurrent
        concurrently_active += 1
        max_concurrent = max(max_concurrent, concurrently_active)
        await asyncio.sleep(0.1)
        concurrently_active -= 1

    async def slow_covers():
        nonlocal concurrently_active, max_concurrent
        concurrently_active += 1
        max_concurrent = max(max_concurrent, concurrently_active)
        await asyncio.sleep(0.1)
        concurrently_active -= 1

    async def run_two_overlapping_generations():
        with patch("app.services.overlap.precompute_overlaps", slow_overlap), patch(
            "app.services.precompute_covers", slow_covers
        ):
            """WHEN 세대1이 끝나기 전에(사전계산 중간에) 세대2가 재스캔으로 또 시작되면"""
            task1 = asyncio.create_task(services._precompute_after_scan())
            await asyncio.sleep(0.02)  # 세대1이 overlap 단계를 지나 covers 단계에 들어갈 시간을 줌
            task2 = asyncio.create_task(services._precompute_after_scan())
            await asyncio.gather(task1, task2)

    asyncio.run(run_two_overlapping_generations())

    """THEN 세대2는 락이 걸려있는 걸 보고 조용히 건너뛰어서, 실제 동시 실행 인원은
    항상 1을 넘지 않는다(겹치는 순간이 전혀 없었다는 뜻)"""
    assert max_concurrent == 1

