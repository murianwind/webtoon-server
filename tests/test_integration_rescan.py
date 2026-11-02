"""외부 연동(webtoon-manager)이 다운로드 뒤에 스캔을 시키는 API — 토큰 인증, 요청은 바로 돌아오고 스캔은 백그라운드, 진행 상태 조회, 중복 스캔 방지.

배경: 스캔은 네트워크 드라이브에서 15~30분 걸릴 수 있어서 요청을 오래 붙잡을 수 없다(/api/rescan은 끝날 때까지 응답을 안 준다). 그리고 PROFILES_ENABLED가 켜져
있으면 관리자 API는 로그인 쿠키가 없으면 401이라 외부 연동이 호출할 수 없다 — 그래서 INTEGRATION_TOKEN(환경변수)으로 인증하는 별도 경로를 둔다.
토큰을 정하지 않으면(기본) 이 기능은 없는 것처럼 404다.
"""
import asyncio
import importlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, profiles, services

H = lambda token: {"X-Integration-Token": token}   # HTTP 헤더 값이라 영문/숫자/기호(ASCII) 토큰만 쓸 수 있다


@pytest.fixture(autouse=True)
def fresh_scan_state(monkeypatch):
    """스캔 진행 상태는 모듈 전역이라 앞 테스트의 값이 남지 않게 매번 비운다."""
    monkeypatch.setattr(services, "_scan_task", None)
    monkeypatch.setattr(services, "_scan_finished_at", None)


def test_disabled_without_token(client):
    """GIVEN INTEGRATION_TOKEN을 정하지 않음 THEN 두 경로 모두 404(그런 기능이 없는 것처럼)"""
    assert client.post("/api/integration/rescan", headers=H("anything")).status_code == 404
    assert client.get("/api/integration/scan-status", headers=H("anything")).status_code == 404


def test_token_required(client, monkeypatch):
    """토큰이 없거나 틀리면 403, 맞으면 통과"""
    monkeypatch.setenv("INTEGRATION_TOKEN", "secret")
    async def quick(): return {}, {}
    monkeypatch.setattr(services, "_scan_all_platforms_impl", quick)
    assert client.post("/api/integration/rescan").status_code == 403
    assert client.post("/api/integration/rescan", headers=H("wrong")).status_code == 403
    assert client.get("/api/integration/scan-status", headers=H("wrong")).status_code == 403
    assert client.post("/api/integration/rescan", headers=H("secret")).status_code == 200
    assert client.get("/api/integration/scan-status", headers=H("secret")).status_code == 200


def test_start_returns_immediately_and_reports_progress(library, monkeypatch):
    """스캔을 시작하는 요청은 스캔이 끝나기를 기다리지 않고, 진행 중에는 running=True, 끝나면 False + 끝난 시각. 진행 중에 다시 요청하면 새로 시작하지 않는다"""
    monkeypatch.setenv("INTEGRATION_TOKEN", "secret")
    import app.main as main_module

    async def scenario():
        gate = asyncio.Event(); runs = []
        async def slow():
            runs.append(1)
            await gate.wait()
            return {}, {}
        monkeypatch.setattr(services, "_scan_all_platforms_impl", slow)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app), base_url="http://test") as c:
            before = (await c.get("/api/integration/scan-status", headers=H("secret"))).json()
            assert before["running"] is False and before["last_scan_finished_at"] is None
            r = await asyncio.wait_for(c.post("/api/integration/rescan", headers=H("secret")), 2)         # 스캔이 안 끝나도 바로 돌아온다
            assert r.json() == {"started": True, "running": True}
            await asyncio.sleep(0)
            assert (await c.get("/api/integration/scan-status", headers=H("secret"))).json()["running"] is True
            again = (await c.post("/api/integration/rescan", headers=H("secret"))).json()
            assert again == {"started": False, "running": True} and runs == [1]                       # 이미 스캔 중이면 새로 시작하지 않는다
            gate.set(); await asyncio.sleep(0.05)
            after = (await c.get("/api/integration/scan-status", headers=H("secret"))).json()
            assert after["running"] is False and after["last_scan_finished_at"]
            assert "last_scan_at" in after
            assert (await c.post("/api/integration/rescan", headers=H("secret"))).json()["started"] is True   # 끝난 뒤에는 새로 시작
    asyncio.run(scenario())


