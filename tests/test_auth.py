"""
app/auth.py(관리자 비밀번호 + 기억된 기기) 회귀 테스트. db.py와 완전히 분리된 테이블을
쓰므로, 기존 progress/settings 관련 어떤 테스트에도 영향을 주지 않는다.
"""

from app import auth


def test_every_call_generates_a_new_password(library):
    """GIVEN 저장된 관리자 비밀번호가 전혀 없을 때"""
    auth.init_schema()
    assert auth.has_admin_password() is False

    """WHEN 처음 호출하면"""
    first = auth.ensure_admin_password_exists()

    """THEN 무작위 비밀번호가 하나 만들어져서 반환되고, 저장된 상태가 된다"""
    assert first is not None and len(first) > 8
    assert auth.has_admin_password() is True

    """AND 다시 호출하면(재시작 흉내) 매번 새로운 비밀번호로 교체된다 - 등록된 기기가
    없는 상태라 안전하고, 기기가 있어도 그 기기는 비밀번호가 아니라 기기 쿠키로
    통과되므로 여전히 안전하다"""
    second = auth.ensure_admin_password_exists()
    assert second is not None and second != first

    """AND 이전 비밀번호로는 더 이상 로그인이 안 되고, 최신 비밀번호로만 된다"""
    assert auth.verify_admin_password(first, client_key="1.1.1.1") is False
    assert auth.verify_admin_password(second, client_key="1.1.1.2") is True


def test_correct_password_verifies_and_wrong_one_does_not(library):
    """GIVEN 비밀번호가 생성되어 있을 때"""
    auth.init_schema()
    password = auth.ensure_admin_password_exists()

    """WHEN 정확한 비밀번호로 확인하면 통과, 틀린 비밀번호는 실패한다"""
    assert auth.verify_admin_password(password, client_key="1.2.3.4") is True
    assert auth.verify_admin_password("완전히다른값", client_key="1.2.3.5") is False


def test_repeated_wrong_attempts_get_locked_out(library):
    """GIVEN 비밀번호가 있을 때"""
    auth.init_schema()
    password = auth.ensure_admin_password_exists()

    """WHEN 같은 클라이언트가 짧은 시간에 틀린 비밀번호를 여러 번 시도하면"""
    client = "9.9.9.9"
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth.verify_admin_password("틀린값", client_key=client)

    """THEN 그 이후로는 맞는 비밀번호를 넣어도 잠깐 통과가 안 된다"""
    assert auth.verify_admin_password(password, client_key=client) is False


def test_remembered_device_lifecycle(library):
    """GIVEN 기억된 기기가 없을 때"""
    auth.init_schema()
    assert auth.is_device_remembered("아무-토큰") is False

    """WHEN 기기를 하나 등록하면"""
    device_id = auth.create_remembered_device(label="Chrome · Windows")

    """THEN 그 기기는 기억된 상태로 조회된다"""
    assert auth.is_device_remembered(device_id) is True
    devices = auth.list_remembered_devices()
    assert any(d["device_id"] == device_id for d in devices)

    """WHEN 그 기기를 삭제하면"""
    auth.remove_remembered_device(device_id)

    """THEN 더 이상 기억된 상태가 아니다"""
    assert auth.is_device_remembered(device_id) is False
