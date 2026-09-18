"""
SLOW_PLATFORMS로 지정된 플랫폼(rclone 등 원격 마운트)은 로컬보다 더 넉넉한 스캔
타임아웃을 쓰는지 확인하는 회귀 테스트.
"""

from app import services


def test_slow_platform_gets_longer_timeout_than_local(monkeypatch):
    """GIVEN aaa_slow가 SLOW_PLATFORMS로 지정되어 있을 때"""
    monkeypatch.setenv("SLOW_PLATFORMS", "aaa_slow")

    """THEN 로컬 플랫폼은 기본(짧은) 타임아웃, 느린 플랫폼은 훨씬 긴 타임아웃을 받는다"""
    assert services._scan_timeout_for("zzz_local") == services.SERIES_SCAN_TIMEOUT_SECONDS
    assert services._scan_timeout_for("aaa_slow") == services.SLOW_PLATFORM_SCAN_TIMEOUT_SECONDS
    assert services.SLOW_PLATFORM_SCAN_TIMEOUT_SECONDS > services.SERIES_SCAN_TIMEOUT_SECONDS


def test_no_slow_platforms_configured_everything_uses_default_timeout(monkeypatch):
    """GIVEN SLOW_PLATFORMS가 아예 지정 안 되어 있을 때"""
    monkeypatch.delenv("SLOW_PLATFORMS", raising=False)

    """THEN 어떤 플랫폼이든 기본 타임아웃을 쓴다(느린 플랫폼으로 분류될 게 없으므로)"""
    assert services._scan_timeout_for("아무플랫폼") == services.SERIES_SCAN_TIMEOUT_SECONDS