def test_concurrent_scans_share_one_run_and_survive_cancel(library, monkeypatch):
    """GIVEN 스캔이 도는 중 WHEN 다른 곳(수동 재스캔/자동 재스캔)이 스캔을 요청 THEN 같은 스캔에 합류한다(두 번 훑지 않음). 요청이 취소돼도(연결 끊김) 스캔은 계속된다"""
    async def scenario():
        gate = asyncio.Event(); runs = []
        async def slow():
            runs.append(1); await gate.wait(); return {"a": 1}, {"b": 2}
        monkeypatch.setattr(services, "_scan_all_platforms_impl", slow)
        first = asyncio.ensure_future(services.scan_all_platforms_incrementally())
        second = asyncio.ensure_future(services.scan_all_platforms_incrementally())
        await asyncio.sleep(0.01)
        assert runs == [1] and services.is_scan_running()
        first.cancel()                                                             # 한 요청이 끊겨도
        await asyncio.sleep(0.01); assert services.is_scan_running()               # 스캔은 계속
        gate.set()
        assert await second == ({"a": 1}, {"b": 2}) and not services.is_scan_running() and services.last_scan_finished_at()
    asyncio.run(scenario())


def test_scan_error_does_not_break_later_scans(library, monkeypatch):
    """스캔이 오류로 끝나도 진행 중 표시가 남지 않고 다음 스캔을 시작할 수 있다"""
    async def scenario():
        calls = []
        async def boom():
            calls.append(1); raise RuntimeError("디스크 오류")
        monkeypatch.setattr(services, "_scan_all_platforms_impl", boom)
        with pytest.raises(RuntimeError):
            await services.scan_all_platforms_incrementally()
        assert not services.is_scan_running()
        assert services.start_background_scan() is True; await asyncio.sleep(0.01)
        assert not services.is_scan_running() and calls == [1, 1]
    asyncio.run(scenario())


@pytest.fixture
def profiles_on(library, monkeypatch):
    import app.main as main_module
    monkeypatch.setenv("PROFILES_ENABLED", "true"); monkeypatch.setenv("INTEGRATION_TOKEN", "secret")
    importlib.reload(main_module)
    auth.init_schema(); auth.ensure_admin_password_exists(); profiles.init_schema()
    created = profiles.create_profile("딸")
    async def quick(): return {}, {}
    monkeypatch.setattr(services, "_scan_all_platforms_impl", quick)
    with TestClient(main_module.app) as c:
        yield {"client": c, "token": created["token"]}
    monkeypatch.delenv("PROFILES_ENABLED", raising=False)
    importlib.reload(main_module)


def test_works_through_profiles_gate_but_only_with_token(profiles_on):
    """GIVEN PROFILES_ENABLED(로그인 게이트) THEN 로그인 쿠키가 없어도 토큰이 맞으면 통과하고, 토큰이 없으면 막히며, 공유 프로필 링크로는 존재하지 않는다. 기존 관리자 재스캔은 그대로 로그인이 필요하다"""
    c = profiles_on["client"]
    assert c.post("/api/rescan").status_code == 401                                                              # 관리자 API는 그대로 로그인 필요
    assert c.post("/api/integration/rescan", headers=H("secret")).status_code == 200                                # 연동 경로는 토큰만으로
    assert c.get("/api/integration/scan-status", headers=H("secret")).status_code == 200
    assert c.post("/api/integration/rescan", headers=H("wrong")).status_code == 403
    assert c.post("/api/integration/rescan").status_code == 403
    assert c.post(f"/p/{profiles_on['token']}/api/integration/rescan", headers=H("secret")).status_code == 404    # 공유 프로필은 이 기능이 아예 없다
