"""
라이브러리 스캔 관련 회귀 테스트: 1단계/중첩 폴더 인식, 제외/재포함, 경로 탐색 방어,
플랫폼 우선순위(SLOW_PLATFORMS)를 다룬다.
"""

import os
import threading
import time

from app import scan
from conftest import make_chapter_zip


def test_scans_a_simple_one_level_series(client, library):
    """GIVEN 플랫폼 폴더 바로 아래에 시리즈 폴더가 있을 때"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))

    """WHEN 스캔하면"""
    client.post("/api/rescan")

    """THEN 그 시리즈가 목록에 나온다"""
    series = client.get("/api/series").json()
    assert [s["title"] for s in series] == ["테스트웹툰"]
    assert series[0]["platform"] == "naver"


def test_scans_series_nested_under_extra_folder_levels(client, library):
    """GIVEN 시리즈 폴더가 작가 폴더 등으로 한 단계 더 묶여 있을 때"""
    make_chapter_zip(str(library / "gdrive" / "01 작가" / "어떤작가" / "숨은시리즈" / "001.zip"))

    """WHEN 스캔하면"""
    client.post("/api/rescan")

    """THEN 중간 폴더 이름은 안 보이고, 실제 시리즈 폴더명만 제목으로 뜬다"""
    series = client.get("/api/series").json()
    assert [s["title"] for s in series] == ["숨은시리즈"]


def test_preserves_series_id_for_shallow_series_across_rescans(client, library):
    """GIVEN 1단계 시리즈를 스캔해서 ID를 하나 얻었으면"""
    make_chapter_zip(str(library / "naver" / "테스트웹툰" / "001 1화.zip"))
    client.post("/api/rescan")
    first_id = client.get("/api/series").json()[0]["id"]

    """WHEN 다시 스캔해도"""
    client.post("/api/rescan")

    """THEN 같은 시리즈는 항상 같은 ID를 유지한다(진행률 등이 안 끊기려면 필수)"""
    second_id = client.get("/api/series").json()[0]["id"]
    assert first_id == second_id


def test_excluded_series_disappears_from_listing_but_stays_in_folder_list(client, library):
    """GIVEN 스캔된 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]

    """WHEN 그 폴더를 제외하면"""
    r = client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    assert r.status_code == 200

    """THEN 목록에서는 빠지지만, 설정 패널의 제외된 폴더 목록에는 남아있다(다시 포함시킬 수 있어야 하므로)"""
    assert client.get("/api/series").json() == []
    folders = client.get("/api/series-folders").json()
    assert {"platform": "naver", "series": "웹툰A"} in folders["excluded"]
    assert folders["included"] == []


def test_included_series_reappears_without_full_rescan(client, library):
    """GIVEN 제외된 시리즈가 있을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    assert client.get("/api/series").json() == []

    """WHEN 재포함시키면(전체 재스캔 없이)"""
    r = client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})
    assert r.status_code == 200

    """THEN 그 시리즈가 다시 목록에 뜬다"""
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]


def _wait_until(condition, timeout=5.0):
    """백그라운드 작업이 끝나길 기다린다(고정 sleep 대신 조건이 맞을 때까지 짧게 반복 확인)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def _excluded_known_series(client, library):
    """스캔을 마친 시리즈 "웹툰A"(1화)를 제외한 상태를 만들고 시리즈 id를 돌려준다."""
    from app import scan

    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    return scan.make_id("naver", "웹툰A")


def _chapter_count(client, series_id):
    return len(client.get(f"/api/series/{series_id}/chapters").json()["chapters"])


def test_reinclude_of_known_series_responds_without_waiting_for_the_disk(client, library, monkeypatch):
    """GIVEN 제외된 시리즈가 있고, 폴더를 다시 읽는 일이 아주 느린 상황(네트워크 드라이브)에서"""
    from app import scan

    series_id = _excluded_known_series(client, library)
    real_scan = scan.scan_single_series
    release = threading.Event()

    def slow_scan(*args, **kwargs):
        release.wait(timeout=10)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(scan, "scan_single_series", slow_scan)

    """WHEN 다시 포함시키면"""
    started = time.monotonic()
    r = client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})
    elapsed = time.monotonic() - started

    """THEN 디스크 읽기를 기다리지 않고 바로 응답하고, 그 시점에 이미 목록에 있고 읽을 수 있다"""
    release.set()
    assert r.status_code == 200
    assert elapsed < 2
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]
    assert _chapter_count(client, series_id) == 1


