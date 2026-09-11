"""
프로필별 접속 기기 관리 - 최대 2대까지 자동 등록, 3번째부터는 관리자 승인이 필요하다.

"기기"는 실제로는 이 브라우저에 심어둔 쿠키(device_id) 하나를 가리킨다 - 시크릿 모드나
쿠키 삭제/재설치는 다른 기기로 인식된다는 한계가 있다는 걸 전제로 설계되었다(사용자와
이미 논의되어 감수하기로 한 부분).

접속할 때마다 마지막 접속 시각(last_seen_at)을 갱신해서, "오래된 기기"의 기준이
"등록한 지 오래됐다"가 아니라 "안 쓴 지 오래됐다"가 되게 한다 - 매일 쓰는 기기 2대는
등록 순서와 무관하게 절대 교체 대상이 되지 않는다.
"""

import os
import sqlite3
from datetime import datetime

from . import db

MAX_DEVICES_PER_PROFILE = 2
PROFILE_DEVICE_COOKIE_NAME = "webtoon_profile_device"
PROFILE_DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 접속마다 이 값으로 다시 연장(롤링)


def init_schema() -> None:
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_devices (
                profile_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, device_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_device_requests (
                profile_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                label TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, device_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def count_devices(profile_id: str) -> int:
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM profile_devices WHERE profile_id = ?", (profile_id,)
        ).fetchone()
    return row[0]


def is_device_registered(profile_id: str, device_id: str | None) -> bool:
    if not device_id:
        return False
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM profile_devices WHERE profile_id = ? AND device_id = ?", (profile_id, device_id)
        ).fetchone()
    return row is not None


def register_device(profile_id: str, device_id: str, label: str) -> None:
    now = datetime.utcnow().isoformat()
    with db.db_connection() as conn:
        conn.execute(
            "INSERT INTO profile_devices (profile_id, device_id, label, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            (profile_id, device_id, label, now, now),
        )
        conn.commit()


def touch_device(profile_id: str, device_id: str) -> None:
    with db.db_connection() as conn:
        conn.execute(
            "UPDATE profile_devices SET last_seen_at = ? WHERE profile_id = ? AND device_id = ?",
            (datetime.utcnow().isoformat(), profile_id, device_id),
        )
        conn.commit()


def list_devices(profile_id: str) -> list[dict]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT device_id, label, created_at, last_seen_at FROM profile_devices WHERE profile_id = ? "
            "ORDER BY last_seen_at DESC",
            (profile_id,),
        ).fetchall()
    return [{"device_id": r[0], "label": r[1], "created_at": r[2], "last_seen_at": r[3]} for r in rows]


def _remove_oldest_device(profile_id: str) -> None:
    """마지막 접속(last_seen_at)이 가장 오래된 기기 하나를 지운다 - "등록한 지"가
    아니라 "안 쓴 지" 오래된 걸 기준으로 하므로, 매일 쓰는 기기는 절대 교체되지 않는다."""
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT device_id FROM profile_devices WHERE profile_id = ? ORDER BY last_seen_at ASC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM profile_devices WHERE profile_id = ? AND device_id = ?", (profile_id, row[0])
            )
            conn.commit()


def create_pending_request(profile_id: str, device_id: str, label: str) -> None:
    """이미 같은 (프로필, 기기) 대기 요청이 있으면 그대로 두고 새로 안 만든다 - 요청
    화면을 닫았다가 같은 기기로 다시 들어와도 중복 요청이 쌓이지 않게."""
    with db.db_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM profile_device_requests WHERE profile_id = ? AND device_id = ?",
            (profile_id, device_id),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO profile_device_requests (profile_id, device_id, label, requested_at) VALUES (?, ?, ?, ?)",
            (profile_id, device_id, label, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_pending_request(profile_id: str, device_id: str | None) -> dict | None:
    if not device_id:
        return None
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT label, requested_at FROM profile_device_requests WHERE profile_id = ? AND device_id = ?",
            (profile_id, device_id),
        ).fetchone()
    return {"device_id": device_id, "label": row[0], "requested_at": row[1]} if row else None


def list_pending_requests(profile_id: str) -> list[dict]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT device_id, label, requested_at FROM profile_device_requests WHERE profile_id = ? "
            "ORDER BY requested_at ASC",
            (profile_id,),
        ).fetchall()
    return [{"device_id": r[0], "label": r[1], "requested_at": r[2]} for r in rows]


def approve_request(profile_id: str, device_id: str) -> None:
    """이미 2대가 등록되어 있으면 가장 오래(안 쓴) 기기를 먼저 지우고, 새 기기를
    등록한 뒤 대기 요청을 정리한다."""
    pending = get_pending_request(profile_id, device_id)
    if pending is None:
        return
    if count_devices(profile_id) >= MAX_DEVICES_PER_PROFILE:
        _remove_oldest_device(profile_id)
    register_device(profile_id, device_id, pending["label"])
    with db.db_connection() as conn:
        conn.execute(
            "DELETE FROM profile_device_requests WHERE profile_id = ? AND device_id = ?", (profile_id, device_id)
        )
        conn.commit()


def delete_all_data_for_profile(profile_id: str) -> None:
    """프로필 삭제 시 등록된 기기/대기 요청도 같이 정리한다."""
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_devices WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profile_device_requests WHERE profile_id = ?", (profile_id,))
        conn.commit()
