"""
streamlit_app.py — v11.1
박스권 스캐너 컨트롤룸

변경 이력:
  v11.1 - chunk 단위 전체 스캔 + 자동 이어달리기
          current_chunk_index / chunk_executing 기반 loop guard 추가
          Streamlit Cloud 장시간 실행 안정화
  v10.2 - resume / clear 버튼 trigger 기반 실행으로 안정화
          partial_results records 저장 구조 일관화
          Streamlit rerun 이후 결과 유지 보강
  v10.0 - Resume Scan 실제 구현
          processed_tickers / scan_all_tickers 기반 이어달리기
          남은 ticker만 재개 + 진행률 offset 반영
  v9.1 - TOP 5 카드 UX 리터칭 (2열 그리드, 점수 강도 이모지)
         전수 스캔 중간 저장 + rerun 복구 구조 추가
  v9.0 - 입력 레이어 리팩토링 (멈추지 않는 스캐너)
         시장 선택 UI (KOSPI / KOSDAQ / ALL)
         멀티소스 fallback 구조 (FDR → NAVER → yfinance → 캐시)
         소스 투명도 UI (컨트롤룸 배지 한 줄)
         상태별 컬러 코드 (정상/경고/위기)
         캐시 사용 시 날짜 배지
  v8.3 - threshold 슬라이더, 튜닝 권고 배너
"""

import pandas as pd
import streamlit as st

from box_range_scanner import (
    FALLBACK_KOSDAQ,
    FALLBACK_KOSPI,
    _get_date,
    get_market_tickers,
    get_price_source_for_scan,
    run_scan,
)

# ── 상수 ──────────────────────────────────────────────────────
FAST_SCORE_THRESHOLD = 0
DEFAULT_FULL_THRESHOLD = 70
RATIO_HIGH = 20.0
RATIO_LOW = 10.0
CHUNK_SIZE = 100

if "trigger_resume" not in st.session_state:
    st.session_state["trigger_resume"] = False

if "trigger_clear" not in st.session_state:
    st.session_state["trigger_clear"] = False

if "current_chunk_index" not in st.session_state:
    st.session_state["current_chunk_index"] = 0

if "chunk_executing" not in st.session_state:
    st.session_state["chunk_executing"] = False

# pykrx (종목명 조회 / 빠른 스캔 거래량 상위)
try:
    from pykrx import stock as krx_stock

    _KRX_AVAILABLE = True
except ImportError:
    _KRX_AVAILABLE = False


# ── 유틸 ──────────────────────────────────────────────────────
def calc_suggested_threshold(current, ratio):
    if ratio > RATIO_HIGH:
        return current + 5, f"후보 비율 {ratio}% > {RATIO_HIGH}% — threshold {current} → {current+5} 권고"
    if ratio < RATIO_LOW:
        return current - 5, f"후보 비율 {ratio}% < {RATIO_LOW}% — threshold {current} → {current-5} 권고"
    return current, None


def get_price_chart(ticker_code):
    end = _get_date(1)
    start = _get_date(90)
    if _KRX_AVAILABLE:
        try:
            df = krx_stock.get_market_ohlcv_by_date(start, end, ticker_code)
            if df is not None and not df.empty:
                return df[["종가"]]
        except Exception:
            pass
    return None


def _source_status(ticker_src, price_src, is_fallback, cache_date):
    if is_fallback or ticker_src == "FALLBACK":
        return "위기"
    if "CACHE" in (ticker_src, price_src) or cache_date:
        return "경고"
    return "정상"


def _render_badge_row(market, ticker_src, price_src, mode_label, cache_date, status):
    cols = st.columns(5)
    items = [
        ("시장", market),
        ("ticker 소스", ticker_src),
        ("가격 소스", price_src),
        ("모드", mode_label),
        ("캐시", f"📅 {cache_date}" if cache_date else "없음"),
    ]
    for col, (label, val) in zip(cols, items):
        col.markdown(f"**{label}**  \n`{val}`")

    if status == "정상":
        st.success("✅ 정상 수집 — FDR 기반 실시간 데이터")
    elif status == "경고":
        st.warning(f"⚠️ 캐시 데이터 사용 중 — [{cache_date}] 기준 최근 성공 데이터로 스캔되었습니다")
    else:
        st.error("🚨 제한 모드 — 현재 데이터가 제한적입니다. fallback 종목 기준으로 스캔 중")


