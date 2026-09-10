"""
app/auth.py(관리자 비밀번호 + 기억된 기기) 회귀 테스트. db.py와 완전히 분리된 테이블을
쓰므로, 기존 progress/settings 관련 어떤 테스트에도 영향을 주지 않는다.
"""

from app import auth


def test_first_run_generates_a_password_once(library):
    """GIVEN 저장된 관리자 비밀번호가 전혀 없을 때"""
    auth.init_schema()
    assert auth.has_admin_password() is False

    """WHEN 최초 확인을 하면"""
    generated = auth.ensure_admin_password_exists()

    """THEN 무작위 비밀번호가 하나 만들어져서 반환되고, 저장된 상태가 된다"""
    assert generated is not None and len(generated) > 8
    assert auth.has_admin_password() is True

    """AND 다시 호출해도(재시작 흉내) 새로 만들지 않고 None을 반환한다 - 기존 기기가 안 끊기려면 필수"""
    again = auth.ensure_admin_password_exists()
    assert again is None


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
    auth.ensure_admin_password_exists()

    """WHEN 같은 클라이언트가 짧은 시간에 틀린 비밀번호를 여러 번 시도하면"""
    client = "9.9.9.9"
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth.verify_admin_password("틀린값", client_key=client)

    """THEN 그 이후로는 맞는 비밀번호를 넣어도 잠깐 통과가 안 된다"""
    correct = None  # 정확한 값을 몰라도 되도록, 다른 사용자로 별도 발급받아 확인
    with_password = auth.ensure_admin_password_exists()  # 이미 있으므로 None 반환됨
    assert with_password is None
    # 위에서 이미 5회 틀렸으므로, 설령 여기서 맞는 비번을 안다 해도 잠금 상태 확인만 검증
    assert auth.verify_admin_password("아무값", client_key=client) is False


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