def test_reinclude_refreshes_the_folder_in_the_background(client, library):
    """GIVEN 제외된 사이에 폴더에 새 회차가 생겼을 때"""
    series_id = _excluded_known_series(client, library)
    make_chapter_zip(str(library / "naver" / "웹툰A" / "002.zip"))

    """WHEN 다시 포함시키면"""
    client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})

    """THEN 곧 백그라운드 갱신으로 새 회차가 반영된다"""
    assert _wait_until(lambda: _chapter_count(client, series_id) == 2)


def test_background_refresh_does_not_unhide_a_series_excluded_again_meanwhile(client, library, monkeypatch):
    """GIVEN 다시 포함한 직후 백그라운드 갱신이 진행되는 중에"""
    from app import db, scan

    _excluded_known_series(client, library)
    real_scan = scan.scan_single_series

    def scan_then_user_excludes_again(*args, **kwargs):
        result = real_scan(*args, **kwargs)
        db.set_excluded_series({("naver", "웹툰A")})  # 갱신 도중 사용자가 다시 제외함
        return result

    monkeypatch.setattr(scan, "scan_single_series", scan_then_user_excludes_again)

    """WHEN 갱신이 끝나면"""
    client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})
    assert _wait_until(lambda: db.get_excluded_series() == {("naver", "웹툰A")})
    time.sleep(0.3)  # 갱신 결과가 카탈로그에 반영될 시간

    """THEN 갱신 결과가 사용자의 최신 선택(제외)을 덮어써서 다시 나타나면 안 된다"""
    assert client.get("/api/series").json() == []


def test_background_refresh_failure_keeps_the_existing_series_readable(client, library, monkeypatch):
    """GIVEN 다시 포함한 뒤 백그라운드 갱신이 오류로 실패하는 상황(마운트 끊김 등)에서"""
    from app import scan

    series_id = _excluded_known_series(client, library)

    def broken_scan(*args, **kwargs):
        raise OSError("mount lost")

    monkeypatch.setattr(scan, "scan_single_series", broken_scan)

    """WHEN 다시 포함시키면"""
    r = client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})
    time.sleep(0.3)

    """THEN 응답은 성공이고, 기존 카탈로그 항목은 그대로 목록에 있고 읽을 수 있다"""
    assert r.status_code == 200
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]
    assert _chapter_count(client, series_id) == 1


def test_repeated_reinclude_runs_only_one_refresh_at_a_time(client, library, monkeypatch):
    """GIVEN 제외/포함을 연타하는 상황에서 폴더 읽기가 느릴 때"""
    from app import scan

    _excluded_known_series(client, library)
    real_scan = scan.scan_single_series
    release = threading.Event()
    calls = []

    def slow_scan(*args, **kwargs):
        calls.append(1)
        release.wait(timeout=10)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(scan, "scan_single_series", slow_scan)
    body = {"platform": "naver", "series": "웹툰A"}

    """WHEN 갱신이 끝나기 전에 제외/포함을 반복하면"""
    client.post("/api/series-folders/include", json=body)
    client.post("/api/series-folders/exclude", json=body)
    client.post("/api/series-folders/include", json=body)
    release.set()
    time.sleep(0.3)

    """THEN 같은 폴더를 겹쳐서 여러 번 읽지 않는다(네트워크 드라이브 낭비 방지)"""
    assert len(calls) == 1


def test_full_scan_uses_the_latest_exclusion_not_a_stale_snapshot(client, library):
    """GIVEN 플랫폼을 훑는 도중(네트워크 드라이브는 수십 분)에"""
    from app import db

    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "웹툰B" / "001.zip"))
    db.set_excluded_series({("naver", "웹툰A")})
    gen = scan.iter_platform_series_streaming("naver")
    first_ref, first_entry, _ = next(gen)
    assert (first_ref, first_entry["excluded"]) == ("웹툰A", True)

    """WHEN 사용자가 B를 제외하고 A를 다시 포함시킨 뒤 스캔이 이어지면"""
    db.set_excluded_series({("naver", "웹툰B")})
    second_ref, second_entry, _ = next(gen)

    """THEN 스캔 시작 때의 낡은 목록이 아니라 최신 제외 목록이 반영된다"""
    assert (second_ref, second_entry["excluded"]) == ("웹툰B", True)


def test_reinclude_of_unknown_series_still_scans_the_folder(client, library):
    """GIVEN 카탈로그에는 없지만 디스크에는 있는 폴더(예: 백업 복원 직후)가 제외 목록에 있을 때"""
    from app import catalog, scan

    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    client.post("/api/rescan")  # 시작 시 백그라운드 초기 스캔과 겹쳐도 결과가 같도록 먼저 확정
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    catalog.remove_series(scan.make_id("naver", "웹툰A"))
    assert client.get("/api/series").json() == []

    """WHEN 다시 포함시키면"""
    client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})

    """THEN 카탈로그에 없으므로 이 경우에만 폴더를 읽어서 목록에 더한다"""
    assert [s["title"] for s in client.get("/api/series").json()] == ["웹툰A"]