def _merge_result_rows(existing_rows, new_df):
    """partial 결과와 재개 스캔 결과를 종목코드 기준으로 병합"""
    cols = ["종목코드", "종목명", "점수", "거래량", "이유", "돌파신호"]
    frames = []

    if isinstance(existing_rows, pd.DataFrame):
        if not existing_rows.empty:
            frames.append(existing_rows.copy())
    elif existing_rows:
        frames.append(pd.DataFrame(existing_rows))

    if new_df is not None and not new_df.empty:
        frames.append(new_df.copy())

    if not frames:
        return pd.DataFrame(columns=cols)

    merged = pd.concat(frames, ignore_index=True)
    if "종목코드" in merged.columns:
        merged = merged.drop_duplicates(subset=["종목코드"], keep="last")

    for col in cols:
        if col not in merged.columns:
            merged[col] = "" if col in ["종목코드", "종목명", "이유", "돌파신호"] else 0

    return merged[cols]


def _merge_processed_tickers(existing_tickers, new_tickers):
    merged = []
    seen = set()
    for ticker in (existing_tickers or []) + (new_tickers or []):
        if ticker and ticker not in seen:
            seen.add(ticker)
            merged.append(ticker)
    return merged


def _build_chunks(tickers):
    return [tickers[i : i + CHUNK_SIZE] for i in range(0, len(tickers), CHUNK_SIZE)]


def _get_saved_processed_tickers():
    processed_tickers = st.session_state.get("processed_tickers") or []
    if processed_tickers:
        return _merge_processed_tickers([], processed_tickers)

    all_tickers = st.session_state.get("scan_all_tickers") or st.session_state.get("scan_tickers") or []
    progress = int(st.session_state.get("scan_progress", 0) or 0)
    if all_tickers and progress > 0:
        return all_tickers[:progress]
    return []


def _clear_partial_state():
    for key, val in {
        "partial_results": [],
        "processed_tickers": [],
        "scan_progress": 0,
        "scan_total": 0,
        "scan_processed": 0,
        "scan_fail": 0,
        "scan_running": False,
        "scan_interrupted": False,
        "scan_all_tickers": [],
        "scan_tickers": [],
        "scan_ticker_cache_date": None,
        "scan_ticker_src": "-",
        "scan_price_src": "-",
        "scan_cache_date": None,
        "scan_fallback": False,
        "scan_ratio": 0,
        "scan_threshold_used": 0,
        "tune_msg": None,
        "suggested_threshold": DEFAULT_FULL_THRESHOLD,
        "trigger_resume": False,
        "trigger_clear": False,
        "current_chunk_index": 0,
        "chunk_executing": False,
    }.items():
        st.session_state[key] = val
    st.session_state.pop("result", None)


