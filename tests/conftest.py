"""
pytest 공용 픽스처.

핵심 설계: scan.LIBRARY_ROOT / db.DB_PATH는 각 모듈이 import될 때 딱 한 번 환경변수를
읽어서 고정하는 값이라(운영 코드 자체를 바꾸지 않기 위해 일부러 이 방식을 그대로 둠),
테스트마다 다른 임시 폴더/DB를 쓰려면 그 값을 직접(monkeypatch) 바꿔줘야 한다. 이렇게
하면 운영 코드는 한 줄도 안 바뀌고, 테스트만 격리된 상태로 돌 수 있다.
"""

import io
import os
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# app.main을 import하는 순간 StaticFiles(directory=STATIC_DIR)가 그 폴더의 존재를
# 확인하므로, import 전에 실제 존재하는 경로를 넣어둬야 한다(내용은 안 씀 - /api/* 만 테스트).
os.environ.setdefault("STATIC_DIR", os.path.join(_REPO_ROOT, "static"))
os.environ.setdefault("LIBRARY_ROOT", "/tmp/webtoon_test_unused_library")
os.environ.setdefault("DB_PATH", "/tmp/webtoon_test_unused_progress.db")
os.makedirs(os.environ["LIBRARY_ROOT"], exist_ok=True)

from app import catalog, db, scan  # noqa: E402
from app.main import app  # noqa: E402


def make_chapter_zip(path: str, page_count: int = 3, width: int = 400, height: int = 600, start_color: int = 0) -> None:
    """테스트용 회차 zip을 만든다. 실제 유효한 JPEG을 넣어야 zip 목록/커버 생성 등이
    정상적으로 동작을 검증할 수 있다(가짜 바이트로는 PIL이 아예 못 열어서 다른 코드
    경로를 타게 됨)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(page_count):
            img = Image.new("RGB", (width, height), color=((start_color + i * 20) % 255, 100, 150))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            zf.writestr(f"{i + 1:04d}.jpg", buf.getvalue())


@pytest.fixture
def library(tmp_path, monkeypatch):
    """
    빈 라이브러리 폴더 + 빈 DB로 격리된 상태를 만든다. scan.LIBRARY_ROOT / db.DB_PATH를
    이 테스트 전용 경로로 바꿔치기하고, 메모리 카탈로그도 완전히 비워서 이전 테스트의
    잔여 상태가 절대 섞이지 않게 한다.
    """
    lib_root = tmp_path / "library"
    lib_root.mkdir()
    db_path = tmp_path / "progress.db"

    monkeypatch.setattr(scan, "LIBRARY_ROOT", str(lib_root))
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_schema()

    catalog._state["series"] = {}
    catalog._state["chapters"] = {}
    catalog._state["last_scan_at"] = None
    catalog._state["folder_refs"] = {}
    catalog._state["known_platforms"] = []

    yield lib_root


@pytest.fixture
def client(library):
    """
    API 테스트용 TestClient. lifespan(시작 시 초기 스캔 등)까지 실제로 거치도록 with로
    연다. 초기 스캔은 백그라운드 태스크라 타이밍이 보장되지 않으므로, 라이브러리 내용을
    확실히 반영하고 싶은 테스트는 client.post("/api/rescan")을 명시적으로 호출해서
    동기적으로 스캔을 끝낸 뒤 검증할 것(굳이 sleep으로 기다리지 않아도 됨).
    """
    with TestClient(app) as c:
        yield c
