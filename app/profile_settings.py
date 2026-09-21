"""
프로필별 화면 설정(검색 필터, 정렬 등) 저장. 관리자의 app_settings 테이블(db.py)과는
완전히 분리된 테이블이라, 한 프로필이 뭘 바꿔도 관리자나 다른 프로필의 설정에는
전혀 영향이 없다.
"""

import os
import sqlite3

from . import db


def init_schema() -> None:
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_settings (
                profile_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (profile_id, key)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_setting(profile_id: str, key: str) -> str | None:
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT value FROM profile_settings WHERE profile_id = ? AND key = ?", (profile_id, key)
        ).fetchone()
    return row[0] if row else None


def set_setting(profile_id: str, key: str, value: str) -> None:
    with db.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO profile_settings (profile_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT (profile_id, key) DO UPDATE SET value = excluded.value
            """,
            (profile_id, key, value),
        )
        conn.commit()


def export_all() -> list[dict]:
    """백업용 - 모든 프로필의 검색/정렬/필터 설정을 전부 내보낸다."""
    init_schema()
    with db.db_connection() as conn:
        rows = conn.execute("SELECT profile_id, key, value FROM profile_settings").fetchall()
    return [{"profile_id": r[0], "key": r[1], "value": r[2]} for r in rows]


def import_all(rows: list) -> int:
    """기존 프로필 설정을 전부 지우고 백업 내용으로 교체한다. 반환값은 복원된 건수."""
    init_schema()
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_settings")
        count = 0
        for row in rows:
            if not all(k in row for k in ("profile_id", "key", "value")):
                continue
            conn.execute(
                "INSERT INTO profile_settings (profile_id, key, value) VALUES (?, ?, ?)",
                (row["profile_id"], row["key"], row["value"]),
            )
            count += 1
        conn.commit()
    return count
