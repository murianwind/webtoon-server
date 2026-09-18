"""
스캔 결과를 메모리에 담아두는 카탈로그. scan.iter_platform_series_streaming()이 플랫폼별로
찾아낸 시리즈들을 이 모듈이 보관하고(add_series/remove_series/prune_platform_series로
시리즈 하나 단위로 갱신), API 라우트들은 여기서 읽는다.

실제 파일시스템 스캔 로직은 scan.py에 있다 - 이 모듈은 "지금 알고 있는 상태"만 담당한다
(단일 책임: 상태 저장/조회, 스캔 방법은 모름).
"""

from datetime import datetime

_state = {"series": {}, "chapters": {}, "last_scan_at": None, "folder_refs": {}, "known_platforms": []}


def get_series_map() -> dict:
    return _state["series"]


def get_chapters_map() -> dict:
    return _state["chapters"]


def get_platform_age_ratings() -> dict[str, set[str]]:
    """
    지금 스캔되어 있는 시리즈들을 훑어서, 플랫폼별로 실제 등장하는 연령등급 값 집합을
    모아 반환한다. (공유 프로필의 "둘러보기 연령 필터" 화면에서, 실제 라이브러리에
    어떤 값들이 있는지 보여주는 용도 - DB에 따로 저장하지 않고 스캔 결과에서 매번
    바로 뽑아서 항상 최신 상태를 반영한다.)

    연령등급 정보가 없는 시리즈(info.xml이 없거나 그 필드가 빈 경우)는 None으로 묶어서
    포함한다 - 호출하는 쪽에서 "정보 없음"으로 표시할 수 있게.
    """
    result: dict[str, set[str]] = {}
    for series in _state["series"].values():
        platform = series["platform"]
        info = series.get("info") or {}
        age_rating = info.get("age_rating") or None
        result.setdefault(platform, set()).add(age_rating)
    return result


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


def add_platform_folder_ref(platform: str, series_ref: str) -> None:
    """설정 패널의 폴더 목록에 새로 발견된 폴더 하나를 추가한다(교체가 아니라 추가).

    예전에는 스캔 중 폴더를 하나 발견할 때마다 "지금까지 이번 스캔에서 본 것만"으로
    이 플랫폼의 전체 목록을 통째로 교체했다 - 그러면 스캔이 시작된 순간부터 끝날
    때까지, 이미 알고 있던(이전 스캔에서 발견된) 폴더들이 화면에서 일시적으로
    사라졌다가 스캔이 진행되면서 서서히 다시 나타났다. 게다가 느린 원격 마운트 등
    때문에 스캔이 중간에 타임아웃/오류로 멈추면, 그 시점까지 발견된 폴더만 남고
    나머지는 다음 스캔이 성공하기 전까지 계속 "제외 목록"에서도, "포함 목록"에서도
    보이지 않아 아예 손을 댈 수 없는 상태가 됐다. 그래서 이제는 발견되는 즉시
    "추가"만 하고, 정말로 사라진 폴더 정리(prune_platform_folder_refs)는 스캔이
    끝까지 완주했을 때만 별도로 한다."""
    refs = _state["folder_refs"].setdefault(platform, [])
    if series_ref not in refs:
        refs.append(series_ref)


def prune_platform_folder_refs(platform: str, seen_refs: set[str]) -> None:
    """스캔이 끝까지 완주했을 때만 호출해야 한다 - 이번 스캔에서 다시 발견되지 않은
    (폴더가 실제로 삭제된) 것만 폴더 목록에서 정리한다. prune_platform_series와
    똑같은 안전 원칙: 중간에 멈춘 스캔의 결과로는 절대 호출하면 안 된다(아직 못
    훑은 뒷부분의 폴더까지 "사라졌다"고 오판해서 지워버리게 되므로)."""
    existing = _state["folder_refs"].get(platform, [])
    _state["folder_refs"][platform] = [ref for ref in existing if ref in seen_refs]


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
