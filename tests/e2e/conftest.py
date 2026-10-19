"""
브라우저(Playwright) 테스트 전용 픽스처 - 실제 서버를 띄워 화면 동작을 검증한다.

이 폴더는 일부러 따로 실행한다(`python -m pytest tests/e2e`). pytest-playwright의 동기 API가
메인 스레드에 이벤트 루프를 남겨서, 같은 실행에서 뒤에 오는 기존 테스트의 asyncio.run()이
"already running" 오류로 깨지기 때문이다. 기본 실행(`pytest tests`)에서는 pytest.ini가 제외한다.
"""

import os

import pytest

from app.main import app
from conftest import make_chapter_zip


LONG_TITLE = "천재소녀가 무림공적을 숨김 그리고 아주 길고 긴 제목이 계속 이어져서 두 줄을 훌쩍 넘어가는 웹툰"
LONG_SUMMARY = "무림공적을 숨긴 천재소녀의 귀엽 뽀짝 무협 모험기. " * 40


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """playwright install을 못 하는 환경(미리 설치된 크로미엄만 있는 곳)에서는
    PLAYWRIGHT_CHROMIUM_EXECUTABLE에 그 경로를 지정해 쓴다. 없으면 Playwright 기본값."""
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


@pytest.fixture
def live_server(library):
    """실제 uvicorn 서버를 백그라운드 스레드로 띄우고 주소를 돌려준다."""
    import threading
    import time

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("테스트 서버가 시작되지 않았습니다")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def webtoon_site(live_server, library):
    """
    목록 화면 테스트용 라이브러리. 정주행 중 2개(긴 제목 + info.xml이 있는 것 / 짧은 제목)와
    나중에 읽기 1개가 스캔된 상태. 서버 주소를 돌려준다.
    """
    import httpx

    def add(platform: str, title: str) -> None:
        make_chapter_zip(str(library / platform / title / "001.zip"))
        make_chapter_zip(str(library / platform / title / "002.zip"))

    add("카카오", LONG_TITLE)
    add("카카오", "열렙전사")
    add("네이버", "묵향 다크레이디")
    (library / "카카오" / LONG_TITLE / "info.xml").write_text(
        f"<ComicInfo><Writer>환운</Writer><Summary>{LONG_SUMMARY}</Summary>"
        "<Web>https://example.com/original</Web></ComicInfo>",
        encoding="utf-8",
    )
    httpx.post(f"{live_server}/api/rescan", timeout=30)
    series = {s["title"]: s["id"] for s in httpx.get(f"{live_server}/api/series").json()}
    httpx.put(f"{live_server}/api/series-lists", json={"series_ids": [series["묵향 다크레이디"]], "list": "later"})
    return live_server
