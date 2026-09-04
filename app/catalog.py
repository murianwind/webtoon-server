"""
스캔 결과를 메모리에 담아두는 카탈로그. scan.scan_library()가 만든 (series, chapters)
결과를 이 모듈이 보관하고, API 라우트들은 여기서 읽는다.

실제 파일시스템 스캔 로직은 scan.py에 있다 - 이 모듈은 "지금 알고 있는 상태"만 담당한다
(단일 책임: 상태 저장/조회, 스캔 방법은 모름).
"""

from datetime import datetime

_state = {"series": {}, "chapters": {}, "last_scan_at": None, "folder_refs": {}, "known_platforms": []}


def get_series_map() -> dict:
    return _state["series"]


def get_chapters_map() -> dict:
    return _state["chapters"]


def get_series(series_id: str) -> dict | None:
    return _state["series"].get(series_id)


def get_chapter_zip_path(chapter_id: str) -> str | None:
    return _state["chapters"].get(chapter_id)


def get_last_scan_display() -> str | None:
    """마지막 스캔 시각을 "YYYY-MM-DD HH:MM" 형식으로 반환 (없으면 None).
    TZ 환경변수가 설정되어 있으면 그 시간대 기준으로 표시된다."""
    dt = _state["last_scan_at"]
    return dt.strftime("%Y-%m-%d %H:%M") if dt else None


def set_known_platforms(platforms: list[str]) -> None:
    """
    /library 바로 아래 폴더 이름 목록(=플랫폼 태그)을 기록해둔다. 폴더 이름만 훑는 거라
    거의 즉시 알 수 있어서, 실제 시리즈 스캔(특히 네트워크 드라이브)이 끝나기 한참
    전이라도 "이런 플랫폼이 있다"는 걸 화면에 먼저 보여줄 수 있게 하기 위함이다.
    """
    _state["known_platforms"] = list(platforms)


def get_known_platforms() -> list[str]:
    return _state["known_platforms"]


def replace(series_map: dict, chapters_map: dict) -> None:
    _state["series"] = series_map
    _state["chapters"] = chapters_map
    _state["last_scan_at"] = datetime.now()


def diff_and_replace(series_map: dict, chapters_map: dict) -> tuple[int, int]:
    """새 스캔 결과로 교체하면서 (추가된 시리즈 수, 제거된 시리즈 수)를 반환."""
    prev_ids = set(_state["series"].keys())
    new_ids = set(series_map.keys())
    added = len(new_ids - prev_ids)
    removed = len(prev_ids - new_ids)
    replace(series_map, chapters_map)
    return added, removed


def merge_platform(platform: str, series_map: dict, chapters_map: dict, folder_refs: list[str]) -> None:
    """
    플랫폼 하나만 새로 스캔한 결과를 반영한다. 다른 플랫폼의 기존 항목은 안 건드리고,
    이 플랫폼 소속 항목만 통째로 교체한다 - 로컬을 먼저 반영하고 네트워크 드라이브는
    나중에 반영해도, 그 사이에 로컬 목록이 먼저 화면에 뜰 수 있게 하기 위함이다.
    """
    old_chapter_ids_for_platform = set()
    kept_series = {}
    for sid, s in _state["series"].items():
        if s["platform"] == platform:
            old_chapter_ids_for_platform.update(ch["id"] for ch in s["chapters"])
        else:
            kept_series[sid] = s

    kept_chapters = {
        cid: path for cid, path in _state["chapters"].items()
        if cid not in old_chapter_ids_for_platform
    }

    kept_series.update(series_map)
    kept_chapters.update(chapters_map)

    _state["series"] = kept_series
    _state["chapters"] = kept_chapters
    _state["folder_refs"][platform] = folder_refs
    _state["last_scan_at"] = datetime.now()


def remove_series(series_id: str) -> None:
    """시리즈 하나를 카탈로그에서 즉시 뺀다(제외 처리 - 재스캔 없이 바로 반영하기 위함)."""
    series = _state["series"].pop(series_id, None)
    if series:
        for chapter in series["chapters"]:
            _state["chapters"].pop(chapter["id"], None)


def add_series(series_entry: dict, chapters_map: dict) -> None:
    """시리즈 하나를 카탈로그에 즉시 추가한다(포함/신규 스캔 시 - 전체 재스캔 없이 바로 반영)."""
    _state["series"][series_entry["id"]] = series_entry
    _state["chapters"].update(chapters_map)
    _state["last_scan_at"] = datetime.now()


def set_platform_folder_refs(platform: str, folder_refs: list[str]) -> None:
    """설정 패널의 폴더 목록 캐시만 갱신한다(시리즈 스캔 결과와는 별개로, 시리즈를
    하나씩 스캔하기 전에 "이 플랫폼에 이런 폴더들이 있다"를 먼저 반영해두기 위함)."""
    _state["folder_refs"][platform] = folder_refs


def prune_platform_series(platform: str, keep_ids: set[str]) -> None:
    """이 플랫폼 소속 시리즈 중 keep_ids에 없는 건 카탈로그에서 뺀다 - 이번 스캔에서
    다시 나타나지 않은 것(폴더가 삭제됐거나 새로 제외된 경우)을 정리하기 위함이다."""
    to_remove = [
        sid for sid, s in _state["series"].items()
        if s["platform"] == platform and sid not in keep_ids
    ]
    for sid in to_remove:
        remove_series(sid)


def get_all_folder_refs() -> list[tuple[str, str]]:
    """설정 패널의 "스캔 중/제외된 폴더" 목록용 - 마지막 스캔 때 이미 훑어둔 결과를
    그대로 재사용한다(요청마다 디스크를 다시 훑지 않기 위함)."""
    result = []
    for platform, refs in _state["folder_refs"].items():
        for ref in refs:
            result.append((platform, ref))
    return result


def find_chapter_position(chapter_id: str) -> tuple[dict | None, int | None]:
    """chapter_id가 속한 시리즈와 그 안에서의 인덱스를 찾는다. 못 찾으면 (None, None)."""
    for series in _state["series"].values():
        for index, chapter in enumerate(series["chapters"]):
            if chapter["id"] == chapter_id:
                return series, index
    return None, None
