"""
화 전환 시 중복(리캡) 페이지 감지. 다음 화 맨 앞부분이 이전 화 끝부분과 픽셀 단위로
겹치는지 이미지 매칭(OpenCV 템플릿 매칭)으로 확인하고, 겹치는 페이지 수를 계산해 캐싱한다.

판단 기준: 처음에는 "매칭 점수가 0.9 이상이면 겹침"이라는 단일 기준을 썼는데, 서로 다른
세션/도구로 압축된 zip 두 개를 실제로 비교해보니 진짜 겹치는 페이지인데도 압축 차이 때문에
점수가 0.4~0.97까지 들쭉날쭉하게 나오는 경우가 있었다(반대로 완전히 무관한 페이지는
0.3~0.55 정도). 점수 하나만으로는 이 두 경우를 안정적으로 구분할 수 없어서, 대신
"연속된 페이지들의 매칭 위치가 페이지 높이만큼씩 순차적으로 이어지는지"를 주된 근거로
삼는다 - 진짜 겹침이면 다음 화의 1, 2, 3...페이지가 검색 이미지에서 정확히 그 순서/간격
그대로 이어지는 위치에서 발견되는데, 무관한 페이지 여러 장이 우연히 이런 정확한 순차
패턴을 만들어낼 가능성은 매우 낮다. 매칭 점수는 "완전히 무관한 매칭"만 걸러내는 낮은
하한선으로만 쓰고, 최소 OVERLAP_MIN_RUN장 이상 연속으로 이어져야만 겹침으로 인정해서
(우연한 한두 장짜리 오탐 방지) 오탐이 콘텐츠 유실로 이어지는 위험을 낮춘다.
"""

import asyncio
import io
import logging
import zipfile

import cv2
import numpy as np
from PIL import Image

from . import catalog, db
from .scan import list_zip_image_names

log = logging.getLogger("webtoon-server")

# 이 밑이면 "완전히 무관한 매칭"으로 보고 배제한다. 실측 결과 무관한 페이지는 0.3~0.55
# 정도였는데, 압축 차이 때문에 진짜 겹치는 페이지도 낮으면 0.4대까지 나올 수 있어서,
# 이 값 자체는 "확실히 무관한" 것만 걸러내는 낮은 하한선으로만 쓰고 실제 판단은 아래
# 위치 순차성으로 한다.
OVERLAP_THRESHOLD = 0.35
OVERLAP_POSITION_TOLERANCE_RATIO = 0.05  # 페이지 높이의 5%까지는 위치 오차로 허용
OVERLAP_MIN_RUN = 2  # 최소 이 개수 이상 연속으로 이어져야 겹침으로 인정(우연한 오탐 방지)
OVERLAP_MAX_CHECK = 10  # 다음 화 맨 앞에서 최대 몇 장까지 검사할지
OVERLAP_TAIL_PAGES = 15  # 이전 화 끝에서 몇 장을 검색 대상으로 삼을지

_precompute_lock = asyncio.Lock()


def _stitch_gray_vertical(zip_path: str, image_names: list[str]):
    """zip 안의 이미지 여러 장을 세로로 이어붙여 흑백 numpy 배열로 반환."""
    if not image_names:
        return None
    images = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in image_names:
            raw = zf.read(name)
            images.append(Image.open(io.BytesIO(raw)).convert("L"))
    width = images[0].width
    total_height = sum(image.height for image in images)
    canvas = Image.new("L", (width, total_height))
    y = 0
    for image in images:
        if image.width != width:
            image = image.resize((width, max(1, round(image.height * width / image.width))))
        canvas.paste(image, (0, y))
        y += image.height
    return np.array(canvas)


def compute_overlap_pages(prev_zip_path: str, next_zip_path: str) -> int:
    """다음 화 zip 맨 앞부터 몇 장이 이전 화 zip 끝부분과 겹치는지 계산."""
    try:
        prev_names = list_zip_image_names(prev_zip_path)
        next_names = list_zip_image_names(next_zip_path)
        if not prev_names or not next_names:
            return 0

        search_image = _stitch_gray_vertical(prev_zip_path, prev_names[-OVERLAP_TAIL_PAGES:])
        if search_image is None:
            return 0

        # 먼저 각 페이지별로 "가장 비슷한 위치와 그때의 점수"를 전부 구해둔다 - 판단은
        # 그 다음 단계(위치 순차성 확인)에서 한다.
        candidates = []  # (매칭된 y 위치, 매칭 점수, 이 페이지의 높이)
        with zipfile.ZipFile(next_zip_path) as zf:
            for name in next_names[:OVERLAP_MAX_CHECK]:
                raw = zf.read(name)
                template = np.array(Image.open(io.BytesIO(raw)).convert("L"))
                if template.shape[0] > search_image.shape[0] or template.shape[1] != search_image.shape[1]:
                    break
                result = cv2.matchTemplate(search_image, template, cv2.TM_CCOEFF_NORMED)
                _, score, _, max_loc = cv2.minMaxLoc(result)
                candidates.append((max_loc[1], score, template.shape[0]))

        matched_count = 0
        expected_pos = None
        for pos, score, height in candidates:
            if score < OVERLAP_THRESHOLD:
                break
            if expected_pos is not None:
                tolerance = max(20, height * OVERLAP_POSITION_TOLERANCE_RATIO)
                if abs(pos - expected_pos) > tolerance:
                    break
            matched_count += 1
            expected_pos = pos + height

        if matched_count < OVERLAP_MIN_RUN:
            return 0

        # 회차 전체가 겹친다고 나오면 뭔가 잘못된 것 - 안전하게 최소 1장은 남김
        if matched_count >= len(next_names):
            matched_count = len(next_names) - 1
        return max(matched_count, 0)
    except Exception as e:
        log.warning(f"회차 간 겹침 감지 실패, 건너뛰지 않음: {e}")
        return 0


async def precompute_overlaps() -> None:
    """
    재스캔 직후 호출되는 백그라운드 작업. 아직 계산된 적 없는 화 전환(연속된 두 회차)만
    골라서 겹침을 미리 계산해 캐싱해둔다. 요청 처리를 막지 않도록 각 계산은 스레드로 돌리고,
    이미 실행 중이면 중복 실행하지 않는다.
    """
    if _precompute_lock.locked():
        return
    async with _precompute_lock:
        pending = []
        for series in catalog.get_series_map().values():
            chapters = series["chapters"]
            for i in range(1, len(chapters)):
                next_chapter_id = chapters[i]["id"]
                if db.get_cached_overlap(next_chapter_id) is None:
                    pending.append((chapters[i - 1], chapters[i]))

        if not pending:
            return

        log.info(f"화 전환 겹침 사전 계산 시작 - {len(pending)}건")
        computed = 0
        found_overlaps = 0
        for prev_chapter, next_chapter in pending:
            try:
                skip_pages = await asyncio.to_thread(
                    compute_overlap_pages, prev_chapter["path"], next_chapter["path"]
                )
                db.set_cached_overlap(next_chapter["id"], prev_chapter["id"], skip_pages)
                computed += 1
                if skip_pages > 0:
                    found_overlaps += 1
            except Exception:
                log.exception(f"겹침 사전 계산 실패 (건너뛰고 계속): {next_chapter['id']}")
        log.info(
            f"화 전환 겹침 사전 계산 완료 - {computed}/{len(pending)}건 처리, "
            f"그중 겹침 발견 {found_overlaps}건"
        )
