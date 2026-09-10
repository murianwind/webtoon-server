"""
app/access_requests.py 회귀 테스트.
"""

from app import access_requests as reqs


def test_new_request_is_pending(library):
    """GIVEN 요청이 없을 때"""
    reqs.init_schema()
    assert reqs.get_request_status("p1", "s1") is None

    """WHEN 요청을 만들면"""
    reqs.create_request("p1", "s1")

    """THEN 대기중 상태가 된다"""
    assert reqs.get_request_status("p1", "s1") == reqs.STATUS_PENDING


def test_duplicate_request_does_not_overwrite_existing_status(library):
    """GIVEN 이미 거부된 요청이 있을 때"""
    reqs.init_schema()
    reqs.create_request("p1", "s1")
    reqs.reject_request("p1", "s1")
    assert reqs.get_request_status("p1", "s1") == reqs.STATUS_REJECTED

    """WHEN 같은 (프로필, 시리즈)로 다시 요청을 시도하면"""
    reqs.create_request("p1", "s1")

    """THEN 거부 상태가 그대로 유지된다(요청으로 되돌아가지 않음)"""
    assert reqs.get_request_status("p1", "s1") == reqs.STATUS_REJECTED


def test_reject_then_undo_allows_requesting_again(library):
    """GIVEN 거부된 요청이 있을 때"""
    reqs.init_schema()
    reqs.reject_request("p1", "s1")
    assert reqs.get_request_status("p1", "s1") == reqs.STATUS_REJECTED

    """WHEN 관리자가 거부를 되돌리면"""
    reqs.clear_rejection("p1", "s1")

    """THEN 요청 기록 자체가 없어져서, 다시 요청할 수 있는 상태가 된다"""
    assert reqs.get_request_status("p1", "s1") is None
    reqs.create_request("p1", "s1")
    assert reqs.get_request_status("p1", "s1") == reqs.STATUS_PENDING


def test_approval_clears_the_request_record(library):
    """GIVEN 대기중인 요청이 있을 때"""
    reqs.init_schema()
    reqs.create_request("p1", "s1")

    """WHEN 관리자가 승인 처리하면(허용 목록에 넣고 요청 기록 정리)"""
    reqs.clear_request_on_approval("p1", "s1")

    """THEN 요청 기록이 사라진다(승인된 건 이제 요청 목록에 안 나와야 함)"""
    assert reqs.get_request_status("p1", "s1") is None


def test_list_requests_for_profile(library):
    """GIVEN 한 프로필에 요청이 여러 개 있을 때"""
    reqs.init_schema()
    reqs.create_request("p1", "s1")
    reqs.reject_request("p1", "s2")

    """WHEN 그 프로필의 요청 목록을 조회하면"""
    items = reqs.list_requests_for_profile("p1")

    """THEN 둘 다 나오고, 다른 프로필 요청은 안 섞인다"""
    series_ids = {item["series_id"] for item in items}
    assert series_ids == {"s1", "s2"}
    assert reqs.list_requests_for_profile("p2") == []
