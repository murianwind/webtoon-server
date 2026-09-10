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
  - routers/auth.py      (PROFILES_ENABLED일 때만) 관리자 로그인 + 기억된 기기 관리
  - routers/profiles.py  (PROFILES_ENABLED일 때만) 프로필 관리(관리자 전용)
  - services.py          위 라우터들이 공유하는 백그라운드 스캔/커버 사전생성 등
  - db.py       읽음 진행률 / 설정 / 제외목록 / 겹침캐시 / 백업·복원 (SQLite) - 관리자 전용
  - auth.py, profiles.py, access_requests.py, profile_progress.py, discord_notify.py
                (PROFILES_ENABLED일 때만 실질적으로 쓰이는 공유 프로필 기능)
  - catalog.py  스캔 결과를 담아두는 메모리 상태
  - scan.py     파일시스템 스캔 + 회차 라벨 파싱
  - overlap.py  화 전환 겹침(리캡) 감지 알고리즘 + 백그라운드 사전계산
  - covers.py   시리즈 커버 썸네일 생성/캐싱

PROFILES_ENABLED(공유 프로필) 기능: 환경변수가 없거나 "true"가 아니면 완전히 비활성화되고,
그 상태에서는 이 파일의 나머지 로직이 기존과 100% 동일하게 동작한다(관련 라우터도
등록되지 않고, 관련 스키마도 만들어지지 않음) - 이 기능을 켜지 않은 사용자에게는
아무 영향이 없다.
"""

import asyncio
import logging
import os
import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, services
from .routers import backup, chapters, library, series, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("webtoon-server")

PROFILES_ENABLED = os.environ.get("PROFILES_ENABLED", "").lower() == "true"

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
    cover_mtime/파일 mtime 기준으로 캐시를 무효화함) 오히려 적극적으로, 오래 캐싱해도
    된다는 걸 명시해야 한다 - 그냥 "금지 안 함" 정도로는 브라우저가 알아서 캐싱해준다는
    보장이 없어서, 실제로 리더 갔다가 메인화면에 돌아올 때마다 섬네일을 매번 새로
    받아오는 문제가 있었다. 원본이 바뀌면 서버가 항상 정확히 무효화하므로(캐시 기간과
    무관하게 새 이미지로 바뀜), 기간을 넉넉하게(1주일) 둬도 오래된 이미지가 계속
    보일 위험은 없다 - 오히려 짧으면 "오랜만에 접속" 같은 흔한 상황마다 괜히 다시
    받아오게 될 뿐이다.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        if _CACHEABLE_IMAGE_PATH.match(path):
            response.headers["Cache-Control"] = "public, max-age=604800"
        else:
            response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# 공유 프로필 인증 게이트 (PROFILES_ENABLED일 때만 동작)
# ---------------------------------------------------------------------------

DEVICE_COOKIE_NAME = "webtoon_device"

# 비밀번호 없이 항상 통과시켜야 하는 경로 - 로그인 화면 자체와 로그인 API, 그리고 로그인
# 화면이 그리는 데 필요한 최소한의 정적 자원.
_ADMIN_GATE_ALLOWLIST = ("/login.html", "/api/auth/login", "/favicon.ico", "/manifest.json", "/icons/", "/style.css")


@app.middleware("http")
async def profile_and_admin_gate(request, call_next):
    """
    PROFILES_ENABLED가 꺼져있으면 아무것도 안 하고 그대로 통과시킨다(request.state.profile만
    None으로 채워둠) - 이 기능을 안 쓰는 사람에게는 기존과 동일하게 동작한다.

    켜져있으면:
    - "/p/<토큰>/..." 경로는 그 토큰이 유효한 프로필인지 확인하고, request.state.profile에
      그 프로필을 담아둔 뒤, 경로에서 "/p/<토큰>" 부분을 떼어내고 나머지 요청을 그대로
      진행시킨다 - 그러면 "/p/xyz/api/series"가 실제로는 "/api/series"와 똑같이
      라우팅되어, 기존 라우트 코드를 하나도 복제/중복 등록할 필요가 없다.
    - 그 외 경로(관리자용 루트)는 "기억된 기기" 쿠키가 있어야 통과한다. 없으면 API
      요청은 401, 화면 요청은 로그인 화면으로 리다이렉트한다.
    """
    if not PROFILES_ENABLED:
        request.state.profile = None
        return await call_next(request)

    from . import auth, profiles  # 지연 import: 기능이 꺼져있을 때 불필요한 로드를 피함

    path = request.scope["path"]

    if path.startswith("/p/"):
        parts = path.split("/", 3)  # ["", "p", "<토큰>", "나머지..."]
        token = parts[2] if len(parts) > 2 else ""
        profile = profiles.get_profile_by_token(token)
        if profile is None:
            return JSONResponse({"detail": "profile link not found"}, status_code=404)
        request.state.profile = profile
        request.scope["path"] = "/" + parts[3] if len(parts) > 3 else "/"
        return await call_next(request)

    request.state.profile = None
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if auth.is_device_remembered(device_id):
        auth.touch_device(device_id)
        return await call_next(request)

    if any(path == allowed or path.startswith(allowed) for allowed in _ADMIN_GATE_ALLOWLIST):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return RedirectResponse("/login.html")


# ---------------------------------------------------------------------------
# 앱 생명주기: 시작 시 스캔, 자동 재스캔 예약, (켜져있으면) 프로필 기능 초기화
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

    if PROFILES_ENABLED:
        from . import access_requests, auth, discord_notify, profile_progress, profile_settings, profiles

        auth.init_schema()
        profiles.init_schema()
        access_requests.init_schema()
        profile_progress.init_schema()
        profile_settings.init_schema()

        # 서버가 켜질 때마다 새 비밀번호를 만들어서 로그(+디스코드)에 남긴다. 이미
        # 로그인해서 기억된 기기는 비밀번호가 아니라 기기 쿠키로만 통과되므로(위
        # ensure_admin_password_exists 설명 참고) 전혀 영향이 없다 - 이 값은 "지금부터
        # 새로 로그인하려는 기기"가 써야 하는 값이다.
        new_password = auth.ensure_admin_password_exists()
        log.warning(f"관리자 비밀번호(새로 접속하는 기기용): {new_password}")
        log.warning("이미 로그인해서 기억된 기기는 이 값과 무관하게 계속 그대로 접속됩니다.")
        asyncio.create_task(
            discord_notify.send(f"🔑 webtoon-server 관리자 비밀번호(새로 접속하는 기기용): `{new_password}`")
        )


# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------

app.include_router(library.router)
app.include_router(series.router)
app.include_router(chapters.router)
app.include_router(settings.router)
app.include_router(backup.router)

if PROFILES_ENABLED:
    from .routers import auth as auth_router
    from .routers import browse as browse_router
    from .routers import profiles as profiles_router

    app.include_router(auth_router.router)
    app.include_router(browse_router.router)
    app.include_router(profiles_router.router)


# ---------------------------------------------------------------------------
# 정적 프론트엔드 (API 라우트 전부 등록된 다음 마지막에 마운트)
# ---------------------------------------------------------------------------

STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
