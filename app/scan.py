"""
파일시스템에서 웹툰 라이브러리를 스캔하고, zip 파일명에서 (정렬키, 회차 라벨)을 뽑아낸다.

라이브러리 구조: LIBRARY_ROOT 바로 아래 1depth = 플랫폼(예: 네이버/카카오),
그 아래 1depth = 시리즈(웹툰) 폴더, 그 안의 zip 파일들 = 회차.

일부 플랫폼(카카오 등)의 다운로드 도구는 시리즈 폴더 안에 대표 이미지(cover.jpg)와
메타데이터(info.xml, ComicInfo 표준 포맷)를 같이 남겨두는데, 있으면 활용한다.
"""

import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile

from . import db

log = logging.getLogger("webtoon-server")

LIBRARY_ROOT = os.environ.get("LIBRARY_ROOT", "/library")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
COVER_FILENAMES = {"cover.jpg", "cover.jpeg", "cover.png", "cover.webp"}
INFO_FILENAME = "info.xml"


def natural_key(name: str):
    """zip 내부 이미지 파일명을 1,2,3...10 순서로(문자열 사전순이 아니라) 정렬하기 위한 키."""
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", name)]


def make_id(*parts: str) -> str:
    return hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()[:12]


def _clean_title(text: str, strip_trailing_hash: bool = True) -> str:
    """추출된 제목 후보에서 구분자/완결표시/날짜형 숫자/장식성 특수문자를 정리."""
    # 앞뒤에 붙는 구분자류 제거 (마커 앞/뒤 어느 쪽 텍스트든 동일하게 적용)
    text = re.sub(r"^[\s\-–—.:：·‧․・,]+", "", text)
    text = re.sub(r"[\s\-–—.:：·‧․・,]+$", "", text)
    if strip_trailing_hash:
        # 카카오식 파일명 끝의 #숫자(회차 제목과 무관한 부가 번호) 제거
        text = re.sub(r"#\d+$", "", text)
    # 완결 표시 제거
    text = re.sub(r"\(完\)|\(완\)|완결", "", text)
    # 날짜로 추정되는 "숫자-숫자" 패턴 제거 (예: 6-28)
    text = re.sub(r"\b\d{1,2}-\d{1,2}\b", "", text)
    # 장식성 특수문자 제거 (단어 사이에 있을 수 있으니 공백으로 치환 후 나중에 정리)
    text = re.sub(r"[？！～·‧․・•●○◆■□※]", " ", text)
    # 끝에 남은 "(숫자)"는 " 숫자"로 (예: (2) -> " 2")
    text = re.sub(r"\((\d+)\)\s*$", r" \1", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text


_SEPARATOR_CLASS = r"[\s：:\-–—·‧․・,]*"


def _series_prefix_length(content: str, series_name: str) -> int:
    """
    content가 series_name으로 시작하면 그 길이를 반환(없으면 0).
    "：" vs 공백처럼 구두점/공백 표기가 실제 파일명과 달라도 매칭되도록,
    시리즈명을 구분자 기준으로 쪼갠 뒤 구분자 자리는 느슨하게(0개 이상) 허용한다.
    """
    if not series_name:
        return 0
    parts = [p for p in re.split(r"[\s：:\-–—·‧․・,]+", series_name) if p]
    if not parts:
        return 0
    pattern = r"^" + _SEPARATOR_CLASS.join(re.escape(p) for p in parts) + _SEPARATOR_CLASS
    match = re.match(pattern, content)
    if match and match.end() > 0:
        return match.end()
    return 0


def parse_chapter_label(stem: str, series_name: str = "") -> tuple[int, str]:
    """
    zip 파일명(확장자 제외)에서 (정렬키, 표시라벨) 추출.

    예)
      "103 마법사랑해 100화 - 아스라이 스러지는 (7)" -> (103, "100화 · 아스라이 스러지는 7")
      "172 나이트런 Extra story - 1화" (series=나이트런) -> (172, "Extra story 1화")
      "651 신의 탑 3부 233화" (series=신의 탑)          -> (651, "3부 233화")
      "0004_1화#64"                                  -> (4,   "1화")
      "0003_프롤로그#48"                              -> (3,   "프롤로그")
      "104 마법사랑해 번외편 - 르네의 일기"           -> (104, "번외편 - 르네의 일기")
      "117 기기괴괴2 절멸의 도시 #2" (series=기기괴괴2) -> (117, "절멸의 도시 2")
      "017 로도스도 전기  사령의 여왕 제16화 ..." (series="로도스도 전기 ： 사령의 여왕")
                                                     -> (17, "16화 · ...")
    """
    sort_match = re.match(r"^(\d+)", stem)
    sort_key = int(sort_match.group(1)) if sort_match else 0

    # 맨 앞 정렬번호 뒤 구분자(_ 또는 공백)로 카카오식/네이버식을 구분
    # 카카오: "0004_1화#64" (언더스코어), 네이버: "103 마법사랑해 ..." (공백)
    is_underscore_style = bool(re.match(r"^\d+_", stem))

    rest = re.sub(r"^\d+[_\s]*", "", stem)

    # 파일명이 시리즈 폴더명으로 시작하면 그 부분은 제거 (제목 추출에 방해되지 않도록).
    # 폴더명과 파일명의 구두점 표기가 달라도(예: "：" vs 공백) 매칭되도록 느슨하게 비교.
    # 시리즈명이 여러 번 반복될 수도 있으니(예: "나이트런 나이트런 신세계 ...")
    # 더 이상 안 잘릴 때까지 반복해서 제거
    content = rest
    while True:
        prefix_length = _series_prefix_length(content, series_name)
        if not prefix_length:
            break
        content = content[prefix_length:]

    # "화" 앞에 "제"가 붙는 표기("제16화")는 "제"를 회차 번호의 일부로 취급해서
    # 부제 프리픽스에 안 남게 함
    marker_match = re.search(r"(?:제\s*)?(\d+)\s*화", content)
    if marker_match:
        # 앞자리 0은 떼고 표시 (예: "030화" -> "30화")
        marker = f"{int(marker_match.group(1))}화"
        # "화" 앞에 붙는 텍스트(부제/시즌 표시 등)도 그대로 살림 (예: "Extra story", "3부")
        prefix = _clean_title(content[: marker_match.start()], strip_trailing_hash=False)
        suffix = _clean_title(content[marker_match.end():])
        label = f"{prefix} {marker}" if prefix else marker
        if suffix:
            label = f"{label} · {suffix}"
        return sort_key, label

    if not is_underscore_style:
        # "N화" 표시가 없는 네이버식 파일명: "부제목 #번호" 패턴 시도 (예: 기기괴괴2)
        hash_matches = list(re.finditer(r"#(\d+)", content))
        if hash_matches:
            last_hash_match = hash_matches[-1]
            number = last_hash_match.group(1)
            title_part = _clean_title(content[: last_hash_match.start()], strip_trailing_hash=False)
            if title_part:
                return sort_key, f"{title_part} {number}"

    # 그 외: "화" 표시도 "#번호"도 없는 경우 (예: 번외편)
    label = _clean_title(content, strip_trailing_hash=True)
    if not label:
        label = stem
    return sort_key, label


def list_platforms_in_scan_order() -> list[str]:
    """
    스캔할 플랫폼 폴더 목록을, 로컬이 먼저 오고 네트워크 드라이브가 나중에 오도록 정렬해서
    반환한다. 어떤 폴더가 "느린(네트워크) 저장소"인지는 컨테이너 안에서 자동으로 알아낼
    방법이 마땅치 않아서(마운트 방식이 여러 가지라 자동 판별이 애매함), 환경변수
    SLOW_PLATFORMS(콤마로 구분한 플랫폼 폴더명)로 직접 지정하게 한다. 지정된 플랫폼은
    항상 뒤로 미뤄지고, 나머지는 기존처럼 이름순이다.
    """
    if not os.path.isdir(LIBRARY_ROOT):
        return []
    platforms = [
        name for name in sorted(os.listdir(LIBRARY_ROOT))
        if os.path.isdir(os.path.join(LIBRARY_ROOT, name))
    ]
    slow = {p.strip() for p in os.environ.get("SLOW_PLATFORMS", "").split(",") if p.strip()}
    fast_platforms = [p for p in platforms if p not in slow]
    slow_platforms = [p for p in platforms if p in slow]
    return fast_platforms + slow_platforms


def is_slow_platform(platform: str) -> bool:
    """SLOW_PLATFORMS 환경변수에 지정된 플랫폼인지(=네트워크 드라이브로 취급) 여부."""
    slow = {p.strip() for p in os.environ.get("SLOW_PLATFORMS", "").split(",") if p.strip()}
    return platform in slow


def _scan_series_at_path(platform: str, series_ref: str, series_path: str) -> tuple[dict, dict] | None:
    """
    시리즈 폴더 하나의 실제 내용(회차 zip 목록, 커버, info.xml)을 읽어서
    (series_entry, chapters_map)을 만든다. 경로가 이미 확정된 상태에서 호출한다 -
    호출하는 쪽(발견 즉시 스캔하는 제너레이터든, 폴더 하나만 다시 스캔하는 함수든)이
    같은 로직을 중복 없이 공유하기 위해 따로 뺀 것.
    """
    series_name = os.path.basename(series_ref)
    zip_filenames = [f for f in os.listdir(series_path) if f.lower().endswith(".zip")]
    if not zip_filenames:
        return None

    series_id = make_id(platform, series_ref)
    chapters = []
    chapters_map = {}
    for zip_filename in zip_filenames:
        stem = zip_filename[:-4]
        sort_key, label = parse_chapter_label(stem, series_name)
        chapter_id = make_id(platform, series_ref, zip_filename)
        full_path = os.path.join(series_path, zip_filename)
        chapters.append(
            {
                "id": chapter_id,
                "label": label,
                "sort_key": sort_key,
                "filename": zip_filename,
                "path": full_path,
            }
        )
        chapters_map[chapter_id] = full_path

    chapters.sort(key=lambda chapter: (chapter["sort_key"], chapter["filename"]))
    latest_mtime = max((os.path.getmtime(chapter["path"]) for chapter in chapters), default=0)

    series_entry = {
        "id": series_id,
        "platform": platform,
        "title": series_name,
        "path": series_path,
        "chapters": chapters,
        "latest_mtime": latest_mtime,
        "cover_path": _find_series_cover(series_path),
        "info": _parse_series_info(series_path),
    }
    return series_entry, chapters_map


def scan_single_series(platform: str, series_ref: str) -> tuple[dict, dict] | None:
    """
    시리즈 폴더 딱 하나만 스캔한다(제외했다가 다시 포함시킬 때, 플랫폼 전체를 다시
    스캔할 필요 없이 이 폴더 하나만 반영하기 위함). 반환값은 (series_entry, chapters_map)
    이거나, 폴더가 없거나 zip이 없으면 None. (재포함 액션 전용이라 제외 목록 확인을
    일부러 안 한다 - 호출하는 쪽에서 "방금 제외를 풀었다"는 걸 이미 알고 부르는 것이므로.)
    """
    platform_path = os.path.join(LIBRARY_ROOT, platform)
    series_path = os.path.join(platform_path, *series_ref.split("/"))
    if not os.path.isdir(series_path):
        return None
    return _scan_series_at_path(platform, series_ref, series_path)


def iter_platform_series_streaming(platform: str):
    """
    플랫폼 폴더를 훑으면서, 시리즈 폴더를 "발견하는 즉시"(전체 폴더 구조를 다 훑기를
    기다리지 않고) 그 자리에서 스캔까지 마쳐서 (series_ref, series_entry, chapters_map)을
    하나씩 내놓는 제너레이터다. "폴더 목록을 전부 모은 뒤에 하나씩 스캔"이 아니라
    "발견 하나당 스캔 하나"라서, 네트워크 드라이브에 폴더가 아주 많아도 첫 번째 결과가
    나오기까지 전체 탐색이 끝나길 기다릴 필요가 없다.

    제외된 (platform, series_ref) 조합은 실제 내용(zip 목록 등)은 절대 열어보지 않지만,
    "이런 폴더가 있다"는 것 자체는 여전히 알려줘야 설정 패널의 "제외된 폴더" 목록에서
    다시 포함시킬 수 있다 - 그래서 series_entry/chapters_map을 None으로 해서 yield한다
    (호출하는 쪽에서 series_entry가 None이면 "발견은 했지만 스캔은 안 함"으로 처리할 것).
    """
    platform_path = os.path.join(LIBRARY_ROOT, platform)
    if not os.path.isdir(platform_path):
        return
    excluded = db.get_excluded_series()
    for dirpath, dirnames, filenames in os.walk(platform_path):
        dirnames.sort()
        has_zip = any(f.lower().endswith(".zip") for f in filenames)
        if not has_zip:
            continue
        dirnames.clear()  # 시리즈 폴더로 인식된 곳 안쪽은 더 내려가지 않음(중복/오인 방지)
        series_ref = os.path.relpath(dirpath, platform_path).replace(os.sep, "/")
        if (platform, series_ref) in excluded:
            yield series_ref, None, None
            continue
        result = _scan_series_at_path(platform, series_ref, dirpath)
        if result:
            series_entry, chapters_map = result
            yield series_ref, series_entry, chapters_map
        else:
            yield series_ref, None, None


def list_platform_series_refs(platform: str) -> list[str]:
    """
    플랫폼 폴더 안의 시리즈 후보 경로를 전부(제외 여부와 무관하게) 나열한다. 설정 패널의
    "스캔 중/제외된 폴더" 목록을 만드는 데 쓴다 - 이 목록 자체는 화면에 보여주기만 할 뿐
    실제 회차 스캔은 안 하니, 값이 좀 걸려도(네트워크 드라이브 폴더가 아주 많은 경우)
    전체 스캔 파이프라인을 막지는 않는다(호출하는 쪽에서 타임아웃을 씌워 쓴다).
    """
    platform_path = os.path.join(LIBRARY_ROOT, platform)
    if not os.path.isdir(platform_path):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(platform_path):
        dirnames.sort()
        if any(f.lower().endswith(".zip") for f in filenames):
            found.append(os.path.relpath(dirpath, platform_path).replace(os.sep, "/"))
            dirnames.clear()
    found.sort()
    return found


def scan_library() -> tuple[dict, dict]:
    """전체 라이브러리를 한 번에 스캔(플랫폼 우선순위 순서로 훑되, 결과는 합쳐서 반환).
    점진적으로 반영하고 싶으면 iter_platform_series_streaming()을 플랫폼별로 쓸 것."""
    series_map = {}
    chapters_map = {}
    for platform in list_platforms_in_scan_order():
        for series_ref, series_entry, s_chapters in iter_platform_series_streaming(platform):
            series_map[series_entry["id"]] = series_entry
            chapters_map.update(s_chapters)
    return series_map, chapters_map


def list_zip_image_names(zip_path: str) -> list[str]:
    """zip 안의 이미지 파일명을 자연 정렬 순서(1,2,3...10)로 반환."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [
            name
            for name in zf.namelist()
            if not name.endswith("/") and os.path.splitext(name)[1].lower() in IMAGE_EXTS
        ]
    names.sort(key=natural_key)
    return names


def _find_series_cover(series_path: str) -> str | None:
    """시리즈 폴더 바로 아래 cover.jpg(류) 파일이 있으면 절대경로를, 없으면 None을 반환."""
    for name in os.listdir(series_path):
        if name.lower() in COVER_FILENAMES:
            return os.path.join(series_path, name)
    return None


def _parse_series_info(series_path: str) -> dict | None:
    """
    시리즈 폴더의 info.xml(ComicInfo 표준 포맷)을 읽어 표시에 쓸만한 필드만 뽑아 반환.
    파일이 없거나 파싱에 실패하면 None (네이버 등 info.xml이 없는 플랫폼에서는 항상 None).

    SeriesStatus(연재/완결)는 실제로 값이 정확하지 않은 경우가 있어 아예 뽑지 않는다.
    """
    info_path = os.path.join(series_path, INFO_FILENAME)
    if not os.path.isfile(info_path):
        return None
    try:
        root = ET.parse(info_path).getroot()
    except Exception as e:
        log.warning(f"info.xml 파싱 실패, 무시하고 진행: {info_path} ({e})")
        return None

    def text(tag: str) -> str:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    genre_raw = text("Genre")
    genres = [g.strip() for g in genre_raw.split(",") if g.strip()]

    info = {
        "summary": text("Summary"),
        "writer": text("Writer"),
        "genre": genre_raw,
        "genres": genres,
        "age_rating": text("AgeRating"),
        "web_url": text("Web"),
        "publisher": text("Publisher"),
        "notes": text("Notes"),
        "cover_artist": text("CoverArtist"),
    }
    # 전부 빈 값이면 사실상 쓸모없으니 None 취급
    return info if any(info[k] for k in info if k != "genres") or genres else None
