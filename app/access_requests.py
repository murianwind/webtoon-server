"""
"둘러보기"에서 프로필이 시리즈를 요청("보여주세요")하는 흐름 전용 모듈. 프로필 자체나
허용 목록(profiles.py)과는 다른 관심사라 분리한다 - 요청은 "대기중/거부됨"이라는 자기만의
상태를 가지고, 승인은 이 모듈이 아니라 profiles.set_allowed_series()로 이루어진다(요청
테이블은 오직 "누가 뭘 물어봤고 거부됐는지"만 기억).
"""

import os
import sqlite3
from datetime import datetime

from . import db

STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"


def init_schema() -> None:
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_requests (
                profile_id TEXT NOT NULL,
                series_id TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, series_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def create_request(profile_id: str, series_id: str) -> None:
    """새 요청을 만든다. 이미 같은 (프로필, 시리즈) 요청이 있으면(대기중이든 거부든)
    새로 만들지 않고 그대로 둔다 - 거부된 걸 사용자가 다시 눌러서 관리자 알림을
    반복시키는 걸 막기 위함(거부됨 상태에서는 버튼 자체가 비활성화되니 정상 사용에선
    이 경로를 안 타지만, 방어적으로 한 번 더 막아둔다)."""
    with db.db_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM access_requests WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO access_requests (profile_id, series_id, status, requested_at) VALUES (?, ?, ?, ?)",
            (profile_id, series_id, STATUS_PENDING, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_request_status(profile_id: str, series_id: str) -> str | None:
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT status FROM access_requests WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        ).fetchone()
    return row[0] if row else None


def list_requests_for_profile(profile_id: str) -> list[dict]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT series_id, status, requested_at FROM access_requests WHERE profile_id = ? ORDER BY requested_at DESC",
            (profile_id,),
        ).fetchall()
    return [{"series_id": r[0], "status": r[1], "requested_at": r[2]} for r in rows]


def reject_request(profile_id: str, series_id: str) -> None:
    """관리자가 거부. 요청이 아직 없었어도(예: 목록에서 바로 거부) 거부 상태를 만들어둔다 -
    그래야 그 시리즈의 요청 버튼이 곧바로 비활성화된 채로 시작한다."""
    with db.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO access_requests (profile_id, series_id, status, requested_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (profile_id, series_id) DO UPDATE SET status = excluded.status
            """,
            (profile_id, series_id, STATUS_REJECTED, datetime.utcnow().isoformat()),
        )
        conn.commit()


def clear_rejection(profile_id: str, series_id: str) -> None:
    """관리자가 거부를 되돌림 - 다시 요청 가능한 상태로."""
    with db.db_connection() as conn:
        conn.execute(
            "DELETE FROM access_requests WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        )
        conn.commit()


def clear_request_on_approval(profile_id: str, series_id: str) -> None:
    """관리자가 승인(허용 목록에 추가)하면, 대기중이던 요청 기록은 더 이상 의미가 없으니
    지운다 - 이미 허용됐으니 "둘러보기"에도 안 나오고 요청 상태를 보여줄 자리도 없다."""
    with db.db_connection() as conn:
        conn.execute(
            "DELETE FROM access_requests WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        )
        conn.commit()
