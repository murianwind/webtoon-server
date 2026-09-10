"""
디스코드 웹훅으로 알림 전송(선택 기능). DISCORD_WEBHOOK_URL 환경변수가 없으면 조용히
아무것도 안 한다 - 이 알림이 실패하거나 설정 안 되어 있어도 그 때문에 실제 기능(비밀번호
생성, 요청 처리 등)이 막히면 안 되므로, 이 모듈의 함수는 절대 예외를 던지지 않는다.

새 의존성(httpx 등)을 추가하지 않기 위해 표준 라이브러리 urllib로 직접 POST한다 - 이
정도의 단순한 웹훅 호출에 별도 HTTP 라이브러리를 운영 이미지에 추가할 필요는 없다.
"""

import asyncio
import json
import logging
import os
import urllib.request

log = logging.getLogger("webtoon-server")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def _post(message: str) -> None:
    data = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10.0):
        pass


async def send(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        await asyncio.to_thread(_post, message)
    except Exception:
        # 알림 실패는 로그만 남기고 절대 위로 전파하지 않는다 - 디스코드가 막혀있다고
        # 비밀번호 생성이나 요청 처리 같은 실제 기능이 실패하면 안 되기 때문이다.
        log.exception("디스코드 알림 전송 실패 (기능 자체에는 영향 없음)")