def _run_full_scan(scan_market, full_threshold, resume=False):
    """전체 스캔 실행/재개 공통 루틴"""
    if not resume:
        with st.spinner(f"{scan_market} 종목 목록 수집 중..."):
            ticker_result = get_market_tickers(scan_market)

        tickers = ticker_result["tickers"]
        ticker_src = ticker_result["source"]
        ticker_logs = ticker_result["log"]
        ticker_cache_date = ticker_result.get("cache_date")
        is_fallback = ticker_src == "FALLBACK"

        for log in ticker_logs:
            if log.startswith("[INFO]"):
                st.success(log)
            elif log.startswith("[WARN]"):
                st.warning(log)
            elif log.startswith("[ERROR]"):
                st.error(log)

        if not tickers:
            st.session_state["scan_running"] = False
            st.session_state["chunk_executing"] = False
            st.error("종목 수집 전체 실패 — 잠시 후 재시도 해주세요.")
            return

        total = len(tickers)
        st.info(f"{scan_market} {total}개 종목 스캔 시작 (threshold: {full_threshold}점 이상)")

        st.session_state.update(
            {
                "scan_running": True,
                "scan_market": scan_market,
                "scan_mode": "full",
                "scan_total": total,
                "scan_processed": 0,
                "scan_fail": 0,
                "scan_ratio": 0,
                "scan_threshold_used": full_threshold,
                "scan_ticker_src": ticker_src,
                "scan_ticker_cache_date": ticker_cache_date,
                "scan_all_tickers": tickers,
                "scan_tickers": tickers,
                "scan_price_src": "-",
                "scan_cache_date": ticker_cache_date,
                "scan_fallback": is_fallback,
                "partial_results": [],
                "processed_tickers": [],
                "scan_progress": 0,
                "scan_interrupted": False,
                "current_chunk_index": 0,
                "chunk_executing": True,
            }
        )
        st.session_state.pop("result", None)

    tickers = st.session_state.get("scan_all_tickers") or st.session_state.get("scan_tickers") or []
    ticker_src = st.session_state.get("scan_ticker_src", "-")
    ticker_cache_date = st.session_state.get("scan_ticker_cache_date")
    is_fallback = st.session_state.get("scan_fallback", False)
    partial_rows = st.session_state.get("partial_results") or []
    processed_tickers_base = _get_saved_processed_tickers()

    if not tickers:
        st.session_state["scan_running"] = False
        st.session_state["chunk_executing"] = False
        st.error("이어달리기 실패 — 저장된 ticker 목록이 없습니다. 새 스캔을 시작해주세요.")
        return

    total = len(tickers)
    chunks = _build_chunks(tickers)
    processed_lookup = set(processed_tickers_base)
    remaining_tickers = [ticker for ticker in tickers if ticker not in processed_lookup]
    current_chunk_index = int(st.session_state.get("current_chunk_index", 0) or 0)

    while current_chunk_index < len(chunks):
        current_chunk = [ticker for ticker in chunks[current_chunk_index] if ticker not in processed_lookup]
        if current_chunk:
            break
        current_chunk_index += 1
        st.session_state["current_chunk_index"] = current_chunk_index

    if not remaining_tickers or current_chunk_index >= len(chunks):
        final_df = _merge_result_rows(partial_rows, None)
        processed_total = int(st.session_state.get("scan_processed", 0) or 0)
        fail_total = int(st.session_state.get("scan_fail", 0) or 0)
        ratio = round(len(final_df) / processed_total * 100, 1) if processed_total > 0 else 0
        suggested, tune_msg = calc_suggested_threshold(full_threshold, ratio)

        st.session_state.update(
            {
                "result": final_df,
                "scan_total": total,
                "scan_processed": processed_total,
                "scan_fail": fail_total,
                "scan_mode": "full",
                "scan_fallback": is_fallback,
                "scan_market": scan_market,
                "scan_ticker_src": ticker_src,
                "scan_price_src": st.session_state.get("scan_price_src", "-"),
                "scan_cache_date": st.session_state.get("scan_cache_date"),
                "scan_ratio": ratio,
                "scan_threshold_used": full_threshold,
                "suggested_threshold": suggested,
                "tune_msg": tune_msg,
                "scan_running": False,
                "scan_interrupted": False,
                "chunk_executing": False,
                "partial_results": final_df.to_dict("records"),
                "processed_tickers": processed_tickers_base,
                "scan_progress": len(processed_tickers_base),
                "scan_all_tickers": tickers,
                "scan_tickers": tickers,
                "current_chunk_index": len(chunks),
            }
        )
        return

    if resume:
        st.info(
            f"이어달리기 진행 중 — chunk {current_chunk_index + 1} / {len(chunks)} "
            f"| 현재 진행 {len(processed_tickers_base)} / {total}"
        )
    else:
        st.info(f"chunk {current_chunk_index + 1} / {len(chunks)} 실행 중 — {len(current_chunk)}개 종목")

    start = _get_date(90)
    end = _get_date(1)
    try:
        with st.spinner("가격 데이터 소스 확인 중..."):
            price_info = get_price_source_for_scan(current_chunk, start, end)
    except Exception as e:
        st.session_state["scan_running"] = False
        st.session_state["scan_interrupted"] = True
        st.session_state["chunk_executing"] = False
        st.error(f"가격 데이터 소스 확인 중 오류: {e}")
        return

    for log in price_info["log"]:
        if log.startswith("[INFO]"):
            st.success(log)
        elif log.startswith("[WARN]"):
            st.warning(log)
        elif log.startswith("[ERROR]"):
            st.error(log)

    price_src = price_info["source"]
    price_cache_date = price_info.get("cache_date")
    cache_date = ticker_cache_date or price_cache_date

    st.session_state.update(
        {
            "scan_running": True,
            "scan_interrupted": False,
            "chunk_executing": True,
            "scan_price_src": price_src,
            "scan_cache_date": cache_date,
            "scan_market": scan_market,
            "scan_mode": "full",
            "scan_threshold_used": full_threshold,
            "scan_total": total,
            "scan_all_tickers": tickers,
            "scan_tickers": tickers,
            "processed_tickers": processed_tickers_base,
            "current_chunk_index": current_chunk_index,
        }
    )

    processed_base = int(st.session_state.get("scan_processed", 0) or 0)
    fail_base = int(st.session_state.get("scan_fail", 0) or 0)
    progress_offset = len(processed_tickers_base)

    progress_bar = st.progress(progress_offset / total if total else 0)
    status_text = st.empty()

    def update_progress(current, inner_total, name):
        absolute_current = progress_offset + current
        progress_bar.progress(absolute_current / total)
        status_text.text(f"({absolute_current} / {total}) {name}")

    def save_partial_state(partial_rows, processed_count, fail_count, current_index, total, processed_tickers):
        current_rows = st.session_state.get("partial_results") or []
        merged_df = _merge_result_rows(current_rows, pd.DataFrame(partial_rows))
        merged_processed_tickers = _merge_processed_tickers(processed_tickers_base, processed_tickers)
        st.session_state["partial_results"] = merged_df.to_dict("records")
        st.session_state["processed_tickers"] = merged_processed_tickers
        st.session_state["scan_processed"] = processed_base + processed_count
        st.session_state["scan_fail"] = fail_base + fail_count
        st.session_state["scan_progress"] = len(merged_processed_tickers)
        st.session_state["scan_total"] = len(tickers)

    try:
        df_chunk, processed_count_chunk, fail_count_chunk, processed_tickers_chunk = run_scan(
            tickers=current_chunk,
            progress_callback=update_progress,
            score_threshold=full_threshold,
            price_source_info=price_info,
            partial_callback=save_partial_state,
            save_every=5,
        )
    except Exception as e:
        st.session_state["scan_running"] = False
        st.session_state["scan_interrupted"] = True
        st.session_state["chunk_executing"] = False
        st.error(f"스캔 중 오류: {e}")
        return

    merged_processed_tickers = _merge_processed_tickers(processed_tickers_base, processed_tickers_chunk)
    processed_total = processed_base + processed_count_chunk
    fail_total = fail_base + fail_count_chunk
    current_rows = st.session_state.get("partial_results") or []
    merged_df = _merge_result_rows(current_rows, df_chunk)
    ratio = round(len(merged_df) / processed_total * 100, 1) if processed_total > 0 else 0
    suggested, tune_msg = calc_suggested_threshold(full_threshold, ratio)
    next_chunk_index = current_chunk_index + 1
    remaining_after_chunk = [ticker for ticker in tickers if ticker not in set(merged_processed_tickers)]

    progress_bar.progress(len(merged_processed_tickers) / total if total else 0)
    status_text.text(f"chunk {current_chunk_index + 1} / {len(chunks)} 완료")

    if not remaining_after_chunk or next_chunk_index >= len(chunks):
        st.session_state.update(
            {
                "result": merged_df,
                "scan_total": total,
                "scan_processed": processed_total,
                "scan_fail": fail_total,
                "scan_mode": "full",
                "scan_fallback": is_fallback,
                "scan_market": scan_market,
                "scan_ticker_src": ticker_src,
                "scan_price_src": price_src,
                "scan_cache_date": cache_date,
                "scan_ratio": ratio,
                "scan_threshold_used": full_threshold,
                "suggested_threshold": suggested,
                "tune_msg": tune_msg,
                "scan_running": False,
                "scan_interrupted": False,
                "chunk_executing": False,
                "partial_results": merged_df.to_dict("records"),
                "processed_tickers": merged_processed_tickers,
                "scan_progress": len(merged_processed_tickers),
                "scan_all_tickers": tickers,
                "scan_tickers": tickers,
                "current_chunk_index": len(chunks),
            }
        )
        return

    st.session_state.update(
        {
            "scan_total": total,
            "scan_processed": processed_total,
            "scan_fail": fail_total,
            "scan_mode": "full",
            "scan_fallback": is_fallback,
            "scan_market": scan_market,
            "scan_ticker_src": ticker_src,
            "scan_price_src": price_src,
            "scan_cache_date": cache_date,
            "scan_ratio": ratio,
            "scan_threshold_used": full_threshold,
            "suggested_threshold": suggested,
            "tune_msg": tune_msg,
            "scan_running": True,
            "scan_interrupted": False,
            "chunk_executing": False,
            "partial_results": merged_df.to_dict("records"),
            "processed_tickers": merged_processed_tickers,
            "scan_progress": len(merged_processed_tickers),
            "scan_all_tickers": tickers,
            "scan_tickers": tickers,
            "current_chunk_index": next_chunk_index,
        }
    )
    st.rerun()


