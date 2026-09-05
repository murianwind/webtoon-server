"""
SQLite 영속 계층. 읽음 진행률, 앱 설정(검색/정렬/필터 등), 시리즈 폴더 제외 목록,
회차 간 겹침(리캡) 캐시, 백업/복원까지 이 모듈에서만 SQL을 다룬다.

스키마 생성은 init_schema()로 앱 시작 시 한 번만 실행한다 - 예전에는 커넥션을 열 때마다
CREATE TABLE IF NOT EXISTS를 반복 실행했는데(매 요청마다 불필요한 반복), 이제는 시작 시
한 번만 만들고 이후에는 연결만 열고 닫는다.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "/data/progress.db")

# 회차의 page_index로 이 값이 저장되어 있으면 "그 회차까지 다 읽었다"는 뜻.
# /continue 조회 시 이 값을 만나면 실제 페이지 수와 비교해 다음 화로 자동 이동시킨다.
PAGE_FINISHED_SENTINEL = 1_000_000

_EXCLUDED_SERIES_SETTING_KEY = "excluded_series"


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def init_schema() -> None:
    """앱 시작 시 한 번만 호출. 테이블이 없으면 만든다."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress (
                series_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                page_index INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chapter_overlap (
                next_chapter_id TEXT PRIMARY KEY,
                prev_chapter_id TEXT NOT NULL,
                skip_pages INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS read_chapters (
                series_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                PRIMARY KEY (series_id, chapter_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db_connection():
    """단발성 커넥션을 열고 블록이 끝나면 자동으로 닫는다.
    init_schema()가 이미 테이블을 만들어뒀다고 가정한다."""
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 읽음 진행률
# ---------------------------------------------------------------------------


def get_progress(series_id: str) -> dict | None:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT chapter_id, chapter_index, page_index FROM progress WHERE series_id = ?",
            (series_id,),
        ).fetchone()
    if not row:
        return None
    return {"chapter_id": row[0], "chapter_index": row[1], "page_index": row[2]}


def set_progress(series_id: str, chapter_id: str, chapter_index: int, page_index: int) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO progress (series_id, chapter_id, chapter_index, page_index, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(series_id) DO UPDATE SET
                chapter_id = excluded.chapter_id,
                chapter_index = excluded.chapter_index,
                page_index = excluded.page_index,
                updated_at = excluded.updated_at
            """,
            (series_id, chapter_id, chapter_index, page_index, _utc_now_iso()),
        )
        conn.commit()


def delete_progress(series_id: str) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM progress WHERE series_id = ?", (series_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# 회차별 명시적 읽음 기록
#
# "몇 번째까지 읽었다"는 인덱스 하나로 뭉뚱그리면, 나중에 빠졌던 회차가 사이사이에
# 채워질 때 "그 시점의 경계선보다 앞이니 이미 읽은 것"이라고 잘못 취급해버리는 문제가
# 있다 (실제로는 한 번도 안 본 회차인데도). 그래서 회차 하나하나를 chapter_id로
# 명시적으로 기록해서, 나중에 순서가 바뀌거나 사이에 새 회차가 끼어들어도 그 회차
# 자체의 읽음 여부는 흔들리지 않게 한다.
# ---------------------------------------------------------------------------


def get_read_chapter_ids(series_id: str) -> set[str]:
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT chapter_id FROM read_chapters WHERE series_id = ?", (series_id,)
        ).fetchall()
    return {row[0] for row in rows}


def mark_chapters_read(series_id: str, chapter_ids: list[str]) -> None:
    if not chapter_ids:
        return
    with db_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO read_chapters (series_id, chapter_id) VALUES (?, ?)",
            [(series_id, chapter_id) for chapter_id in chapter_ids],
        )
        conn.commit()


def mark_chapters_unread(series_id: str, chapter_ids: list[str]) -> None:
    if not chapter_ids:
        return
    with db_connection() as conn:
        conn.executemany(
            "DELETE FROM read_chapters WHERE series_id = ? AND chapter_id = ?",
            [(series_id, chapter_id) for chapter_id in chapter_ids],
        )
        conn.commit()


def clear_all_read_chapters(series_id: str) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM read_chapters WHERE series_id = ?", (series_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# 앱 설정 (검색/정렬/필터 등 기기 간 동일하게 유지할 값)
# ---------------------------------------------------------------------------


def get_setting(key: str, default=None):
    with db_connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 시리즈 폴더 스캔 제외 목록 (플랫폼 폴더 안에 웹툰 아닌 폴더가 섞여 있을 때
# 특정 폴더만 스캔에서 뺐다가 다시 넣을 수 있게 함. 실제 파일은 절대 건드리지 않음)
# ---------------------------------------------------------------------------


def get_excluded_series() -> set[tuple[str, str]]:
    """제외된 (platform, series_name) 튜플 집합."""
    raw = get_setting(_EXCLUDED_SERIES_SETTING_KEY)
    if not raw:
        return set()
    try:
        data = json.loads(raw)
        return {(item["platform"], item["series"]) for item in data if "platform" in item and "series" in item}
    except Exception:
        return set()


def set_excluded_series(pairs: set[tuple[str, str]]) -> None:
    data = [{"platform": p, "series": s} for p, s in sorted(pairs)]
    set_setting(_EXCLUDED_SERIES_SETTING_KEY, json.dumps(data, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 회차 간 겹침(리캡) 캐시
# ---------------------------------------------------------------------------


def get_cached_overlap(next_chapter_id: str) -> int | None:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT skip_pages FROM chapter_overlap WHERE next_chapter_id = ?",
            (next_chapter_id,),
        ).fetchone()
    return row[0] if row else None


def set_cached_overlap(next_chapter_id: str, prev_chapter_id: str, skip_pages: int) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO chapter_overlap (next_chapter_id, prev_chapter_id, skip_pages, computed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(next_chapter_id) DO UPDATE SET
                prev_chapter_id = excluded.prev_chapter_id,
                skip_pages = excluded.skip_pages,
                computed_at = excluded.computed_at
            """,
            (next_chapter_id, prev_chapter_id, skip_pages, _utc_now_iso()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 백업 / 복원 (여러 행을 한 트랜잭션으로 다루는 벌크 작업이라 raw 커넥션을 직접 씀)
# ---------------------------------------------------------------------------


def export_backup_data() -> dict:
    with db_connection() as conn:
        progress_rows = conn.execute(
            "SELECT series_id, chapter_id, chapter_index, page_index, updated_at FROM progress"
        ).fetchall()
        settings_rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        read_rows = conn.execute("SELECT series_id, chapter_id FROM read_chapters").fetchall()
    return {
        "progress": [
            {
                "series_id": row[0],
                "chapter_id": row[1],
                "chapter_index": row[2],
                "page_index": row[3],
                "updated_at": row[4],
            }
            for row in progress_rows
        ],
        "app_settings": [{"key": row[0], "value": row[1]} for row in settings_rows],
        "read_chapters": [{"series_id": row[0], "chapter_id": row[1]} for row in read_rows],
    }


def import_backup_data(progress_rows: list, settings_rows: list, read_chapter_rows: list | None = None) -> tuple[int, int, int]:
    """기존 progress/app_settings/read_chapters를 전부 지우고 주어진 내용으로 교체.
    반환값은 (저장된 progress 건수, 저장된 settings 건수, 저장된 read_chapters 건수)."""
    read_chapter_rows = read_chapter_rows or []
    progress_count = 0
    settings_count = 0
    read_count = 0
    with db_connection() as conn:
        conn.execute("DELETE FROM progress")
        conn.execute("DELETE FROM app_settings")
        conn.execute("DELETE FROM read_chapters")

        for row in progress_rows:
            series_id = row.get("series_id")
            chapter_id = row.get("chapter_id")
            if not series_id or not chapter_id:
                continue
            conn.execute(
                """
                INSERT INTO progress (series_id, chapter_id, chapter_index, page_index, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    series_id,
                    chapter_id,
                    int(row.get("chapter_index", 0)),
                    int(row.get("page_index", 0)),
                    row.get("updated_at") or _utc_now_iso(),
                ),
            )
            progress_count += 1

        for row in settings_rows:
            key = row.get("key")
            if not key:
                continue
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                (key, row.get("value", "")),
            )
            settings_count += 1

        for row in read_chapter_rows:
            series_id = row.get("series_id")
            chapter_id = row.get("chapter_id")
            if not series_id or not chapter_id:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO read_chapters (series_id, chapter_id) VALUES (?, ?)",
                (series_id, chapter_id),
            )
            read_count += 1

        conn.commit()
    return progress_count, settings_count, read_count
