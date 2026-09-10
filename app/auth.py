"""
관리자 비밀번호(해시 저장) + "기억된 기기" 관리. PROFILES_ENABLED가 켜졌을 때만 쓰인다.

기존 db.py는 건드리지 않는다 - 같은 DB 파일(db.DB_PATH)에 이 모듈만의 테이블을 추가로
만들어서 쓴다. db.py의 기존 테이블/쿼리는 이 모듈이 존재하는지조차 몰라도 된다(단일 책임:
db.py는 "관리자(공용) 데이터", 이 모듈은 "누가 관리자인지 확인하는 것"만 담당).

비밀번호는 평문으로 저장하지 않는다 - PBKDF2(반복 해싱)로 저장해서, DB 파일이 유출돼도
원문을 바로 복원할 수 없게 한다.
"""

import hashlib
import logging
import os
import secrets
import sqlite3
from datetime import datetime

from . import db

log = logging.getLogger("webtoon-server")

_PASSWORD_HASH_KEY = "admin_password_hash"
_PBKDF2_ITERATIONS = 200_000

# 짧은 시간에 비밀번호를 너무 많이 틀리면 잠깐 잠근다(무작위 대입 방지).
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

_failed_attempts: dict[str, list[float]] = {}


def init_schema() -> None:
    """앱 시작 시 한 번 호출. db.py의 init_schema()와 별개로, 이 모듈만의 테이블을 만든다."""
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_auth (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS remembered_devices (
                device_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return salt.hex() + ":" + digest.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return secrets.compare_digest(expected.hex(), digest_hex)


def has_admin_password() -> bool:
    with db.db_connection() as conn:
        row = conn.execute("SELECT value FROM admin_auth WHERE key = ?", (_PASSWORD_HASH_KEY,)).fetchone()
    return row is not None


def ensure_admin_password_exists() -> str:
    """
    서버가 시작될 때마다 호출된다. 항상 새 비밀번호를 만들어서 반환한다(호출한 쪽이
    로그/디스코드로 안내하도록) - 이미 로그인해서 기기로 등록된 사람은 전혀 영향이
    없다. 그 사람들은 비밀번호를 다시 확인받는 게 아니라 "기억된 기기" 쿠키로만
    통과되므로(profile_and_admin_gate 참고), 비밀번호 자체는 그 이후로 아무 의미가
    없어진 상태다. 즉 이 비밀번호는 "그 순간 새로 접속하려는 기기를 등록할 때만"
    필요한 일회성 열쇠에 가까워서, 재시작마다 바뀌어도 기존 기기는 안전하다.
    """
    raw_password = secrets.token_urlsafe(9)  # 사람이 옮겨 적기 부담없는 길이의 무작위 비밀번호
    salt = secrets.token_bytes(16)
    hashed = _hash_password(raw_password, salt)
    with db.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO admin_auth (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (_PASSWORD_HASH_KEY, hashed),
        )
        conn.commit()
    return raw_password


def verify_admin_password(password: str, client_key: str) -> bool:
    """
    client_key는 잠금 판단 단위(보통 접속 IP)다. 짧은 시간에 너무 많이 틀리면 무조건
    실패로 처리해서, 무작위로 계속 찍어보는 시도를 막는다.
    """
    now = datetime.utcnow().timestamp()
    attempts = [t for t in _failed_attempts.get(client_key, []) if now - t < LOCKOUT_SECONDS]
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        _failed_attempts[client_key] = attempts
        return False

    with db.db_connection() as conn:
        row = conn.execute("SELECT value FROM admin_auth WHERE key = ?", (_PASSWORD_HASH_KEY,)).fetchone()
    if row is None:
        return False

    ok = _verify_password(password, row[0])
    if not ok:
        attempts.append(now)
        _failed_attempts[client_key] = attempts
    else:
        _failed_attempts.pop(client_key, None)
    return ok


def create_remembered_device(label: str) -> str:
    """비밀번호 확인에 성공한 기기를 기억해두고, 쿠키에 넣을 device_id를 반환한다."""
    device_id = secrets.token_urlsafe(24)
    now = datetime.utcnow().isoformat()
    with db.db_connection() as conn:
        conn.execute(
            "INSERT INTO remembered_devices (device_id, label, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (device_id, label, now, now),
        )
        conn.commit()
    return device_id


def is_device_remembered(device_id: str | None) -> bool:
    if not device_id:
        return False
    with db.db_connection() as conn:
        row = conn.execute("SELECT 1 FROM remembered_devices WHERE device_id = ?", (device_id,)).fetchone()
    return row is not None


def touch_device(device_id: str) -> None:
    """이 기기가 실제로 요청을 보낼 때마다 마지막 접속 시각을 갱신(관리 화면에 표시용)."""
    with db.db_connection() as conn:
        conn.execute(
            "UPDATE remembered_devices SET last_seen_at = ? WHERE device_id = ?",
            (datetime.utcnow().isoformat(), device_id),
        )
        conn.commit()


def list_remembered_devices() -> list[dict]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT device_id, label, created_at, last_seen_at FROM remembered_devices ORDER BY last_seen_at DESC"
        ).fetchall()
    return [
        {"device_id": r[0], "label": r[1], "created_at": r[2], "last_seen_at": r[3]}
        for r in rows
    ]


def remove_remembered_device(device_id: str) -> None:
    """등록된 기기 목록에서 삭제 - 그 기기는 다음 접속 때 비밀번호를 다시 물어보게 된다."""
    with db.db_connection() as conn:
        conn.execute("DELETE FROM remembered_devices WHERE device_id = ?", (device_id,))
        conn.commit()