# ── 화면 구성 ──────────────────────────────────────────────────
st.title("박스권 스캐너 컨트롤룸")

# ── 사이드바 ───────────────────────────────────────────────────
st.sidebar.header("스캔 설정")
market_choice = st.sidebar.selectbox(
    "시장 선택",
    ["KOSPI", "KOSDAQ", "ALL"],
    index=0,
    help="KOSPI / KOSDAQ / ALL(전체 병합)",
)
scan_mode = st.sidebar.radio(
    "스캔 모드",
    ["⚡ 빠른 스캔 (후보군)", "🔍 전체 스캔 (전종목)"],
    index=0,
)
is_full_scan = scan_mode.startswith("🔍")

if is_full_scan:
    market_label = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "ALL": "전체(KOSPI+KOSDAQ)"}.get(
        market_choice, market_choice
    )
    st.sidebar.info(f"{market_label} 전종목 스캔\n소요 시간: 수 분 예상")
    default_thresh = st.session_state.get("suggested_threshold", DEFAULT_FULL_THRESHOLD)
    full_threshold = st.sidebar.slider(
        "박스권 점수 threshold",
        min_value=40,
        max_value=95,
        value=default_thresh,
        step=5,
        help="이 점수 이상인 종목만 결과에 표시됩니다.",
    )
    st.sidebar.caption(f"현재: {full_threshold}점 이상 | 목표 비율: {RATIO_LOW}~{RATIO_HIGH}%")
    st.caption(f"🔍 전체 스캔 모드 ({market_choice}) — threshold: {full_threshold}점 이상")
