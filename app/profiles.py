"""
프로필(공유 계정) 관리: 생성/수정/삭제, 둘러보기 연령 필터(플랫폼별), 허용 시리즈 목록.
"진행률"은 이 모듈이 아니라 profile_progress.py가 담당한다(관심사 분리: "누구인지"와
"그 사람이 어디까지 읽었는지"는 서로 다른 문제).

시리즈를 가리킬 때는 scan.make_id()가 만드는 것과 동일한 series_id를 그대로 재사용한다 -
그 값은 (platform, series_ref) 조합에서 나오는 안정적인 해시라, 재스캔해도 같은 시리즈는
항상 같은 값을 유지한다(이미 다른 기능에서 검증됨).
"""

import os
import secrets
import sqlite3
from datetime import datetime

from . import db


def init_schema() -> None:
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_browse_filters (
                profile_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                age_rating TEXT NOT NULL,
                PRIMARY KEY (profile_id, platform, age_rating)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_allowed_series (
                profile_id TEXT NOT NULL,
                series_id TEXT NOT NULL,
                PRIMARY KEY (profile_id, series_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# 연령 정보가 아예 없는 시리즈를 가리키는 특수값 - 실제 info.xml 값과 절대 안 겹치도록
# 플랫폼 원문에는 나올 수 없는 형태로 정함.
NO_AGE_RATING = "__no_rating__"


def create_profile(name: str) -> dict:
    profile_id = secrets.token_hex(6)
    token = secrets.token_urlsafe(24)
    now = datetime.utcnow().isoformat()
    with db.db_connection() as conn:
        conn.execute(
            "INSERT INTO profiles (id, name, token, created_at) VALUES (?, ?, ?, ?)",
            (profile_id, name, token, now),
        )
        conn.commit()
    return {"id": profile_id, "name": name, "token": token, "created_at": now}


def get_profile_by_token(token: str) -> dict | None:
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT id, name, token, created_at FROM profiles WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "token": row[2], "created_at": row[3]}


def get_profile(profile_id: str) -> dict | None:
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT id, name, token, created_at FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "token": row[2], "created_at": row[3]}


def list_profiles() -> list[dict]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, token, created_at FROM profiles ORDER BY created_at ASC"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "token": r[2], "created_at": r[3]} for r in rows]


def rename_profile(profile_id: str, name: str) -> None:
    with db.db_connection() as conn:
        conn.execute("UPDATE profiles SET name = ? WHERE id = ?", (name, profile_id))
        conn.commit()


def reissue_profile_token(profile_id: str) -> str:
    """기존 링크를 무효화하고 새 토큰을 발급한다. 프로필 이름/허용목록/진행률은 그대로 유지됨
    (profile_progress.py 쪽 데이터는 profile_id로 연결되어 있어 토큰이 바뀌어도 안 끊어짐)."""
    new_token = secrets.token_urlsafe(24)
    with db.db_connection() as conn:
        conn.execute("UPDATE profiles SET token = ? WHERE id = ?", (new_token, profile_id))
        conn.commit()
    return new_token


def delete_profile(profile_id: str) -> None:
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.execute("DELETE FROM profile_browse_filters WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profile_allowed_series WHERE profile_id = ?", (profile_id,))
        conn.commit()


def set_browse_filters(profile_id: str, platform_age_pairs: list[tuple[str, str]]) -> None:
    """이 프로필의 "둘러보기"에 보여줄 (플랫폼, 연령등급) 조합 전체를 통째로 교체한다.
    연령등급 없는 시리즈를 포함하려면 age_rating에 NO_AGE_RATING을 넣어서 넘긴다."""
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_browse_filters WHERE profile_id = ?", (profile_id,))
        conn.executemany(
            "INSERT INTO profile_browse_filters (profile_id, platform, age_rating) VALUES (?, ?, ?)",
            [(profile_id, platform, age) for platform, age in platform_age_pairs],
        )
        conn.commit()


def get_browse_filters(profile_id: str) -> set[tuple[str, str]]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT platform, age_rating FROM profile_browse_filters WHERE profile_id = ?",
            (profile_id,),
        ).fetchall()
    return {(r[0], r[1]) for r in rows}


def set_allowed_series(profile_id: str, series_ids: list[str]) -> None:
    """이 프로필이 "내 웹툰"에서 실제로 볼 수 있는 시리즈 전체를 통째로 교체한다."""
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_allowed_series WHERE profile_id = ?", (profile_id,))
        conn.executemany(
            "INSERT INTO profile_allowed_series (profile_id, series_id) VALUES (?, ?)",
            [(profile_id, sid) for sid in series_ids],
        )
        conn.commit()


def get_allowed_series_ids(profile_id: str) -> set[str]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT series_id FROM profile_allowed_series WHERE profile_id = ?", (profile_id,)
        ).fetchall()
    return {r[0] for r in rows}
