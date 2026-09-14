import os
import zipfile
import tempfile
import pytest
from unittest.mock import MagicMock
from app import scan, db

def create_dummy_cbz(path, image_names):
    """테스트용 dummy cbz(zip) 파일을 생성합니다."""
    with zipfile.ZipFile(path, 'w') as zf:
        for name in image_names:
            zf.writestr(name, b"fake image data")

@pytest.fixture
def mock_library(monkeypatch):
    """임시 디렉토리를 생성하고 LIBRARY_ROOT를 설정하는 피스처입니다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # scan 모듈의 LIBRARY_ROOT 변수를 임시 디렉토리로 교체
        monkeypatch.setattr(scan, "LIBRARY_ROOT", tmpdir)
        
        # DB 관련 호출은 빈 결과를 반환하도록 모킹 (제외된 시리즈 없음)
        monkeypatch.setattr(db, "get_excluded_series", MagicMock(return_value=set()))
        
        yield tmpdir

def test_cbz_file_recognition(mock_library):
    """
    .cbz 확장자를 가진 파일이 시리즈 내의 회차로 올바르게 인식되는지 확인합니다.
    """
    platform = "naver"
    series_name = "CBZ_테스트_웹툰"
    
    # 1. 폴더 구조 생성: /library/naver/CBZ_테스트_웹툰
    platform_path = os.path.join(mock_library, platform)
    series_path = os.path.join(platform_path, series_name)
    os.makedirs(series_path)
    
    # 2. .cbz 파일 생성
    cbz_filename = "001 1화.cbz"
    cbz_path = os.path.join(series_path, cbz_filename)
    create_dummy_cbz(cbz_path, ["001.jpg", "002.png"])
    
    # 3. 스캐너 실행
    results = list(scan.iter_platform_series_streaming(platform))
    
    # 4. 검증
    assert len(results) == 1
    series_ref, series_entry, chapters_map = results[0]
    
    assert series_ref == series_name
    assert series_entry["title"] == series_name
    assert len(series_entry["chapters"]) == 1
    
    chapter = series_entry["chapters"][0]
    assert chapter["filename"] == cbz_filename
    assert chapter["label"] == "1화"
    assert chapter["sort_key"] == 1

def test_cbz_image_listing(mock_library):
    """
    .cbz 파일 내부의 이미지 파일 목록을 자연 정렬 순서로 잘 가져오는지 확인합니다.
    """
    platform = "kakao"
    series_name = "이미지_목록_테스트"
    series_path = os.path.join(mock_library, platform, series_name)
    os.makedirs(series_path)
    
    cbz_path = os.path.join(series_path, "001_1화.cbz")
    # 이미지 순서가 섞여 있어도 자연 정렬되어야 함
    images = ["1.jpg", "10.jpg", "2.jpg", "non-image.txt"]
    create_dummy_cbz(cbz_path, images)
    
    # 이미지 목록 조회 함수 호출
    image_names = scan.list_zip_image_names(cbz_path)
    
    # 확장자 필터링 및 자연 정렬 확인
    assert image_names == ["1.jpg", "2.jpg", "10.jpg"]