else:
    top_n = st.sidebar.slider("후보군 종목 수", min_value=15, max_value=100, value=50, step=5)
    st.caption(f"⚡ 빠른 스캔 모드 ({market_choice}) — 거래량 상위 후보군 분석")
    full_threshold = DEFAULT_FULL_THRESHOLD

# ── 복구/이어달리기 화면 ─────────────────────────────────────
_saved_processed_tickers = _get_saved_processed_tickers()
_resume_total = st.session_state.get("scan_total", 0) or len(
    st.session_state.get("scan_all_tickers") or st.session_state.get("scan_tickers") or []
)
_resume_progress = len(_saved_processed_tickers)
show_resume_ui = (
    bool(st.session_state.get("scan_all_tickers") or st.session_state.get("scan_tickers"))
    and _resume_total > 0
    and _resume_progress < _resume_total
    and (
        st.session_state.get("scan_interrupted")
        or st.session_state.get("scan_running")
        or _resume_progress > 0
    )
)

if show_resume_ui:
    _prog = _resume_progress
    _total = _resume_total
    st.warning(
        f"⚠️ 이전 스캔이 중단되었거나 앱이 재실행되었습니다. "
        f"현재 진행: {_prog} / {_total} 종목. "
        "아래는 저장된 중간 결과입니다. 이어서 확인하거나 다시 스캔할 수 있습니다."
    )
    st.info(f"부분 결과 {len(st.session_state.get('partial_results', []))}건 복원됨 — 현재까지 {_prog} / {_total} 종목 기준입니다.")
    col_resume, col_clear = st.columns([1, 1])
    with col_resume:
        resume_btn = st.button("▶️ 이어서 스캔 재개", use_container_width=True)
    with col_clear:
        clear_partial_btn = st.button("🧹 중간 결과 비우기", use_container_width=True)

    if clear_partial_btn:
        st.session_state["trigger_clear"] = True

    if resume_btn:
        st.session_state["trigger_resume"] = True