def test_path_traversal_in_include_is_blocked(client, library):
    """GIVEN 라이브러리 폴더 밖에 zip이 있는 폴더가 있을 때(공격 대상)"""
    outside_dir = library.parent / "outside_secret" / "무언가"
    make_chapter_zip(str(outside_dir / "001.zip"))
    assert os.path.isdir(str(outside_dir))

    """WHEN "../"가 섞인 series 값으로 재포함을 시도하면"""
    traversal_ref = f"../{os.path.basename(str(outside_dir.parent))}/무언가"
    r = client.post("/api/series-folders/include", json={"platform": "naver", "series": traversal_ref})
    assert r.status_code == 200  # 엔드포인트 자체는 에러 없이 응답하지만(기존 동작 유지)

    """THEN 라이브러리 밖의 폴더는 절대 시리즈로 등록되지 않는다"""
    assert client.get("/api/series").json() == []


def test_local_platforms_are_scanned_before_slow_platforms(client, library, monkeypatch):
    """GIVEN 로컬 플랫폼과 네트워크(SLOW_PLATFORMS로 지정) 플랫폼이 섞여 있을 때"""
    make_chapter_zip(str(library / "zzz_local" / "로컬웹툰" / "001.zip"))
    make_chapter_zip(str(library / "aaa_slow" / "느린웹툰" / "001.zip"))
    monkeypatch.setenv("SLOW_PLATFORMS", "aaa_slow")

    """WHEN 스캔 순서를 뽑아보면"""
    from app import scan
    order = scan.list_platforms_in_scan_order()

    """THEN 이름순으로는 aaa_slow가 앞이어야 하지만, SLOW_PLATFORMS 지정 때문에 뒤로 밀린다"""
    assert order == ["zzz_local", "aaa_slow"]


def test_folder_list_only_grows_during_scan_never_shrinks_midway(client, library):
    """GIVEN 이전 스캔에서 이미 폴더 3개를 다 알고 있는 상태일 때(설정 패널의
    "스캔 중/제외된 폴더" 목록에 이미 반영되어 있음)"""
    from app import catalog

    catalog.add_platform_folder_ref("naver", "웹툰A")
    catalog.add_platform_folder_ref("naver", "웹툰B")
    catalog.add_platform_folder_ref("naver", "웹툰C")
    before = {ref for _, ref in catalog.get_all_folder_refs()}
    assert before == {"웹툰A", "웹툰B", "웹툰C"}

    """WHEN 새 스캔이 시작되어 폴더를 하나씩 다시 발견해나가는 중(아직 완주 전)"""
    catalog.add_platform_folder_ref("naver", "웹툰A")  # 재발견 - 중복 추가되면 안 됨
    during_scan = {ref for _, ref in catalog.get_all_folder_refs()}

    """THEN 스캔이 끝나기 전인데도 이미 알고 있던 웹툰B, 웹툰C가 목록에서 사라지지
    않는다(예전에는 이 시점에 "지금까지 이번 스캔에서 본 것만" 남기는 방식이라
    스캔이 끝날 때까지 웹툰B, 웹툰C가 화면에서 사라졌었다)"""
    assert during_scan == {"웹툰A", "웹툰B", "웹툰C"}


def test_failed_scan_does_not_lose_folders_not_yet_reached(client, library):
    """GIVEN 이전 스캔에서 폴더 3개를 알고 있는 상태에서"""
    from app import catalog

    catalog.add_platform_folder_ref("naver", "웹툰A")
    catalog.add_platform_folder_ref("naver", "웹툰B")
    catalog.add_platform_folder_ref("naver", "웹툰C")

    """WHEN 이번 스캔이 웹툰A만 발견한 상태에서 타임아웃/오류로 중단되면(completed=False라서
    prune을 호출하지 않는 상황을 그대로 재현)"""
    catalog.add_platform_folder_ref("naver", "웹툰A")
    # prune_platform_folder_refs를 호출하지 않음 - 완주 못 했으므로

    """THEN 아직 못 훑은 웹툰B, 웹툰C도 그대로 남아있어야 한다(제외/포함 조작이
    계속 가능해야 하므로)"""
    refs = {ref for _, ref in catalog.get_all_folder_refs()}
    assert refs == {"웹툰A", "웹툰B", "웹툰C"}


