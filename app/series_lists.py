"""
시리즈를 "정주행 중" / "나중에 읽기" 두 목록으로 나누는 규칙(관리자 개인 정리용).

- 기본값은 "정주행 중"이다. "나중에 읽기"로 옮긴 시리즈 ID만 저장한다(db.py).
- 목록을 옮기는 건 이 구분 표시 하나만 바꾼다. 이어보기 위치/읽음 기록은 전혀
  건드리지 않고, 읽는다고 해서 자동으로 옮겨지지도 않는다.
- 단일 책임: "어느 목록에 속하는가"와 "옮기기"만 안다. HTTP는 routers/series_lists.py,
  저장은 db.py가 맡는다.
"""

from . import catalog, db

READING = "reading"
LATER = "later"
VALID_LISTS = (READING, LATER)


def list_name_of(series_id: str, later_ids: set[str]) -> str:
    """later_ids는 목록 전체를 돌 때 매번 DB를 읽지 않도록, 호출하는 쪽이 한 번만
    읽어서 넘겨준다."""
    return LATER if series_id in later_ids else READING


def move(series_ids: list[str], target: str) -> int:
    """주어진 시리즈들을 target 목록으로 옮기고, 실제로 바뀐 개수를 돌려준다.

    target이 정의된 목록이 아니면 ValueError. 카탈로그에 없는 ID는 무시한다 -
    나중에 읽기로 보낼 때 없는 ID를 그대로 저장하면 유령 항목이 계속 쌓이기 때문이다.
    (정주행 중으로 되돌리는 쪽은 저장된 ID를 지우기만 하므로 카탈로그와 무관하게 처리)
    """
    if target not in VALID_LISTS:
        raise ValueError(f"unknown list: {target!r}")

    later_ids = db.get_later_series_ids()
    requested = set(series_ids)

    if target == LATER:
        known = requested & set(catalog.get_series_map())
        changed = known - later_ids
        new_later = later_ids | known
    else:
        changed = requested & later_ids
        new_later = later_ids - requested

    if changed:
        db.set_later_series_ids(new_later)
    return len(changed)
