"""
"정주행 중" / "나중에 읽기" 목록 이동 라우트(관리자 전용).

관리자 개인 정리용이라 공유 프로필에게는 이 기능 자체가 존재하지 않아야 한다 -
library.py/backup.py와 같은 이유로 라우터 전체에 require_admin을 건다.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import access_control, series_lists

router = APIRouter(dependencies=[Depends(access_control.require_admin)])


class MoveSeriesIn(BaseModel):
    series_ids: list[str]
    list: str  # series_lists.READING | series_lists.LATER


@router.put("/api/series-lists")
def move_series(body: MoveSeriesIn):
    try:
        moved = series_lists.move(body.series_ids, body.list)
    except ValueError:
        raise HTTPException(400, f"list must be one of {list(series_lists.VALID_LISTS)}")
    return {"moved": moved}
