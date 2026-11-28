"""
scan_guard.py — 전체 스캔 실행 토큰 관리 (앱 v11.7)

왜 별도 모듈인가:
  Streamlit은 재실행마다 streamlit_app.py의 전역 변수를 새로 만든다.
  잠금(lock)과 '지금 살아 있는 실행' 목록은 재실행을 넘어 유지돼야 하므로
  sys.modules에 한 번만 올라가는 이 모듈에 둔다.

규칙:
  - chunk 하나를 실행할 때마다 begin()이 새 토큰을 발급하고 세션에 기록한다.
    새 토큰이 발급되면 이전 토큰은 자동으로 무효가 된다.
  - 세션 상태를 바꾸는 저장은 commit()/finish()로만 하고,
    둘 다 같은 잠금 안에서 '토큰 확인 → 저장'을 한 번에 처리한다(경쟁 상태 방지).
  - 실행이 끝나거나 끊기면(Streamlit RerunException 등 BaseException 포함)
    호출 측의 finally에서 end()로 살아 있는 실행 목록에서 뺀다.
  - 다음 실행에서 detect_stall()이 '실행 중 표시인데 그 실행이 없음'을 찾으면
    mark_stalled()로 멈춤 의심 상태로 바꾸고 이어달리기를 허용한다.

서버(프로세스) 재시작 시:
  session_state와 이 모듈의 상태가 모두 사라진다. 복구할 실행 기록이 없으므로
  앱은 빈 상태로 시작한다(중간 결과 복구 불가). 디스크 저장 복구는 별도 결정 사항.
"""

import threading
import time
import uuid

STALL_SECONDS = 90

_LOCK = threading.RLock()
_LIVE = {}  # token -> 시작 시각


class StaleRunError(Exception):
    """무효화된(오래된) 실행이 저장을 시도할 때 실행을 끝내기 위한 신호."""


def _now():
    return time.time()


def is_live(token):
    with _LOCK:
        return bool(token) and token in _LIVE


def begin(state, now=None):
    """
    새 실행 토큰 발급. 이미 살아 있고 heartbeat가 신선한 실행이 있으면 None(거절).
    성공 시 세션에 토큰·heartbeat·chunk_executing을 기록하고 토큰을 반환한다.
    """
    now = _now() if now is None else now
    with _LOCK:
        current = state.get("scan_exec_token")
        if current and current in _LIVE:
            last = float(state.get("scan_heartbeat") or 0)
            if now - last <= STALL_SECONDS:
                return None
            _LIVE.pop(current, None)
        token = uuid.uuid4().hex
        _LIVE[token] = now
        state["scan_exec_token"] = token
        state["scan_heartbeat"] = now
        state["chunk_executing"] = True
        state["scan_stalled"] = False
        return token


def end(token):
    """실행 종료(정상·예외·재실행 중단 모두). 살아 있는 실행 목록에서만 뺀다."""
    with _LOCK:
        _LIVE.pop(token, None)


def heartbeat(state, token, now=None):
    """토큰이 유효하면 heartbeat 갱신 후 True, 무효면 False."""
    with _LOCK:
        if not token or state.get("scan_exec_token") != token:
            return False
        state["scan_heartbeat"] = _now() if now is None else now
        return True


def commit(state, token, updates):
    """토큰 확인과 저장을 한 잠금 안에서 처리. 무효 토큰이면 아무것도 쓰지 않고 False."""
    with _LOCK:
        if not token or state.get("scan_exec_token") != token:
            return False
        for key, value in updates.items():
            state[key] = value
        state["scan_heartbeat"] = _now()
        return True


def finish(state, token, updates):
    """마지막 저장 + 토큰 해제(chunk_executing=False). 무효 토큰이면 False."""
    with _LOCK:
        if not token or state.get("scan_exec_token") != token:
            return False
        for key, value in updates.items():
            state[key] = value
        state["scan_exec_token"] = None
        state["chunk_executing"] = False
        _LIVE.pop(token, None)
        return True


def invalidate(state):
    """새 스캔 시작·중간 결과 비우기 등에서 기존 토큰을 무효화."""
    with _LOCK:
        token = state.get("scan_exec_token")
        state["scan_exec_token"] = None
        if token:
            _LIVE.pop(token, None)


def detect_stall(state, now=None):
    """
    멈춤 의심 판정.
      - chunk_executing=True인데 그 토큰의 실행이 살아 있지 않음 → 멈춤(재실행으로 끊김 등)
      - 살아 있지만 heartbeat가 STALL_SECONDS 넘게 없음 → 멈춤 의심
    """
    now = _now() if now is None else now
    with _LOCK:
        if not state.get("chunk_executing"):
            return False
        token = state.get("scan_exec_token")
        if not token or token not in _LIVE:
            return True
        return now - float(state.get("scan_heartbeat") or 0) > STALL_SECONDS


def mark_stalled(state):
    """멈춤 의심 상태로 전환: 토큰 무효화, 실행 플래그 해제, 이어달리기 허용."""
    with _LOCK:
        token = state.get("scan_exec_token")
        if token:
            _LIVE.pop(token, None)
        state["scan_exec_token"] = None
        state["chunk_executing"] = False
        state["scan_running"] = False
        state["scan_interrupted"] = True
        state["scan_stalled"] = True
        state["scan_current_status"] = "멈춤 의심 — 이어달리기 가능"
        state["scan_current_ticker"] = "-"
        state["scan_current_ticker_code"] = "-"


def heartbeat_age(state, now=None):
    now = _now() if now is None else now
    last = state.get("scan_heartbeat")
    return None if not last else max(0.0, now - float(last))
