"""
시리즈 커버(썸네일) 생성. 웹툰 첫 페이지는 세로로 아주 긴 원본 이미지(수 MB)인 경우가
흔해서, 그대로 내려주면 모바일에서 목록 화면이 매우 느려지고 무거워진다. 그래서
리사이즈+JPEG 압축한 결과를 원본 mtime 기준으로 캐싱해서 재사용한다.
"""

import io
import logging
import os
import zipfile

from PIL import Image

log = logging.getLogger("webtoon-server")

COVER_MAX_WIDTH = 320

IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# series_id -> (source_mtime, jpeg_bytes, media_type)
_cache: dict[str, tuple[float, bytes, str]] = {}


def get_cached_cover(series_id: str, source_mtime: float) -> tuple[bytes, str] | None:
    """원본이 그 사이에 바뀌지 않았으면(mtime 동일) 캐시된 것을 반환, 아니면 None."""
    cached = _cache.get(series_id)
    if cached and cached[0] == source_mtime:
        return cached[1], cached[2]
    return None


def generate_and_cache_cover_from_zip(series_id: str, source_mtime: float, zip_path: str, image_name: str) -> tuple[bytes, str] | None:
    with zipfile.ZipFile(zip_path) as zf:
        raw = zf.read(image_name)
    ext = os.path.splitext(image_name)[1].lower()
    result = _resize_and_compress(raw, ext, source_label=f"{zip_path}::{image_name}")
    if result is None:
        return None  # 원본을 읽다가 손상/일부만 읽힌 것으로 보여 캐시하지 않음(다음 시도에서 재시도)
    data, media_type = result
    _cache[series_id] = (source_mtime, data, media_type)
    return data, media_type


def generate_and_cache_cover_from_file(series_id: str, source_mtime: float, file_path: str) -> tuple[bytes, str] | None:
    """cover.jpg처럼 zip 밖에 별도로 있는 대표 이미지 파일로 커버를 만든다."""
    with open(file_path, "rb") as f:
        raw = f.read()
    ext = os.path.splitext(file_path)[1].lower()
    result = _resize_and_compress(raw, ext, source_label=file_path)
    if result is None:
        return None
    data, media_type = result
    _cache[series_id] = (source_mtime, data, media_type)
    return data, media_type


def _resize_and_compress(raw: bytes, ext: str, source_label: str) -> tuple[bytes, str] | None:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()  # 헤더만 읽고 실제 픽셀 데이터(본문)는 못 읽은 경우까지 여기서 확실히 걸러낸다.
    except Exception as e:
        # PIL이 열지도 못하면(또는 본문을 끝까지 못 읽으면) 이 raw 자체가 손상됐을 가능성이
        # 높다 - 특히 네트워크 드라이브에서 읽는 도중 연결이 끊기면 파일 앞부분만 읽히고
        # 뒷부분이 잘리는 경우가 실제로 있었다(원드라이브 탐색 타임아웃 로그로 확인됨).
        # 이걸 예전처럼 "원본이라도 보여주자"며 그대로 캐시해버리면, 캐시는 만료 시간이
        # 없어서 이 손상된 상태가 원본 mtime이 바뀌기 전까지 영원히 남아있게 된다.
        # 그래서 여기서는 캐시하지 않고 None을 반환해 다음 시도에서 다시 읽어보게 한다.
        log.warning(f"커버 이미지를 열 수 없음(읽는 중 손상/잘렸을 수 있음), 캐시하지 않고 다음에 재시도 - {source_label} ({e})")
        return None

    try:
        img = img.convert("RGB")
        if img.width > COVER_MAX_WIDTH:
            ratio = COVER_MAX_WIDTH / img.width
            new_height = max(1, round(img.height * ratio))
            img = img.resize((COVER_MAX_WIDTH, new_height), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=78, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        # 여기까지 왔다면 이미지 자체는 정상적으로 다 읽혔다는 뜻이라(위 img.load()까지
        # 통과), 리사이즈/재인코딩 단계만의 문제로 보고 원본을 그대로 캐시해도 안전하다.
        log.warning(f"커버 이미지 리사이즈 실패, 원본으로 대체 - {source_label} ({e})")
        return raw, IMAGE_MEDIA_TYPES.get(ext, "application/octet-stream")
