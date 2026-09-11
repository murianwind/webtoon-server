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

STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"


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
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, device_id)
            )
            """
        )
        # 이 기능이 나중에(거부 버튼과 함께) 추가되어, status 컬럼 없이 이미 만들어진
        # 테이블이 있을 수 있다 - 없으면 추가하고, 이미 있으면 에러가 나므로 조용히 무시.
        try:
            conn.execute("ALTER TABLE profile_device_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        except sqlite3.OperationalError:
            pass
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
    """이미 같은 (프로필, 기기) 요청이 있으면(대기중이든 거부든) 그대로 두고 새로 안
    만든다 - 요청 화면을 닫았다가 같은 기기로 다시 들어와도 중복 요청이 안 쌓이고,
    거부된 걸 다시 요청해서 관리자한테 알림이 반복되는 것도 막는다."""
    with db.db_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM profile_device_requests WHERE profile_id = ? AND device_id = ?",
            (profile_id, device_id),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO profile_device_requests (profile_id, device_id, label, status, requested_at) VALUES (?, ?, ?, ?, ?)",
            (profile_id, device_id, label, STATUS_PENDING, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_request_status(profile_id: str, device_id: str | None) -> str | None:
    """"pending" | "rejected" | None(요청 자체가 없음)."""
    if not device_id:
        return None
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT status FROM profile_device_requests WHERE profile_id = ? AND device_id = ?",
            (profile_id, device_id),
        ).fetchone()
    return row[0] if row else None


def get_pending_request(profile_id: str, device_id: str | None) -> dict | None:
    """상태(대기/거부)와 무관하게 요청 레코드 자체를 가져온다 - approve_request가
    label을 알아내는 데 쓴다(거부된 요청이라도 관리자가 뒤늦게 승인할 수 있어야 함)."""
    if not device_id:
        return None
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT label, requested_at FROM profile_device_requests WHERE profile_id = ? AND device_id = ?",
            (profile_id, device_id),
        ).fetchone()
    return {"device_id": device_id, "label": row[0], "requested_at": row[1]} if row else None


def list_pending_requests(profile_id: str) -> list[dict]:
    """관리자 화면에 보여줄 목록 - 이미 거부 처리된 건 더 이상 "대기중"이 아니므로 뺀다."""
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT device_id, label, requested_at FROM profile_device_requests WHERE profile_id = ? AND status = ? "
            "ORDER BY requested_at ASC",
            (profile_id, STATUS_PENDING),
        ).fetchall()
    return [{"device_id": r[0], "label": r[1], "requested_at": r[2]} for r in rows]


def reject_request(profile_id: str, device_id: str) -> None:
    """대기중인 요청을 거부 상태로 바꾼다. 요청 자체가 아직 없었어도(관리자가 먼저
    선제적으로 막고 싶은 경우) 거부 상태를 만들어서, 그 기기가 다시 신청 버튼을
    눌러도 곧바로 거부됨으로 처리되게 한다."""
    with db.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO profile_device_requests (profile_id, device_id, label, status, requested_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (profile_id, device_id) DO UPDATE SET status = excluded.status
            """,
            (profile_id, device_id, "", STATUS_REJECTED, datetime.utcnow().isoformat()),
        )
        conn.commit()


def approve_request(profile_id: str, device_id: str) -> None:
    """이미 2대가 등록되어 있으면 가장 오래(안 쓴) 기기를 먼저 지우고, 새 기기를
    등록한 뒤 요청 레코드를 정리한다. 거부됐던 요청이라도 관리자가 뒤늦게 승인하면
    그대로 등록된다(거부는 되돌릴 수 없는 최종 상태가 아니라, 승인이 그 자체로
    번복 수단이 된다)."""
    pending = get_pending_request(profile_id, device_id)
    if pending is None:
        return
    label = pending["label"] or "알 수 없는 기기"
    if count_devices(profile_id) >= MAX_DEVICES_PER_PROFILE:
        _remove_oldest_device(profile_id)
    register_device(profile_id, device_id, label)
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
