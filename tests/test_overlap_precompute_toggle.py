"""
"화 전환 겹침 자동 감지" 사전계산을 설정으로 껐을 때, 사전계산 백그라운드 작업만
건너뛰고 실시간(그 자리에서 계산) API는 계속 정상 동작하는지 확인하는 회귀 테스트.
"""

import asyncio
from unittest.mock import patch

from app import db, services
from conftest import make_chapter_zip


def test_precompute_skips_overlap_when_disabled(library):
    """GIVEN 설정에서 겹침 사전계산을 꺼뒀을 때"""
    db.set_setting("overlap_precompute_enabled", "false")

    overlap_called = []
    covers_called = []

    async def fake_overlap():
        overlap_called.append(1)

    async def fake_covers():
        covers_called.append(1)

    """WHEN 스캔 후 사전계산을 실행하면"""
    with patch("app.services.overlap.precompute_overlaps", fake_overlap), patch(
        "app.services.precompute_covers", fake_covers
    ):
        asyncio.run(services._precompute_after_scan())

    """THEN 겹침 계산은 건너뛰고, 커버 생성은 그대로 실행된다(이 설정과 무관)"""
    assert overlap_called == []
    assert covers_called == [1]


def test_precompute_runs_overlap_when_enabled_or_unset(library):
    """GIVEN 설정을 아예 안 건드렸을 때(기본값)"""
    overlap_called = []

    async def fake_overlap():
        overlap_called.append(1)

    async def fake_covers():
        pass

    """WHEN 사전계산을 실행하면"""
    with patch("app.services.overlap.precompute_overlaps", fake_overlap), patch(
        "app.services.precompute_covers", fake_covers
    ):
        asyncio.run(services._precompute_after_scan())

    """THEN 기본값은 켜짐이라 겹침 계산도 정상 실행된다(기존 동작 유지)"""
    assert overlap_called == [1]


def test_realtime_overlap_endpoint_still_works_when_precompute_disabled(client, library):
    """GIVEN 사전계산을 꺼뒀고, 아직 아무 화도 계산된 적 없는 시리즈가 있을 때"""
    db.set_setting("overlap_precompute_enabled", "false")
    make_chapter_zip(str(library / "naver" / "실시간테스트" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "실시간테스트" / "002.zip"))
    client.post("/api/rescan")

    series_id = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{series_id}/chapters").json()["chapters"]

    """WHEN 두 번째 화의 겹침 정보를 조회하면(사전계산이 안 해뒀어도)"""
    r = client.get(f"/api/chapters/{chapters[1]['id']}/overlap")

    """THEN 그 자리에서 계산되어 정상 응답한다 - 사전계산 설정과 무관하게 리더 기능
    자체는 항상 동작해야 한다"""
    assert r.status_code == 200
    assert "skip_pages" in r.json()
