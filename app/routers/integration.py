"""외부 연동(예: webtoon-manager)이 다운로드 뒤에 라이브러리 스캔을 시키는 API.

왜 /api/rescan을 쓰지 않나: ① /api/rescan은 스캔이 끝날 때까지 응답을 안 준다 — 네트워크 드라이브는 15~30분 걸릴 수 있어서 연동이 요청을 그렇게 오래 붙잡을 수 없다.
② PROFILES_ENABLED(로그인)가 켜져 있으면 관리자 API는 쿠키 없는 외부 호출이 401이다. 그래서 환경변수 INTEGRATION_TOKEN으로 인증하는 별도 경로를 둔다:
  POST /api/integration/rescan     — 스캔을 시작하고 바로 돌아온다({"started": 새로 시작했는지, "running": true}). 이미 스캔 중이면 새로 시작하지 않는다.
  GET  /api/integration/scan-status — {"running": 진행 중인지, "last_scan_at": 화면에 표시하는 마지막 스캔 시각, "last_scan_finished_at": 마지막 스캔 종료 시각(ISO)}
요청 헤더 X-Integration-Token에 INTEGRATION_TOKEN과 같은 값(영문/숫자/기호)을 넣는다. 토큰을 정하지 않으면(기본) 이 기능은 없는 것처럼 404다.
"""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import access_control, catalog, services

# 공유 프로필에게는 이런 기능 자체가 존재하지 않아야 하므로 관리자 전용 라우터와 같은 방식으로 막는다(프로필 링크로는 404)
router = APIRouter(dependencies=[Depends(access_control.require_admin)])


def require_integration_token(x_integration_token: str | None = Header(None)) -> None:
    expected = os.environ.get("INTEGRATION_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")  # 토큰을 정하지 않았으면 이 기능은 꺼져 있다
    if not x_integration_token or not secrets.compare_digest(x_integration_token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="invalid integration token")


@router.post("/api/integration/rescan", dependencies=[Depends(require_integration_token)])
async def integration_rescan():
    # 스캔이 끝난 뒤의 후처리(커버 미리 만들기 등)는 수동/자동 재스캔과 같게 하려고 같은 헬퍼를 쓴다
    started = services.start_background_scan()
    return {"started": started, "running": True}


@router.get("/api/integration/scan-status", dependencies=[Depends(require_integration_token)])
def integration_scan_status():
    return {
        "running": services.is_scan_running(),
        "last_scan_at": catalog.get_last_scan_display(),
        "last_scan_finished_at": services.last_scan_finished_at(),
    }
