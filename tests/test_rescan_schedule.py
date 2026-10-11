"""
RESCAN_SCHEDULE(추가 옵션 - 숫자면 간격, 크론 표현식이면 그 일정) 회귀 테스트.
비어있으면 기존 RESCAN_INTERVAL_SECONDS 동작이 그대로 유지되어야 한다.
"""

from datetime import datetime

from app import services


def test_empty_schedule_falls_back_to_existing_interval_setting(monkeypatch):
    """GIVEN RESCAN_SCHEDULE을 아예 설정 안 했을 때(기존 배포와 동일한 상태)"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "")
    monkeypatch.setattr(services, "RESCAN_INTERVAL_SECONDS", 300)

    """THEN 기존 RESCAN_INTERVAL_SECONDS 그대로 동작한다(새 옵션이 기존 동작에 영향 없음)"""
    assert services.is_rescan_enabled() is True
    assert services.seconds_until_next_rescan() == 300


def test_empty_schedule_respects_existing_disable_behavior(monkeypatch):
    """GIVEN RESCAN_SCHEDULE은 비어있고 RESCAN_INTERVAL_SECONDS를 0으로(끄기) 뒀을 때"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "")
    monkeypatch.setattr(services, "RESCAN_INTERVAL_SECONDS", 0)

    """THEN 기존과 동일하게 비활성화 상태로 판단된다"""
    assert services.is_rescan_enabled() is False


def test_plain_number_schedule_behaves_like_interval_seconds(monkeypatch):
    """GIVEN RESCAN_SCHEDULE에 순수 숫자를 넣었을 때"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "600")
    monkeypatch.setattr(services, "RESCAN_INTERVAL_SECONDS", 7200)  # 기존 옵션은 무시되어야 함

    """THEN RESCAN_INTERVAL_SECONDS 대신 이 값이 간격으로 쓰인다"""
    assert services.is_rescan_enabled() is True
    assert services.seconds_until_next_rescan() == 600


def test_plain_number_zero_disables_rescan(monkeypatch):
    """GIVEN RESCAN_SCHEDULE에 0을 넣었을 때(기존 RESCAN_INTERVAL_SECONDS<=0과 같은 의미)"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "0")

    """THEN 자동 재스캔이 꺼진 것으로 판단된다"""
    assert services.is_rescan_enabled() is False


def test_cron_expression_schedule_computes_next_run_time(monkeypatch):
    """GIVEN RESCAN_SCHEDULE에 "매일 새벽 3시"를 뜻하는 크론 표현식을 넣었을 때"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "0 3 * * *")

    """THEN 항상 켜진 것으로 취급되고"""
    assert services.is_rescan_enabled() is True

    """AND 지금이 새벽 1시라면 다음 실행까지 정확히 2시간(7200초) 남았다고 계산된다"""
    fixed_now = datetime(2026, 1, 5, 1, 0, 0)  # 월요일 새벽 1시
    import app.services as services_module

    original_datetime = services_module.datetime

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(services_module, "datetime", _FixedDatetime)
    try:
        wait_seconds = services.seconds_until_next_rescan()
    finally:
        monkeypatch.setattr(services_module, "datetime", original_datetime)
    assert wait_seconds == 7200


def test_invalid_cron_expression_raises_so_caller_can_fall_back(monkeypatch):
    """GIVEN RESCAN_SCHEDULE에 숫자도 아니고 유효한 크론 표현식도 아닌 값을 넣었을 때"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "이건 크론도 숫자도 아님")

    """WHEN 다음 실행 시각을 계산하려 하면"""
    """THEN 예외가 나서, 호출하는 쪽(auto_rescan_loop)이 이를 감지하고 기본 주기로
    대체할 수 있게 한다(서버가 죽거나 재스캔이 영영 멈추면 안 되므로)"""
    import pytest

    with pytest.raises(Exception):
        services.seconds_until_next_rescan()


def test_auto_rescan_loop_falls_back_and_keeps_running_on_invalid_cron(library, monkeypatch):
    """GIVEN RESCAN_SCHEDULE이 잘못된 값이라 매번 예외가 나는 상황일 때"""
    import asyncio

    from unittest.mock import patch

    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "이건 크론도 숫자도 아님")
    monkeypatch.setattr(services, "RESCAN_INTERVAL_SECONDS", 0.01)  # 테스트가 빨리 끝나게

    scan_count = {"n": 0}

    async def fake_scan():
        scan_count["n"] += 1
        return {}, {}

    async def run_two_iterations_then_stop():
        with patch("app.services.scan_all_platforms_incrementally", fake_scan), patch(
            "app.services.log_scan_result"
        ), patch("app.services._precompute_after_scan", return_value=None):
            task = asyncio.create_task(services.auto_rescan_loop())
            await asyncio.sleep(0.1)  # 기본 주기(0.01초)로 몇 바퀴 돌 시간을 줌
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    """WHEN 루프를 잠깐 돌려보면"""
    asyncio.run(run_two_iterations_then_stop())

    """THEN 크론 파싱이 계속 실패해도(예외를 삼키고) 기본 주기로 대체해서 재스캔
    자체는 계속 반복 실행된다 - 서버가 멈추거나 죽지 않는다"""
    assert scan_count["n"] >= 2


