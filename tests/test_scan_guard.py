"""실행 토큰 모듈 단위 테스트: 오래된 실행 저장 차단, 경쟁 상태, 멈춤 감지, 중복 실행 거절"""
import threading

import pytest

from modules import scan_guard as g


@pytest.fixture(autouse=True)
def _clean():
    with g._LOCK:
        g._LIVE.clear()
    yield
    with g._LOCK:
        g._LIVE.clear()


def test_new_token_invalidates_old_and_blocks_its_saves():
    state = {}
    a = g.begin(state)
    g.end(a)                      # 실행 A가 재실행으로 끊김
    assert g.detect_stall(state)
    g.mark_stalled(state)
    b = g.begin(state)            # 이어달리기 = 새 실행 B
    assert b and b != a
    assert g.commit(state, a, {"scan_processed": 999}) is False
    assert g.finish(state, a, {"scan_processed": 999}) is False
    assert g.heartbeat(state, a) is False
    assert "scan_processed" not in state
    assert g.commit(state, b, {"scan_processed": 1}) and state["scan_processed"] == 1


def test_begin_refuses_while_live_and_fresh():
    state = {}
    a = g.begin(state, now=1000)
    assert g.begin(state, now=1010) is None          # 연속 요청 → 거절 (단일 실행)
    assert state["scan_exec_token"] == a


def test_begin_takes_over_when_live_but_heartbeat_stale():
    state = {}
    a = g.begin(state, now=1000)
    b = g.begin(state, now=1000 + g.STALL_SECONDS + 1)
    assert b and b != a and not g.is_live(a)


def test_detect_stall_cases():
    state = {"chunk_executing": False}
    assert not g.detect_stall(state)
    state = {"chunk_executing": True}                 # 토큰 없음(시작 직전 끊김)
    assert g.detect_stall(state)
    state = {}
    a = g.begin(state, now=1000)
    assert not g.detect_stall(state, now=1010)
    assert g.detect_stall(state, now=1000 + g.STALL_SECONDS + 1)   # heartbeat 끊김
    g.end(a)
    assert g.detect_stall(state, now=1010)                         # 실행 자체가 사라짐


def test_mark_stalled_state():
    state = {}
    g.begin(state)
    g.mark_stalled(state)
    assert state["chunk_executing"] is False and state["scan_running"] is False
    assert state["scan_interrupted"] is True and state["scan_stalled"] is True
    assert state["scan_exec_token"] is None


def test_finish_releases_token():
    state = {}
    a = g.begin(state)
    assert g.finish(state, a, {"x": 1})
    assert state["chunk_executing"] is False and state["scan_exec_token"] is None and not g.is_live(a)


def test_race_old_writer_never_lands_after_takeover():
    """토큰 확인과 저장이 원자적이어야: 새 토큰 발급 이후 오래된 실행의 쓰기가 하나도 들어가면 안 됨."""
    for _ in range(50):
        state = {}
        a = g.begin(state)
        stop = threading.Event()
        rejected = threading.Event()

        def old_writer():
            while not stop.is_set():
                if not g.commit(state, a, {"writer": "A"}):
                    rejected.set()
                    return

        t = threading.Thread(target=old_writer)
        t.start()
        g.mark_stalled(state)
        b = g.begin(state)
        assert g.commit(state, b, {"writer": "B"})
        rejected.wait(2)
        stop.set()
        t.join(2)
        assert rejected.is_set()
        assert state["writer"] == "B"
