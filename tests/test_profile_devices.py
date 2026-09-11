"""
app/profile_devices.py 회귀 테스트.
"""

import time

from app import profile_devices as devices


def test_first_two_devices_register_directly(library):
    """GIVEN 등록된 기기가 없을 때"""
    devices.init_schema()
    assert devices.count_devices("p1") == 0

    """WHEN 기기를 2개까지 등록하면"""
    devices.register_device("p1", "dev-a", "Chrome · Windows")
    devices.register_device("p1", "dev-b", "Safari · iPhone")

    """THEN 둘 다 등록된 상태가 된다"""
    assert devices.count_devices("p1") == 2
    assert devices.is_device_registered("p1", "dev-a") is True
    assert devices.is_device_registered("p1", "dev-b") is True


def test_approve_replaces_least_recently_seen_device(library):
    """GIVEN 기기 2대가 이미 등록되어 있고, dev-a가 dev-b보다 더 오래 안 쓰였을 때"""
    devices.init_schema()
    devices.register_device("p1", "dev-a", "기기A")
    time.sleep(0.01)
    devices.register_device("p1", "dev-b", "기기B")
    devices.touch_device("p1", "dev-b")  # dev-b를 최근에 씀으로 갱신

    """WHEN 3번째 기기(dev-c)의 대기 요청을 승인하면"""
    devices.create_pending_request("p1", "dev-c", "기기C")
    devices.approve_request("p1", "dev-c")

    """THEN 더 오래 안 쓰인 dev-a가 지워지고, dev-b와 dev-c가 남는다"""
    assert devices.is_device_registered("p1", "dev-a") is False
    assert devices.is_device_registered("p1", "dev-b") is True
    assert devices.is_device_registered("p1", "dev-c") is True
    assert devices.count_devices("p1") == 2


def test_duplicate_pending_request_does_not_create_new_row(library):
    """GIVEN 이미 대기 요청이 있을 때"""
    devices.init_schema()
    devices.create_pending_request("p1", "dev-a", "기기A")
    first = devices.get_pending_request("p1", "dev-a")

    """WHEN 같은 기기로 다시 요청을 시도하면(화면 닫고 재접속 등)"""
    devices.create_pending_request("p1", "dev-a", "기기A(다른 라벨)")

    """THEN 기존 요청이 그대로 유지된다(중복 안 쌓임, 라벨도 안 바뀜)"""
    second = devices.get_pending_request("p1", "dev-a")
    assert first == second


def test_requests_and_devices_are_isolated_per_profile(library):
    """GIVEN 프로필 A에 기기/요청이 있을 때"""
    devices.init_schema()
    devices.register_device("A", "dev-1", "기기1")
    devices.create_pending_request("A", "dev-2", "기기2")

    """THEN 프로필 B는 전혀 영향받지 않는다"""
    assert devices.count_devices("B") == 0
    assert devices.get_pending_request("B", "dev-2") is None


def test_delete_all_data_for_profile_clears_devices_and_requests(library):
    """GIVEN 기기/요청이 있는 프로필일 때"""
    devices.init_schema()
    devices.register_device("p1", "dev-a", "기기A")
    devices.create_pending_request("p1", "dev-b", "기기B")

    """WHEN 프로필 삭제에 맞춰 정리하면"""
    devices.delete_all_data_for_profile("p1")

    """THEN 기기/요청 둘 다 사라진다"""
    assert devices.count_devices("p1") == 0
    assert devices.get_pending_request("p1", "dev-b") is None


def test_approving_nonexistent_request_does_nothing(library):
    """GIVEN 대기 요청이 없을 때"""
    devices.init_schema()

    """WHEN 존재하지 않는 요청을 승인하려 하면"""
    devices.approve_request("p1", "no-such-device")

    """THEN 아무 일도 안 일어난다(에러 없이 조용히 무시)"""
    assert devices.count_devices("p1") == 0
