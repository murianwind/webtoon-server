"""
catalog.get_series_map()/get_chapters_map()이 원본 딕셔너리를 그대로 돌려주면,
그걸 순회하는 동안 다른 스레드(백그라운드 스캔)가 같은 딕셔너리에 키를 추가/삭제해서
"RuntimeError: dictionary changed size during iteration"이 나는 실제 경쟁 상태가
있었다. 얕은 복사본을 돌려주도록 고친 뒤, 진짜로 동시 수정 상황에서 안전한지 재현
검증한다.
"""

import threading
import time

from app import catalog


def _make_series_entry(i: int) -> dict:
    return {"id": f"series-{i}", "platform": "naver", "title": f"웹툰{i}", "chapters": []}


def test_get_series_map_returns_a_copy_not_the_original(library):
    """GIVEN 카탈로그에 시리즈가 하나 있을 때"""
    catalog.add_series(_make_series_entry(0), {})

    """WHEN 두 번 조회해서 각각의 딕셔너리에 키를 추가/삭제해봐도"""
    snapshot1 = catalog.get_series_map()
    snapshot1["intruder"] = "should not leak into catalog"
    snapshot2 = catalog.get_series_map()

    """THEN 그 변경이 실제 카탈로그나 다른 스냅샷에 전혀 영향을 주지 않는다(원본이
    아니라 매번 새로 복사된 것이라는 뜻)"""
    assert "intruder" not in snapshot2
    assert "intruder" not in catalog.get_series_map()


def test_iterating_series_map_survives_concurrent_mutation(library):
    """GIVEN 순회에 어느 정도 시간이 걸리는 카탈로그가 있고, 다른 스레드가 그 사이에도
    계속 시리즈를 추가/삭제하는(백그라운드 스캔을 흉내낸) 상황일 때"""
    for i in range(300):
        catalog.add_series(_make_series_entry(i), {})

    stop = threading.Event()

    def mutate_forever():
        i = 300
        while not stop.is_set():
            catalog.add_series(_make_series_entry(i), {})
            catalog.remove_series(f"series-{i}")
            i += 1

    mutator = threading.Thread(target=mutate_forever, daemon=True)
    mutator.start()
    try:
        """WHEN 그 와중에 목록을 여러 번 순회하면(list_series()가 하는 것과 동일)"""
        for _ in range(15):
            total = 0
            for series in catalog.get_series_map().values():
                total += 1
                time.sleep(0)  # GIL을 매번 양보해서, mutator 스레드가 순회 도중에
                # 끼어들 기회를 최대한 늘린다(그래야 이 재현 테스트가 실제 경쟁
                # 상태를 안정적으로 드러낼 수 있다 - 그냥 반복문만 돌리면 GIL 전환이
                # 우연에 맡겨져서 재현이 잘 안 됐다).
            assert total >= 300  # 예전 같으면 이 순회 도중 RuntimeError가 났을 상황
    finally:
        stop.set()
        mutator.join(timeout=2)

    """THEN(예외 없이 여기까지 도달하면) 순회가 동시 수정에도 안전하게 끝난 것이다"""
