"""
화 전환 겹침(리캡) 감지 회귀 테스트. 실제 서로 다른 세션에서 압축된 것처럼 매칭 점수가
페이지마다 들쭉날쭉해도, "위치가 순차적으로 이어지는지"로 정확히 판단해야 한다.

테스트 이미지는 일부러 단색이 아니라 그라디언트+도형을 넣어서 진짜 사진/일러스트에
가까운 텍스처를 갖게 한다 - OpenCV의 정규화 교차상관(TM_CCOEFF_NORMED)은 완전한
단색 이미지에서는 분산이 0이 되어 결과가 정의되지 않으므로, 순수 단색으로는 이
알고리즘이 원래 의도한 동작을 검증할 수 없다.
"""

import io
import os
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from app import overlap


def _make_page(width, height, seed):
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        arr[y, :, 0] = (seed * 7 + y // 3) % 255
        arr[y, :, 1] = (seed * 13 + y // 5) % 255
    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse([50, 50 + (seed % 5) * 20, 150, 150 + (seed % 5) * 20], fill=(seed * 30 % 255, 0, 0))
    draw.rectangle([200, 300, 350, 450], fill=(0, seed * 40 % 255, 0))
    return img


def _zip_pages(path, pages):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i, img in enumerate(pages):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            zf.writestr(f"{i + 1:04d}.jpg", buf.getvalue())


def test_no_overlap_when_chapters_are_unrelated(tmp_path):
    """GIVEN 서로 완전히 무관한 두 회차가 있을 때"""
    prev = tmp_path / "prev.zip"
    next_ = tmp_path / "next.zip"
    _zip_pages(str(prev), [_make_page(400, 600, seed=i) for i in range(5)])
    _zip_pages(str(next_), [_make_page(400, 600, seed=i + 100) for i in range(5)])

    """WHEN 겹침을 계산하면"""
    result = overlap.compute_overlap_pages(str(prev), str(next_))

    """THEN 0페이지로 나온다"""
    assert result == 0


def test_detects_overlap_even_with_noisy_per_page_scores(tmp_path):
    """GIVEN 다음 화 맨 앞 3페이지가 이전 화 끝 3페이지와 정확히 같은 순서로 이어지는 경우"""
    shared_pages = [_make_page(400, 600, seed=i + 1) for i in range(3)]
    prev_only = [_make_page(400, 600, seed=90)]
    next_only = [_make_page(400, 600, seed=91), _make_page(400, 600, seed=92)]

    prev = tmp_path / "prev.zip"
    next_ = tmp_path / "next.zip"
    _zip_pages(str(prev), prev_only + shared_pages)
    _zip_pages(str(next_), shared_pages + next_only)

    """WHEN 겹침을 계산하면"""
    result = overlap.compute_overlap_pages(str(prev), str(next_))

    """THEN 겹치는 3페이지가 정확히 감지된다"""
    assert result == 3


def test_overlap_stops_at_the_point_sequence_breaks(tmp_path):
    """GIVEN 앞의 2페이지만 진짜로 이어지고, 그 다음은 무관한 내용일 때"""
    shared_pages = [_make_page(400, 600, seed=1), _make_page(400, 600, seed=2)]
    unrelated_after = [_make_page(400, 600, seed=i + 200) for i in range(3)]

    prev = tmp_path / "prev.zip"
    next_ = tmp_path / "next.zip"
    _zip_pages(str(prev), [_make_page(400, 600, seed=99)] + shared_pages)
    _zip_pages(str(next_), shared_pages + unrelated_after)

    """WHEN 겹침을 계산하면"""
    result = overlap.compute_overlap_pages(str(prev), str(next_))

    """THEN 정확히 2페이지에서 멈춘다(그 뒤 무관한 내용까지 잘못 포함하지 않음)"""
    assert result == 2
