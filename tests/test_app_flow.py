"""
앱 단위 검증 (Streamlit AppTest, 가짜 시장 250종목 = chunk 3개).
'화면 재실행으로 끊김'은 가격 조회 도중 st.rerun()을 호출해 실제 Streamlit 재실행(RerunException)으로 재현한다.
Streamlit Cloud 실제 화면·브라우저 새로고침·컨테이너 재시작은 여기서 재현할 수 없음 → 관제실에 별도 표시.
"""
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from modules import scan_guard

FULL = "🔍 전체 스캔 (전종목)"


def _open(app_path):
    at = AppTest.from_file(app_path, default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value(FULL).run()
    return at


def _start(at):
    [b for b in at.button if "전체 스캔 시작" in b.label][0].click().run()
    return at


def _resume_button(at):
    found = [b for b in at.button if "이어서 스캔 재개" in b.label]
    return found[0] if found else None


def _texts(elements):
    return [str(e.value) for e in elements]


def _interrupt_once_at(market, call_no):
    fired = {"done": False}

    def hook(n):
        if n == call_no and not fired["done"]:
            fired["done"] = True
            st.rerun()  # 실제 화면 재실행과 같은 RerunException

    market.on_call = hook
    return fired


def _snapshot(at):
    ss = at.session_state
    result = ss["result"].sort_values("종목코드").reset_index(drop=True)
    return {
        "processed": ss["scan_processed"],
        "fail": ss["scan_fail"],
        "tickers": sorted(ss["processed_tickers"]),
        "rows": result[["종목코드", "점수"]],
    }


def _assert_clean_completion(at, total):
    ss = at.session_state
    assert ss["scan_current_status"] == "스캔 완료"
    assert ss["scan_running"] is False and ss["chunk_executing"] is False
    assert ss["scan_interrupted"] is False and not ss["scan_stalled"]
    assert ss["scan_processed"] + ss["scan_fail"] == total
    assert len(ss["processed_tickers"]) == len(set(ss["processed_tickers"])) == total
    texts = _texts(at.warning) + _texts(at.error)
    assert not any(("중단" in t) or ("멈" in t) or ("불일치" in t) for t in texts), texts
    assert _resume_button(at) is None
    start = [b for b in at.button if "전체 스캔 시작" in b.label][0]
    assert start.disabled is False
    assert not at.exception


def test_batch_run_completes_cleanly(fake_market, app_path):
    at = _start(_open(app_path))
    _assert_clean_completion(at, 250)
    assert fake_market.calls == 250  # 끊김이 없으면 모든 종목을 정확히 한 번 조회


def test_rerun_mid_chunk_becomes_stalled_then_resume_completes(fake_market, app_path):
    _interrupt_once_at(fake_market, 137)          # chunk 2 진행 중 재실행
    at = _start(_open(app_path))
    ss = at.session_state
    assert ss["scan_stalled"] is True and ss["chunk_executing"] is False and ss["scan_running"] is False
    assert ss["scan_exec_token"] is None
    saved = len(ss["processed_tickers"])
    assert saved == 135                           # 5종목마다 저장 → 끊기기 전 마지막 저장 지점
    assert ss["scan_processed"] + ss["scan_fail"] == saved
    assert any("멈춘 것으로" in t for t in _texts(at.warning))
    assert not any("진행 중입니다" in t for t in _texts(at.info))
    btn = _resume_button(at)
    assert btn is not None
    btn.click().run()
    _assert_clean_completion(at, 250)


def test_resume_equals_batch_with_fixed_data(fake_market, app_path, tmp_path):
    batch = _snapshot(_start(_open(app_path)))

    fake_market.calls = 0
    _interrupt_once_at(fake_market, 42)
    at = _start(_open(app_path))
    _resume_button(at).click().run()
    resumed = _snapshot(at)

    assert (batch["processed"], batch["fail"]) == (resumed["processed"], resumed["fail"])
    assert batch["tickers"] == resumed["tickers"]
    pd.testing.assert_frame_equal(batch["rows"], resumed["rows"])


def test_interrupt_before_first_save_still_offers_resume(fake_market, app_path):
    _interrupt_once_at(fake_market, 2)            # 첫 저장(5종목) 전
    at = _start(_open(app_path))
    assert at.session_state["scan_stalled"] is True
    assert len(at.session_state["processed_tickers"]) == 0
    _resume_button(at).click().run()
    _assert_clean_completion(at, 250)


def test_repeated_resume_requests_single_execution(fake_market, app_path):
    """이어달리기 실행 중에 또 이어달리기 요청 + 재실행이 들어와도 집계는 한 번씩만."""
    _interrupt_once_at(fake_market, 60)
    at = _start(_open(app_path))
    assert at.session_state["scan_stalled"] is True

    fired = {"n": 0}

    def double_click(n):
        # 재개 실행 도중 두 번째 클릭처럼: trigger_resume 세우고 재실행
        if fired["n"] < 2 and n in (90, 160):
            fired["n"] += 1
            st.session_state["trigger_resume"] = True
            st.rerun()

    fake_market.on_call = double_click
    _resume_button(at).click().run()
    for _ in range(4):                            # 남은 멈춤이 있으면 다시 이어달리기
        if at.session_state["scan_current_status"] == "스캔 완료":
            break
        btn = _resume_button(at)
        assert btn is not None
        btn.click().run()
    assert fired["n"] == 2
    _assert_clean_completion(at, 250)


def test_stale_run_cannot_save(fake_market, app_path):
    """실행 도중 토큰이 바뀌면(새 실행이 넘겨받음) 기존 실행은 그 뒤로 아무것도 저장하지 못한다."""
    captured = {}

    def hijack(n):
        if n == 33 and not captured:
            state = st.session_state
            captured["before"] = (state["scan_processed"], state["scan_fail"], list(state["processed_tickers"]))
            # 다른 실행이 토큰을 넘겨받은 상황 재현
            with scan_guard._LOCK:
                state["scan_exec_token"] = "someone-else"

    fake_market.on_call = hijack
    at = _start(_open(app_path))
    ss = at.session_state
    assert captured
    assert (ss["scan_processed"], ss["scan_fail"], list(ss["processed_tickers"])) == captured["before"]
    assert fake_market.calls == 33                 # 오래된 실행은 다음 진행 확인에서 즉시 멈춤


def test_heartbeat_timeout_marks_stalled(fake_market, app_path):
    at = _open(app_path)
    token = "ghost"
    with scan_guard._LOCK:
        scan_guard._LIVE[token] = 0
    at.session_state["scan_mode"] = "full"
    at.session_state["scan_all_tickers"] = fake_market.tickers
    at.session_state["scan_total"] = 250
    at.session_state["processed_tickers"] = fake_market.tickers[:100]
    at.session_state["scan_processed"] = 90
    at.session_state["scan_fail"] = 10
    at.session_state["partial_results"] = []
    at.session_state["scan_running"] = True
    at.session_state["chunk_executing"] = True
    at.session_state["scan_exec_token"] = token
    at.session_state["scan_heartbeat"] = 1.0       # 아주 오래전
    at.run()
    assert at.session_state["scan_stalled"] is True
    assert not scan_guard.is_live(token)
    _resume_button(at).click().run()
    _assert_clean_completion(at, 250)


def test_fresh_session_after_server_restart_is_clean(fake_market, app_path):
    """서버 재시작 = session_state와 scan_guard 상태가 모두 사라짐. 새 세션은 깨끗하게 시작해야 한다."""
    _interrupt_once_at(fake_market, 50)
    old = _start(_open(app_path))
    assert old.session_state["scan_stalled"] is True
    with scan_guard._LOCK:                         # 프로세스 재시작 흉내
        scan_guard._LIVE.clear()
    fake_market.on_call = None
    fresh = _open(app_path)
    assert _resume_button(fresh) is None
    assert not any(("중단" in t) or ("멈" in t) for t in _texts(fresh.warning) + _texts(fresh.error))
    assert "processed_tickers" not in fresh.session_state or not fresh.session_state["processed_tickers"]
    _assert_clean_completion(_start(fresh), 250)
