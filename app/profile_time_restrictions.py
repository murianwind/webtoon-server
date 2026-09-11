"""
프로필별 "리더만" 막는 접속 가능 시간대(요일+시간). 창을 하나도 안 설정한 프로필은
제한이 아예 없는 것으로 취급한다(기존 프로필들이 이 기능 추가로 갑자기 막히지 않게).

지금 읽고 있는 회차(그 프로필의 이어보기 포인터가 가리키는 회차)는 창 밖이어도 예외로
계속 볼 수 있게 하는데, 그 판단은 이 모듈이 아니라 access_control.py가 profile_progress와
함께 조합해서 한다 - 이 모듈은 "지금이 허용 시간인지"만 안다.
"""

import os
import sqlite3
from datetime import datetime

from . import db


def init_schema() -> None:
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_time_windows (
                profile_id TEXT NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_minute INTEGER NOT NULL,
                end_minute INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def set_time_windows(profile_id: str, windows: list[dict]) -> None:
    """통째로 교체한다. windows는 [{"day_of_week": 0-6(월=0), "start_minute": 0-1439,
    "end_minute": 0-1439}, ...] 형태. 빈 리스트면 "제한 없음"이 된다."""
    with db.db_connection() as conn:
        conn.execute("DELETE FROM profile_time_windows WHERE profile_id = ?", (profile_id,))
        conn.executemany(
            "INSERT INTO profile_time_windows (profile_id, day_of_week, start_minute, end_minute) VALUES (?, ?, ?, ?)",
            [(profile_id, w["day_of_week"], w["start_minute"], w["end_minute"]) for w in windows],
        )
        conn.commit()


def get_time_windows(profile_id: str) -> list[dict]:
    with db.db_connection() as conn:
        rows = conn.execute(
            "SELECT day_of_week, start_minute, end_minute FROM profile_time_windows WHERE profile_id = ? "
            "ORDER BY day_of_week, start_minute",
            (profile_id,),
        ).fetchall()
    return [{"day_of_week": r[0], "start_minute": r[1], "end_minute": r[2]} for r in rows]


def is_within_allowed_time(profile_id: str, now: datetime | None = None) -> bool:
    """창을 하나도 설정 안 했으면 항상 True(제한 없음). 설정했으면 지금 요일+시각이
    그중 하나에 들어가야 True."""
    windows = get_time_windows(profile_id)
    if not windows:
        return True
    now = now or datetime.now()
    day_of_week = now.weekday()  # 월=0 ... 일=6
    minute_of_day = now.hour * 60 + now.minute
    return any(
        w["day_of_week"] == day_of_week and w["start_minute"] <= minute_of_day <= w["end_minute"]
        for w in windows
    )