def test_completed_scan_prunes_genuinely_deleted_folders(client, library):
    """GIVEN 폴더 3개를 알고 있는 상태에서"""
    from app import catalog

    catalog.add_platform_folder_ref("naver", "웹툰A")
    catalog.add_platform_folder_ref("naver", "웹툰B")
    catalog.add_platform_folder_ref("naver", "웹툰C")

    """WHEN 스캔이 끝까지 완주됐는데 웹툰B는 실제로 폴더가 삭제되어 이번엔 못 봤다면"""
    catalog.prune_platform_folder_refs("naver", {"웹툰A", "웹툰C"})

    """THEN 이번에는(완주했으므로) 진짜로 사라진 웹툰B만 정리된다"""
    refs = {ref for _, ref in catalog.get_all_folder_refs()}
    assert refs == {"웹툰A", "웹툰C"}


def test_reading_progress_survives_exclude_then_reinclude(client, library):
    """GIVEN 웹툰을 하나 읽어서 이어보기 진행률이 저장되어 있을 때"""
    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "웹툰A" / "002.zip"))
    client.post("/api/rescan")
    series_id = client.get("/api/series").json()[0]["id"]
    chapters = client.get(f"/api/series/{series_id}/chapters").json()["chapters"]
    first_chapter_id = chapters[0]["id"]

    client.put(
        f"/api/series/{series_id}/progress",
        json={"chapter_id": first_chapter_id, "page_index": 1},
    )
    before = client.get(f"/api/series/{series_id}/continue").json()
    assert before["chapter_id"] == first_chapter_id

    """WHEN 그 웹툰 폴더를 제외했다가 다시 포함시키면(중간에 전체 재스캔 없이)"""
    client.post("/api/series-folders/exclude", json={"platform": "naver", "series": "웹툰A"})
    assert client.get("/api/series").json() == []  # 제외된 동안엔 목록에서 빠짐

    client.post("/api/series-folders/include", json={"platform": "naver", "series": "웹툰A"})

    """THEN 시리즈 ID가 폴더 경로 기준으로 그대로 유지되어, 이전에 읽던 위치가
    정확히 그대로 남아있다(제외/재포함이 진행률 테이블을 전혀 건드리지 않으므로)"""
    restored_series_id = client.get("/api/series").json()[0]["id"]
    assert restored_series_id == series_id
    after = client.get(f"/api/series/{series_id}/continue").json()
    assert after["chapter_id"] == first_chapter_id
    assert after["page_index"] == 1


def test_deleted_chapter_is_gone_after_rescan_and_returns_not_found(client, library):
    """GIVEN 회차가 2개인 시리즈를 스캔했고, 그 중 2화의 ID를 알고 있을 때(열어둔 리더 탭/북마크)"""
    import os

    from app import scan

    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "웹툰A" / "002.zip"))
    client.post("/api/rescan")
    series_id = scan.make_id("naver", "웹툰A")
    chapters = client.get(f"/api/series/{series_id}/chapters").json()["chapters"]
    deleted_chapter_id = chapters[1]["id"]
    assert client.get(f"/api/chapters/{deleted_chapter_id}/pages").status_code == 200

    """WHEN 2화 zip이 폴더에서 삭제된 뒤 재스캔하면"""
    os.remove(str(library / "naver" / "웹툰A" / "002.zip"))
    client.post("/api/rescan")

    """THEN 회차 목록에서 빠질 뿐 아니라, 옛 ID로 접근해도 서버 오류가 아니라 "없음"(404)으로 응답한다"""
    remaining = client.get(f"/api/series/{series_id}/chapters").json()["chapters"]
    assert [c["id"] for c in remaining] == [chapters[0]["id"]]
    assert client.get(f"/api/chapters/{deleted_chapter_id}/pages").status_code == 404


def test_refreshing_one_series_drops_its_deleted_chapters_but_keeps_other_series(client, library):
    """GIVEN 시리즈 두 개가 스캔되어 있을 때"""
    import os

    from app import catalog, scan

    make_chapter_zip(str(library / "naver" / "웹툰A" / "001.zip"))
    make_chapter_zip(str(library / "naver" / "웹툰A" / "002.zip"))
    make_chapter_zip(str(library / "naver" / "웹툰B" / "001.zip"))
    client.post("/api/rescan")
    before = catalog.get_chapters_map()
    assert len(before) == 3

    """WHEN 웹툰A의 한 회차가 삭제되고 그 폴더만 다시 읽어 반영하면(폴더 단위 갱신)"""
    os.remove(str(library / "naver" / "웹툰A" / "002.zip"))
    series_entry, chapters_map = scan.scan_single_series("naver", "웹툰A")
    catalog.add_series(series_entry, chapters_map)

    """THEN 웹툰A의 삭제된 회차만 조회표에서 빠지고, 웹툰B의 회차는 그대로 남는다"""
    after = catalog.get_chapters_map()
    assert len(after) == 2
    assert set(after) < set(before)