# ── 스캔 버튼 ──────────────────────────────────────────────────
btn_label = f"🔍 {market_choice} 전체 스캔 시작" if is_full_scan else f"⚡ {market_choice} 빠른 스캔 시작"
if st.button(btn_label, use_container_width=True):
    if is_full_scan:
        st.session_state["chunk_executing"] = True
        _run_full_scan(scan_market=market_choice, full_threshold=full_threshold, resume=False)
    else:
        with st.spinner("분석 중..."):
            try:
                today_str = _get_date(1)
                tickers = None

                if _KRX_AVAILABLE:
                    try:
                        krx_market = "KOSDAQ" if market_choice == "KOSDAQ" else "KOSPI"
                        top_df = krx_stock.get_market_trading_volume_by_ticker(today_str, market=krx_market)
                        if top_df is not None and not top_df.empty:
                            tickers = [
                                str(t).zfill(6)
                                for t in top_df.sort_values("거래량", ascending=False).head(top_n).index.tolist()
                            ]
                    except Exception:
                        pass

                if not tickers:
                    fb = FALLBACK_KOSDAQ if market_choice == "KOSDAQ" else FALLBACK_KOSPI
                    tickers = fb

                if market_choice == "ALL" and _KRX_AVAILABLE:
                    try:
                        tickers_k = []
                        tickers_q = []
                        for mkt, store in [("KOSPI", tickers_k), ("KOSDAQ", tickers_q)]:
                            td = krx_stock.get_market_trading_volume_by_ticker(today_str, market=mkt)
                            if td is not None and not td.empty:
                                store += [
                                    str(t).zfill(6)
                                    for t in td.sort_values("거래량", ascending=False).head(top_n // 2).index.tolist()
                                ]
                        merged = list(dict.fromkeys(tickers_k + tickers_q))
                        merged.sort()
                        tickers = merged if merged else tickers
                    except Exception:
                        pass

                start = _get_date(90)
                end = _get_date(1)
                price_info = get_price_source_for_scan(tickers, start, end)

                df, processed_count, fail_count, _ = run_scan(
                    tickers=tickers,
                    score_threshold=FAST_SCORE_THRESHOLD,
                    price_source_info=price_info,
                )
                ratio = round(len(df) / processed_count * 100, 1) if processed_count > 0 else 0

                st.session_state.update(
                    {
                        "result": df,
                        "scan_total": len(tickers),
                        "scan_processed": processed_count,
                        "scan_fail": fail_count,
                        "scan_mode": "fast",
                        "scan_fallback": False,
                        "scan_market": market_choice,
                        "scan_ticker_src": "PYKRX",
                        "scan_price_src": price_info["source"],
                        "scan_cache_date": price_info.get("cache_date"),
                        "scan_ratio": ratio,
                        "scan_threshold_used": FAST_SCORE_THRESHOLD,
                        "tune_msg": None,
                        "suggested_threshold": DEFAULT_FULL_THRESHOLD,
                        "partial_results": [],
                        "processed_tickers": [],
                        "scan_all_tickers": [],
                        "scan_tickers": [],
                        "scan_progress": 0,
                        "scan_running": False,
                        "scan_interrupted": False,
                        "current_chunk_index": 0,
                        "chunk_executing": False,
                    }
                )
            except Exception as e:
                st.session_state["scan_running"] = False
                st.session_state["scan_interrupted"] = False
                st.session_state["partial_results"] = []
                st.session_state["processed_tickers"] = []
                st.session_state["scan_all_tickers"] = []
                st.session_state["scan_tickers"] = []
                st.session_state["scan_progress"] = 0
                st.session_state["current_chunk_index"] = 0
                st.session_state["chunk_executing"] = False
                st.error(f"오류 발생: {e}")

if st.session_state.get("trigger_clear"):
    st.session_state["trigger_clear"] = False
    st.session_state["trigger_resume"] = False
    _clear_partial_state()
    st.info("중간 결과를 비웠습니다. 새 스캔을 시작할 수 있습니다.")

if st.session_state.get("trigger_resume"):
    st.session_state["trigger_resume"] = False
    st.session_state["chunk_executing"] = True
    _run_full_scan(
        scan_market=st.session_state.get("scan_market", market_choice),
        full_threshold=st.session_state.get("scan_threshold_used", DEFAULT_FULL_THRESHOLD),
        resume=True,
    )

if (
    st.session_state.get("scan_running")
    and not st.session_state.get("chunk_executing")
    and not st.session_state.get("trigger_clear")
    and not st.session_state.get("trigger_resume")
):
    st.session_state["chunk_executing"] = True
    _run_full_scan(
        scan_market=st.session_state.get("scan_market", market_choice),
        full_threshold=st.session_state.get("scan_threshold_used", DEFAULT_FULL_THRESHOLD),
        resume=True,
    )

# ── 결과 표시 ──────────────────────────────────────────────────
_is_partial = False
if (
    (st.session_state.get("scan_running") or st.session_state.get("scan_interrupted"))
    and st.session_state.get("partial_results")
):
    display_df = pd.DataFrame(st.session_state["partial_results"])
    _is_partial = True
elif "result" in st.session_state:
    display_df = st.session_state["result"]
elif st.session_state.get("partial_results"):
    display_df = pd.DataFrame(st.session_state["partial_results"])
    _is_partial = True
else:
    display_df = None

if display_df is not None:
    df = display_df
    mode = st.session_state.get("scan_mode", "fast")
    scanned = st.session_state.get("scan_total", 0)
    processed = st.session_state.get("scan_processed", 0)
    fail = st.session_state.get("scan_fail", 0)
    is_fb = st.session_state.get("scan_fallback", False)
    ratio = round(len(df) / processed * 100, 1) if _is_partial and processed > 0 else (
        0 if _is_partial else st.session_state.get("scan_ratio", 0)
    )
    used_thresh = st.session_state.get("scan_threshold_used", 0)
    tune_msg = st.session_state.get("tune_msg", None)
    found = len(df)
    market = st.session_state.get("scan_market", "KOSPI")
    ticker_src = st.session_state.get("scan_ticker_src", "-")
    price_src = st.session_state.get("scan_price_src", "-")
    cache_date = st.session_state.get("scan_cache_date")
    mode_label = "전체 스캔" if mode == "full" else "빠른 스캔"
    status = _source_status(ticker_src, price_src, is_fb, cache_date)

    st.divider()
    _render_badge_row(market, ticker_src, price_src, mode_label, cache_date, status)
    st.divider()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 대상", f"{scanned}개")
    col2.metric("정상 처리", f"{processed}개")
    col3.metric("실패/스킵", f"{fail}개")
    col4.metric("박스권 후보", f"{found}개  ({ratio}%)")

    if tune_msg and not _is_partial:
        suggested = st.session_state.get("suggested_threshold", used_thresh)
        if ratio > RATIO_HIGH:
            st.warning(f"⚠️ {tune_msg}\n\n사이드바 슬라이더를 **{suggested}**으로 조정 후 재스캔하세요.")
        else:
            st.info(f"ℹ️ {tune_msg}\n\n사이드바 슬라이더를 **{suggested}**으로 조정 후 재스캔하세요.")

    if st.session_state.get("scan_interrupted") is True:
        st.error("스캔 중 오류 발생 — 아래는 저장된 부분 결과입니다.")

    if _is_partial:
        st.warning("⚠️ 전체 스캔이 완료되기 전 중단되었습니다. 아래는 부분 결과입니다.")

    if df.empty:
        st.warning(f"조건에 맞는 종목 없음 — threshold {used_thresh}점 이상 박스권 종목이 없습니다.")
    else:
        if not _is_partial:
            if mode == "full" and not is_fb:
                st.success(f"{market} {scanned}개 중 **{found}개** 박스권 후보 ({ratio}%) | threshold {used_thresh}점")
            elif is_fb:
                st.error(f"🚨 제한 모드 — {scanned}개 중 **{found}개** 후보 ({ratio}%) | threshold {used_thresh}점")
            else:
                st.success(f"후보군 {scanned}개 분석 완료 — **{found}개** 결과 ({ratio}%)")

        df = df.sort_values(by=["점수", "거래량"], ascending=[False, False]).reset_index(drop=True)

        top_n_cards = min(len(df), 5)
        top_df = df.head(top_n_cards)
        st.subheader("🏆 지금 봐야 할 종목 TOP 5")
        if _is_partial:
            st.caption("⚠️ 부분 결과 기준 TOP 5")
        st.caption("점수 + 거래량 기준 — 즉시 판단용")

        cols_per_row = 2 if top_n_cards >= 4 else top_n_cards
        rows = [top_df[i : i + cols_per_row] for i in range(0, len(top_df), cols_per_row)]

        for row_df in rows:
            cols = st.columns(len(row_df))
            for col, (_, row) in zip(cols, row_df.iterrows()):
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{row['종목명']}**")
                        st.caption(f"`{row['종목코드']}`")

                        score = row["점수"]
                        if score >= 90:
                            emoji = "🔥"
                        elif score >= 80:
                            emoji = "⚡"
                        else:
                            emoji = "🟡"

                        st.metric(label="점수", value=f"{score}점 {emoji}")
                        st.write(f"{row['돌파신호']}")
                        st.write(f"🧠 {row['이유']}")

        st.divider()
        st.subheader("박스권 후보")
        st.caption(f"threshold {used_thresh}점 이상 | 높을수록 박스권 가능성 높음 (100점 최고) | 시장: {market}")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()

        name_list = df["종목명"].tolist()
        selected_name = st.selectbox("차트 볼 종목 선택", ["선택하세요"] + name_list)

        if selected_name != "선택하세요":
            ticker_code = df.loc[df["종목명"] == selected_name, "종목코드"].values[0]
            score = df.loc[df["종목명"] == selected_name, "점수"].values[0]
            st.subheader(f"{selected_name} — 최근 90일 종가")
            st.caption(f"박스권 점수: {score}점")
            with st.spinner("차트 불러오는 중..."):
                chart_df = get_price_chart(ticker_code)
            if chart_df is None or chart_df.empty:
                st.warning("차트 데이터를 불러올 수 없습니다.")
            else:
                st.line_chart(chart_df)