# ---------------------------------------------------------------------------
# 여러 크론 표현식을 ";"(또는 줄바꿈)로 이어 쓰기
#
# 크론 한 줄은 분 필드와 시 필드가 독립적으로 곱해져서, "00:40, 03:47, 06:55"처럼 시각마다 분이
# 다른 일정은 한 줄로 표현할 수 없다. 그래서 여러 줄을 이어 쓸 수 있어야 한다.
# ---------------------------------------------------------------------------

# 다른 작업(예: 웹툰 다운로드)이 도는 시각 5분 뒤에 재스캔하는 일정
FIVE_MINUTES_AFTER_DOWNLOADS = "40 0,10,17 * * *;47 3,13,20 * * *;55 6 * * *"


def _next_run_after(monkeypatch, now):
    """now 시각 기준으로 다음 재스캔이 언제인지 돌려준다(시각을 고정해서 계산)."""
    import app.services as services_module

    original_datetime = services_module.datetime

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(services_module, "datetime", _FixedDatetime)
    try:
        wait_seconds = services.seconds_until_next_rescan()
    finally:
        monkeypatch.setattr(services_module, "datetime", original_datetime)
    from datetime import timedelta

    return now + timedelta(seconds=wait_seconds)


def test_multiple_cron_expressions_run_at_every_listed_time_in_order(monkeypatch):
    """GIVEN 시각마다 분이 다른 세 개의 크론 표현식을 ";"로 이어 쓴 일정"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", FIVE_MINUTES_AFTER_DOWNLOADS)
    assert services.is_rescan_enabled() is True

    """WHEN 하루 동안 "방금 한 번 돌고 난 직후" 시각마다 다음 실행을 물어보면"""
    day = datetime(2026, 1, 5)  # 월요일
    expected = [
        (datetime(2026, 1, 5, 0, 0), datetime(2026, 1, 5, 0, 40)),
        (datetime(2026, 1, 5, 0, 41), datetime(2026, 1, 5, 3, 47)),
        (datetime(2026, 1, 5, 3, 48), datetime(2026, 1, 5, 6, 55)),
        (datetime(2026, 1, 5, 6, 56), datetime(2026, 1, 5, 10, 40)),
        (datetime(2026, 1, 5, 10, 41), datetime(2026, 1, 5, 13, 47)),
        (datetime(2026, 1, 5, 13, 48), datetime(2026, 1, 5, 17, 40)),
        (datetime(2026, 1, 5, 17, 41), datetime(2026, 1, 5, 20, 47)),
        (datetime(2026, 1, 5, 20, 48), datetime(2026, 1, 6, 0, 40)),  # 하루가 지나면 다음 날 첫 일정
    ]
    for now, expected_next in expected:
        """THEN 세 표현식 중 가장 가까운 다음 시각이 나온다(하루 7번: 00:40 03:47 06:55 10:40 13:47 17:40 20:47)"""
        assert _next_run_after(monkeypatch, now) == expected_next, f"{now} 다음 실행"


def test_newline_separated_expressions_work_the_same_as_semicolons(monkeypatch):
    """GIVEN 같은 일정을 줄바꿈으로 이어 쓴 경우"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", FIVE_MINUTES_AFTER_DOWNLOADS.replace(";", "\n"))

    """THEN 결과가 똑같다"""
    assert _next_run_after(monkeypatch, datetime(2026, 1, 5, 4, 0)) == datetime(2026, 1, 5, 6, 55)


def test_stray_separators_and_spaces_are_ignored(monkeypatch):
    """GIVEN 끝에 ";"가 남았거나 공백이 섞인 경우"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", " 40 0,10,17 * * * ;  47 3,13,20 * * *; ")

    """THEN 빈 항목은 무시하고 정상적으로 계산한다"""
    assert _next_run_after(monkeypatch, datetime(2026, 1, 5, 1, 0)) == datetime(2026, 1, 5, 3, 47)


def test_one_invalid_expression_among_valid_ones_is_reported_not_silently_skipped(monkeypatch):
    """GIVEN 올바른 표현식 사이에 잘못된 것이 하나 섞인 일정"""
    monkeypatch.setattr(services, "RESCAN_SCHEDULE", "40 0,10,17 * * *;이건 잘못된 표현식")

    """THEN 일부만 조용히 무시하지 않고 예외를 내서, 호출하는 쪽(자동 재스캔 루프)이 알아채고 기본
    주기로 대체하게 한다(오타 때문에 일정의 절반이 말없이 빠지면 모르고 지나가게 되므로)"""
    import pytest

    with pytest.raises(Exception):
        services.seconds_until_next_rescan()
