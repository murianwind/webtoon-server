"""
webtoon-server FastAPI 앱 진입점.

이 파일은 앱 생성, 미들웨어, 생명주기(시작 시 스캔/자동 재스캔 예약), 라우터 등록,
정적 프론트엔드 마운트만 담당한다. 실제 라우트 구현은 도메인별로 나뉜
app/routers/*.py에 있고, 여러 라우터가 공유하는 백그라운드 작업/헬퍼는
app/services.py에 있다:
  - routers/library.py   스캔 트리거 + 시리즈 폴더 제외/재포함
  - routers/series.py    시리즈 목록/조회, 이어보기, 진행률, 읽음 상태, 커버, info.xml
  - routers/chapters.py  회차 페이지 서빙, 화 전환 겹침 감지
  - routers/settings.py  앱 설정 저장/조회
  - routers/backup.py    백업/복원
  - services.py          위 라우터들이 공유하는 백그라운드 스캔/커버 사전생성 등
  - db.py       읽음 진행률 / 설정 / 제외목록 / 겹침캐시 / 백업·복원 (SQLite)
  - catalog.py  스캔 결과를 담아두는 메모리 상태
  - scan.py     파일시스템 스캔 + 회차 라벨 파싱
  - overlap.py  화 전환 겹침(리캡) 감지 알고리즘 + 백그라운드 사전계산
  - covers.py   시리즈 커버 썸네일 생성/캐싱
"""

import asyncio
import logging
import os
import re

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db, services
from .routers import backup, chapters, library, series, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("webtoon-server")

app = FastAPI(title="webtoon-server")

# 커버/페이지 이미지는 내용이 거의 안 바뀌니 캐싱이 오히려 유리해서 캐시 금지 대상에서 뺀다.
_CACHEABLE_IMAGE_PATH = re.compile(r"^/api/series/[^/]+/cover$|^/api/chapters/[^/]+/pages/\d+$")


@app.middleware("http")
async def cache_control_for_api(request, call_next):
    """
    /api/* 응답은 매번 최신 상태를 반영해야 하는 동적 데이터라(읽음 진행률, 안읽음 개수 등),
    브라우저가 임의로 캐싱하면 안 된다. 실제로 브라우저 자체 뒤로가기로 목록 화면에 돌아왔을 때
    fetch 자체는 다시 일어나면서도 브라우저가 이전 응답을 재사용해 안읽음 개수가 갱신 안
    되는 문제가 있었다 - 이 헤더가 없으면 캐시 여부가 브라우저 판단에 맡겨지는 게 원인이었다.

    반대로 커버/페이지 이미지는 내용이 거의 안 바뀌니(바뀌면 서버가 자체적으로
    source_mtime 기준 캐시를 무효화함) 오히려 적극적으로 캐싱해도 된다는 걸 명시해야
    한다 - 그냥 "금지 안 함" 정도로는 브라우저가 알아서 캐싱해준다는 보장이 없어서,
    실제로 리더 갔다가 메인화면에 돌아올 때마다 섬네일을 매번 새로 받아오는 문제가 있었다.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        if _CACHEABLE_IMAGE_PATH.match(path):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# 앱 생명주기: 시작 시 스캔, 자동 재스캔 예약
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup_scan():
    db.init_schema()
    # 첫 스캔도 백그라운드로 돌린다 - 네트워크 드라이브(원드라이브 등)가 섞여 있으면
    # 전체 스캔에 시간이 걸릴 수 있는데, 그걸 여기서 기다리면 컨테이너 시작 자체가
    # 그만큼 느려진다. create_task로 넘기면 서버는 바로 요청을 받기 시작하고,
    # 플랫폼별로 스캔이 끝나는 대로 목록에 자연스럽게 반영된다.
    asyncio.create_task(services.initial_scan())

    if services.RESCAN_INTERVAL_SECONDS > 0:
        log.info(f"자동 재스캔 활성화 - {services.RESCAN_INTERVAL_SECONDS / 60:.0f}분마다 실행")
        asyncio.create_task(services.auto_rescan_loop())
    else:
        log.info("자동 재스캔 비활성화됨 (RESCAN_INTERVAL_SECONDS <= 0)")


# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------

app.include_router(library.router)
app.include_router(series.router)
app.include_router(chapters.router)
app.include_router(settings.router)
app.include_router(backup.router)


# ---------------------------------------------------------------------------
# 정적 프론트엔드 (API 라우트 전부 등록된 다음 마지막에 마운트)
# ---------------------------------------------------------------------------

STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
