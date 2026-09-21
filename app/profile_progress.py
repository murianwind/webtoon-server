"""
프로필별 이어보기/읽음 기록. 관리자(공용)의 진행률은 db.py의 progress/read_chapters
테이블을 그대로 쓰고, 이 모듈은 절대 건드리지 않는다 - 완전히 별도 테이블에 profile_id를
더해서 담아, 관리자 데이터와 프로필 데이터가 한 치도 섞이지 않게 한다.

db.py의 progress/read_chapters 관련 함수들과 구조가 비슷하지만(같은 개념이니 당연함),
일부러 복붙 재사용하지 않고 이 모듈에 그대로 다시 구현한다 - db.py를 프로필 인식하게
고치면 관리자 쪽 기존 로직을 건드리게 되어 회귀 위험이 생기기 때문이다.
"""

import os
import sqlite3
from datetime import datetime

from . import db

# db.PAGE_FINISHED_SENTINEL과 의미/용도가 동일하다(그대로 재사용 - 같은 상수를 두 곳에서
# 따로 정의하면 나중에 한쪽만 바뀌는 사고가 날 수 있음).
PAGE_FINISHED_SENTINEL = db.PAGE_FINISHED_SENTINEL


def init_schema() -> None:
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_progress (
                profile_id TEXT NOT NULL,
                series_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                page_index INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, series_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_read_chapters (
                profile_id TEXT NOT NULL,
                series_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                PRIMARY KEY (profile_id, series_id, chapter_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_progress(profile_id: str, series_id: str) -> dict | None:
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT chapter_id, chapter_index, page_index FROM profile_progress "
            "WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        ).fetchone()
    if not row:
        return None
    return {"chapter_id": row[0], "chapter_index": row[1], "page_index": row[2]}


def set_progress(profile_id: str, series_id: str, chapter_id: str, chapter_index: int, page_index: int) -> None:
    with db.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO profile_progress (profile_id, series_id, chapter_id, chapter_index, page_index, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (profile_id, series_id) DO UPDATE SET
                chapter_id = excluded.chapter_id,
                chapter_index = excluded.chapter_index,
                page_index = excluded.page_index,
                updated_at = excluded.updated_at
            """,
            (profile_id, series_id, chapter_id, chapter_index, page_index, datetime.utcnow().isoformat()),
        )
        conn.commit()


def delete_progress(profile_id: str, series_id: str) -> None:
    with db.db_connection() as conn:
        conn.execute(
            "DELETE FROM profile_progress WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        )
        conn.commit()


def get_read_chapter_ids(profile_id: str, series_id: str) -> set[str]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT chapter_id FROM profile_read_chapters WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        ).fetchall()
    return {r[0] for r in rows}


def mark_chapters_read(profile_id: str, series_id: str, chapter_ids: list[str]) -> None:
    if not chapter_ids:
        return
    with db.db_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO profile_read_chapters (profile_id, series_id, chapter_id) VALUES (?, ?, ?)",
            [(profile_id, series_id, cid) for cid in chapter_ids],
        )
        conn.commit()


def mark_chapters_unread(profile_id: str, series_id: str, chapter_ids: list[str]) -> None:
    if not chapter_ids:
        return
    with db.db_connection() as conn:
        conn.executemany(
            "DELETE FROM profile_read_chapters WHERE profile_id = ? AND series_id = ? AND chapter_id = ?",
            [(profile_id, series_id, cid) for cid in chapter_ids],
        )
        conn.commit()


def clear_all_read_chapters(profile_id: str, series_id: str) -> None:
    with db.db_connection() as conn:
        conn.execute(
            "DELETE FROM profile_read_chapters WHERE profile_id = ? AND series_id = ?",
            (profile_id, series_id),
        )
        conn.commit()


def delete_all_data_for_profile(profile_id: str) -> None:
    """프로필 삭제 시 진행률/읽음기록도 같이 정리한다."""
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_progress WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profile_read_chapters WHERE profile_id = ?", (profile_id,))
        conn.commit()


def export_all() -> dict:
    """백업용 - 모든 프로필의 이어보기 진행률 + 읽은 회차 기록을 전부 내보낸다."""
    init_schema()
    with db.db_connection() as conn:
        progress_rows = conn.execute(
            "SELECT profile_id, series_id, chapter_id, chapter_index, page_index, updated_at FROM profile_progress"
        ).fetchall()
        read_rows = conn.execute(
            "SELECT profile_id, series_id, chapter_id FROM profile_read_chapters"
        ).fetchall()
    return {
        "profile_progress": [
            {
                "profile_id": r[0], "series_id": r[1], "chapter_id": r[2],
                "chapter_index": r[3], "page_index": r[4], "updated_at": r[5],
            }
            for r in progress_rows
        ],
        "profile_read_chapters": [
            {"profile_id": r[0], "series_id": r[1], "chapter_id": r[2]} for r in read_rows
        ],
    }


def import_all(progress_rows: list, read_chapter_rows: list) -> int:
    """기존 프로필 진행률/읽음기록을 전부 지우고 백업 내용으로 교체한다. 반환값은
    복원된 진행률 건수."""
    init_schema()
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_progress")
        conn.execute("DELETE FROM profile_read_chapters")

        count = 0
        for row in progress_rows:
            if not all(k in row for k in ("profile_id", "series_id", "chapter_id", "chapter_index", "updated_at")):
                continue
            conn.execute(
                """
                INSERT INTO profile_progress
                    (profile_id, series_id, chapter_id, chapter_index, page_index, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["profile_id"], row["series_id"], row["chapter_id"],
                    row["chapter_index"], row.get("page_index", 0), row["updated_at"],
                ),
            )
            count += 1
        for row in read_chapter_rows:
            if not all(k in row for k in ("profile_id", "series_id", "chapter_id")):
                continue
            conn.execute(
                "INSERT INTO profile_read_chapters (profile_id, series_id, chapter_id) VALUES (?, ?, ?)",
                (row["profile_id"], row["series_id"], row["chapter_id"]),
            )
        conn.commit()
    return count
