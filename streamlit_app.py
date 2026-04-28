"""
streamlit_app.py — v11.5
박스권 스캐너 컨트롤룸

변경 이력:
  v11.5 - 점수 신뢰도 검증 데이터 수집 기능 추가
          차트 하단 판단 버튼 (👍/🤔/👎) 추가
          validation_log session_state 누적 저장 (중복 overwrite)
          점수 구간별 검증 통계 표시 (80점↑ / 70~79 / 70점↓)
          검증 데이터 CSV 다운로드 버튼 추가
          검증 초기화 버튼 추가
          [안정화] 최소 샘플 수 경고 (20개 미만)
          [안정화] 총 검증 데이터 개수 표시
          [안정화] 현재 종목 판단 기록 → 버튼 위로 이동
          [안정화] 버튼 클릭 시 st.toast() 즉시 피드백
          [안정화] 점수 신뢰도 가이드 expander 추가
  v11.4 - 점수 vs 차트 검증 UI 추가
          _box_label() 판단 라벨 (🟢/🟡/🔴) 적용
          TOP5 카드 판단 라벨 추가
          테이블 위 검증 요약 통계 추가
          차트 상단 점수+판단 표시 / 하단 체크리스트 expander 추가
  v11.3 - 종목 차트 교체 — 막대 차트 → 일봉 캔들스틱 (Plotly Candlestick)
          상단 봉차트 (상승=빨강/하락=파랑) + 하단 거래량 보조 차트
          get_price_chart() 거래량 컬럼 반환 추가
          make_subplots 기반 2단 레이아웃 적용
  v11.2 - 전체 스캔 점수 필터 단일 threshold → 범위 슬라이더 (min~max) 변경
          run_scan()에는 score_min만 전달 (엔진 안정성 유지)
          화면 표시 직전 score_max 필터링 적용
          session_state에 scan_score_min / scan_score_max / ui_score_range 저장
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

import json
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from box_range_scanner import (
    FALLBACK_KOSDAQ,
    FALLBACK_KOSPI,
    _get_date,
    get_price_source_for_scan,
    run_scan,
)

try:
    import FinanceDataReader as fdr

    _FDR_AVAILABLE = True
except ImportError:
    _FDR_AVAILABLE = False

# ── 상수 ──────────────────────────────────────────────────────
FAST_SCORE_THRESHOLD = 0
DEFAULT_SCORE_MIN = 50
DEFAULT_SCORE_MAX = 70
DEFAULT_FULL_THRESHOLD = DEFAULT_SCORE_MIN
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

if "ui_score_range" not in st.session_state:
    st.session_state["ui_score_range"] = (DEFAULT_SCORE_MIN, DEFAULT_SCORE_MAX)

if "validation_log" not in st.session_state:
    st.session_state["validation_log"] = []

# pykrx (종목명 조회 / 빠른 스캔 거래량 상위)
try:
    from pykrx import stock as krx_stock

    _KRX_AVAILABLE = True
except ImportError:
    _KRX_AVAILABLE = False


TICKER_CACHE_PATH = Path(__file__).with_name("ticker_cache.json")


def _normalize_tickers(tickers):
    normalized = []
    seen = set()
    for ticker in tickers or []:
        code = str(ticker).strip().zfill(6)
        if code and code not in seen:
            seen.add(code)
            normalized.append(code)
    normalized.sort()
    return normalized


def _ticker_fallback_for_market(market):
    return FALLBACK_KOSPI if market == "KOSPI" else FALLBACK_KOSDAQ


def _ticker_cache_read():
    if not TICKER_CACHE_PATH.exists():
        return {}
    with TICKER_CACHE_PATH.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return data if isinstance(data, dict) else {}


def _ticker_cache_write(data):
    with TICKER_CACHE_PATH.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def save_ticker_cache(market, tickers, source):
    tickers = _normalize_tickers(tickers)
    if market not in {"KOSPI", "KOSDAQ"} or not tickers:
        return
    try:
        cache = _ticker_cache_read()
    except Exception:
        cache = {}
    cache[market] = {
        "source": source,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tickers": tickers,
    }
    _ticker_cache_write(cache)


def load_ticker_cache(market):
    cache = _ticker_cache_read()
    entry = cache.get(market, {})
    tickers = _normalize_tickers(entry.get("tickers"))
    if not tickers:
        raise ValueError(f"empty ticker cache for {market}")
    return tickers, entry.get("updated_at")


def _is_limited_ticker_mode(market, source, ticker_count):
    source = source or ""
    if source == "FALLBACK":
        return True
    if "FALLBACK" not in source:
        return False
    if market == "ALL":
        return ticker_count < 800
    return ticker_count < 200


def get_tickers_with_fallback(market):
    logs = []
    market_up = market.upper()

    if market_up == "ALL":
        kospi = get_tickers_with_fallback("KOSPI")
        kosdaq = get_tickers_with_fallback("KOSDAQ")
        tickers = _normalize_tickers(kospi["tickers"] + kosdaq["tickers"])
        sources = []
        for source in [kospi["source"], kosdaq["source"]]:
            if source not in sources:
                sources.append(source)
        source = "+".join(sources) if len(sources) > 1 else sources[0]
        cache_dates = [d for d in [kospi.get("cache_date"), kosdaq.get("cache_date")] if d]
        logs.extend(kospi["log"])
        logs.extend(kosdaq["log"])
        logs.append(f"[INFO] ALL ticker merge complete: {len(tickers)} tickers")
        return {
            "tickers": tickers,
            "source": source,
            "cache_date": max(cache_dates) if cache_dates else None,
            "log": logs,
        }

    if _FDR_AVAILABLE:
        try:
            df = fdr.StockListing(market_up)
            if df is not None and not df.empty:
                code_col = next((c for c in ["Code", "Symbol", "종목코드"] if c in df.columns), None)
                if code_col:
                    tickers = _normalize_tickers(df[code_col].tolist())
                    if tickers:
                        save_ticker_cache(market_up, tickers, source="FDR")
                        logs.append(f"[INFO] ticker source FDR-{market_up}: {len(tickers)} tickers")
                        return {"tickers": tickers, "source": "FDR", "cache_date": None, "log": logs}
                logs.append(f"[WARN] FDR StockListing({market_up}) returned no code column")
        except Exception as e:
            logs.append(f"[WARN] FDR StockListing({market_up}) failed: {e}")
    else:
        logs.append("[WARN] FinanceDataReader is not available")

    if _KRX_AVAILABLE:
        try:
            tickers = _normalize_tickers(krx_stock.get_market_ticker_list(market=market_up))
            if tickers:
                save_ticker_cache(market_up, tickers, source="PYKRX")
                logs.append(f"[INFO] ticker source PYKRX-{market_up}: {len(tickers)} tickers")
                return {"tickers": tickers, "source": "PYKRX", "cache_date": None, "log": logs}
            logs.append(f"[WARN] pykrx get_market_ticker_list({market_up}) returned empty list")
        except Exception as e:
            logs.append(f"[WARN] pykrx get_market_ticker_list({market_up}) failed: {e}")
    else:
        logs.append("[WARN] pykrx is not available")

    try:
        tickers, cache_date = load_ticker_cache(market_up)
        logs.append(f"[WARN] ticker cache used for {market_up}: {cache_date} ({len(tickers)} tickers)")
        return {"tickers": tickers, "source": "CACHE", "cache_date": cache_date, "log": logs}
    except Exception as e:
        logs.append(f"[WARN] ticker cache failed for {market_up}: {e}")

    tickers = _normalize_tickers(_ticker_fallback_for_market(market_up))
    logs.append(f"[ERROR] ticker fallback used for {market_up}: {len(tickers)} tickers")
    return {"tickers": tickers, "source": "FALLBACK", "cache_date": None, "log": logs}


def get_market_tickers(market="KOSPI"):
    return get_tickers_with_fallback(market)


# ── 유틸 ──────────────────────────────────────────────────────
def calc_suggested_threshold(current, ratio):
    if ratio > RATIO_HIGH:
        return current + 5, f"후보 비율 {ratio}% > {RATIO_HIGH}% — 하한값(score_min) {current} → {current+5} 권고"
    if ratio < RATIO_LOW:
        return current - 5, f"후보 비율 {ratio}% < {RATIO_LOW}% — 하한값(score_min) {current} → {current-5} 권고"
    return current, None


def get_price_chart(ticker_code, days=120):
    end = _get_date(1)
    start = _get_date(days)
    if _KRX_AVAILABLE:
        try:
            df = krx_stock.get_market_ohlcv_by_date(start, end, ticker_code)
            if df is not None and not df.empty and {"시가", "종가"}.issubset(df.columns):
                cols = [col for col in ["시가", "고가", "저가", "종가", "거래량"] if col in df.columns]
                return df[cols]
        except Exception:
            pass
    return None


def _source_status(ticker_src, price_src, is_fallback, cache_date):
    ticker_src = ticker_src or ""
    price_src = price_src or ""
    if is_fallback or ticker_src == "FALLBACK":
        return "위기"
    if "CACHE" in ticker_src or "CACHE" in price_src or cache_date:
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
        st.success("✅ 정상 수집 — FDR/PYKRX 기반 ticker 목록으로 스캔 중")
    elif status == "경고":
        st.warning(f"⚠️ 캐시 모드 — [{cache_date}] 기준 최근 성공 ticker 목록을 사용 중")
    else:
        st.error("🚨 제한 모드 — cache까지 실패해 최종 fallback 종목 기준으로 스캔 중")


def _render_price_flow_chart(chart_df):
    df = chart_df.copy()
    has_volume = "거래량" in df.columns and df["거래량"].sum() > 0

    if has_volume:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.75, 0.25],
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    # 상단 — 캔들스틱
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["시가"],
            high=df["고가"] if "고가" in df.columns else df["종가"],
            low=df["저가"] if "저가" in df.columns else df["종가"],
            close=df["종가"],
            increasing_line_color="#ef4444",
            decreasing_line_color="#3b82f6",
            increasing_fillcolor="#ef4444",
            decreasing_fillcolor="#3b82f6",
            name="일봉",
        ),
        row=1, col=1,
    )

    # 이동평균선 20 / 60일
    ma_styles = [
        (20,  "#a78bfa", "MA20"),   # 보라
        (60,  "#34d399", "MA60"),   # 초록
    ]
    for period, color, label in ma_styles:
        ma = df["종가"].rolling(period, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=ma,
                mode="lines",
                line=dict(color=color, width=1.2),
                name=label,
                opacity=0.85,
            ),
            row=1, col=1,
        )

    # 하단 — 거래량
    if has_volume:
        is_up = df["종가"] >= df["시가"]
        vol_colors = ["#ef4444" if u else "#3b82f6" for u in is_up]
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["거래량"],
                marker_color=vol_colors,
                name="거래량",
                opacity=0.6,
            ),
            row=2, col=1,
        )

    fig.update_layout(
        height=460,
        margin=dict(l=8, r=8, t=8, b=8),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            font=dict(size=11),
        ),
        xaxis_rangeslider_visible=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(
        showgrid=False,
        tickformat="%m/%d",
        tickangle=0,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(148, 163, 184, 0.18)",
        zeroline=False,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


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


def _score_range_label(score_min, score_max):
    return f"{score_min}~{score_max}점"


def _save_validation(ticker_code, ticker_name, score, judgment):
    """validation_log에 판단 저장 — 동일 종목 재클릭 시 overwrite"""
    log = st.session_state.get("validation_log", [])
    log = [v for v in log if v["종목코드"] != ticker_code]
    log.append({"종목코드": ticker_code, "종목명": ticker_name, "점수": score, "판단": judgment})
    st.session_state["validation_log"] = log


def _get_display_score_range():
    ui_range = st.session_state.get("ui_score_range")
    if isinstance(ui_range, (list, tuple)) and len(ui_range) == 2:
        score_min = int(ui_range[0])
        score_max = int(ui_range[1])
    else:
        score_min = int(st.session_state.get("scan_score_min", DEFAULT_SCORE_MIN) or DEFAULT_SCORE_MIN)
        score_max = int(st.session_state.get("scan_score_max", DEFAULT_SCORE_MAX) or DEFAULT_SCORE_MAX)

    score_min = max(0, min(100, score_min))
    score_max = max(score_min, min(100, score_max))
    return score_min, score_max


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
        "scan_score_min": DEFAULT_SCORE_MIN,
        "scan_score_max": DEFAULT_SCORE_MAX,
        "ui_score_range": (DEFAULT_SCORE_MIN, DEFAULT_SCORE_MAX),
        "trigger_resume": False,
        "trigger_clear": False,
        "current_chunk_index": 0,
        "chunk_executing": False,
    }.items():
        st.session_state[key] = val
    st.session_state.pop("result", None)


def _run_full_scan(scan_market, score_min, score_max=None, resume=False, days=120):
    """전체 스캔 실행/재개 공통 루틴"""
    if not resume:
        if score_max is None:
            score_max = DEFAULT_SCORE_MAX

        with st.spinner(f"{scan_market} 종목 목록 수집 중..."):
            ticker_result = get_market_tickers(scan_market)

        tickers = ticker_result["tickers"]
        ticker_src = ticker_result["source"]
        ticker_logs = ticker_result["log"]
        ticker_cache_date = ticker_result.get("cache_date")
        is_fallback = _is_limited_ticker_mode(scan_market, ticker_src, len(tickers))

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
        st.info(f"{scan_market} {total}개 종목 스캔 시작 (점수 범위: {_score_range_label(score_min, score_max)})")

        st.session_state.update(
            {
                "scan_running": True,
                "scan_market": scan_market,
                "scan_mode": "full",
                "scan_total": total,
                "scan_processed": 0,
                "scan_fail": 0,
                "scan_ratio": 0,
                "scan_threshold_used": score_min,
                "scan_score_min": score_min,
                "scan_score_max": score_max,
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

    score_min = st.session_state.get("scan_score_min", score_min)
    if score_max is None:
        score_max = st.session_state.get("scan_score_max", DEFAULT_SCORE_MAX)
    else:
        score_max = st.session_state.get("scan_score_max", score_max)

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
        suggested, tune_msg = calc_suggested_threshold(score_min, ratio)

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
                "scan_threshold_used": score_min,
                "scan_score_min": score_min,
                "scan_score_max": score_max,
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
            f"| 현재 진행 {len(processed_tickers_base)} / {total} | 범위 {_score_range_label(score_min, score_max)}"
        )
    else:
        st.info(
            f"chunk {current_chunk_index + 1} / {len(chunks)} 실행 중 — "
            f"{len(current_chunk)}개 종목 | 범위 {_score_range_label(score_min, score_max)}"
        )

    start = _get_date(days)
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
            "scan_threshold_used": score_min,
            "scan_score_min": score_min,
            "scan_score_max": score_max,
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
            score_threshold=score_min,
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
    suggested, tune_msg = calc_suggested_threshold(score_min, ratio)
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
                "scan_threshold_used": score_min,
                "scan_score_min": score_min,
                "scan_score_max": score_max,
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
            "scan_threshold_used": score_min,
            "scan_score_min": score_min,
            "scan_score_max": score_max,
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

analysis_days = 90

if is_full_scan:
    market_label = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "ALL": "전체(KOSPI+KOSDAQ)"}.get(
        market_choice, market_choice
    )
    st.sidebar.info(f"{market_label} 전종목 스캔\n소요 시간: 수 분 예상")
    score_min, score_max = st.sidebar.slider(
        "박스권 점수 범위",
        min_value=0,
        max_value=100,
        value=st.session_state.get("ui_score_range", (DEFAULT_SCORE_MIN, DEFAULT_SCORE_MAX)),
        step=5,
        help="이 범위에 해당하는 박스권 점수 종목만 표시합니다.",
    )
    st.session_state["ui_score_range"] = (score_min, score_max)
    st.sidebar.caption(f"현재 범위: {score_min} ~ {score_max}점 | 목표 비율: {RATIO_LOW} ~ {RATIO_HIGH}%")
    st.caption(f"🔍 전체 스캔 모드 ({market_choice}) — 점수 범위: {score_min} ~ {score_max}점")
else:
    top_n = st.sidebar.slider("후보군 종목 수", min_value=15, max_value=100, value=50, step=5)
    st.caption(f"⚡ 빠른 스캔 모드 ({market_choice}) — 거래량 상위 후보군 분석")
    score_min = DEFAULT_FULL_THRESHOLD
    score_max = 100

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
    st.session_state["scan_days"] = analysis_days
    if is_full_scan:
        st.session_state["chunk_executing"] = True
        _run_full_scan(scan_market=market_choice, score_min=score_min, score_max=score_max, resume=False, days=analysis_days)
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

                start = _get_date(analysis_days)
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
                        "scan_score_min": FAST_SCORE_THRESHOLD,
                        "scan_score_max": 100,
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
        score_min=st.session_state.get("scan_score_min", DEFAULT_FULL_THRESHOLD),
        score_max=st.session_state.get("scan_score_max", DEFAULT_SCORE_MAX),
        resume=True,
        days=st.session_state.get("scan_days", 120),
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
        score_min=st.session_state.get("scan_score_min", DEFAULT_FULL_THRESHOLD),
        score_max=st.session_state.get("scan_score_max", DEFAULT_SCORE_MAX),
        resume=True,
        days=st.session_state.get("scan_days", 120),
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
    mode = st.session_state.get("scan_mode", "fast")
    df = display_df.copy()
    display_score_min, display_score_max = _get_display_score_range()
    score_range_label = _score_range_label(display_score_min, display_score_max)
    if mode == "full" and "점수" in df.columns:
        df = df[(df["점수"] >= display_score_min) & (df["점수"] <= display_score_max)].copy()
    scanned = st.session_state.get("scan_total", 0)
    processed = st.session_state.get("scan_processed", 0)
    fail = st.session_state.get("scan_fail", 0)
    is_fb = st.session_state.get("scan_fallback", False)
    ratio = round(len(df) / processed * 100, 1) if processed > 0 else 0
    used_score_min = st.session_state.get("scan_score_min", DEFAULT_FULL_THRESHOLD)
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
        suggested = st.session_state.get("suggested_threshold", used_score_min)
        if ratio > RATIO_HIGH:
            st.warning(f"⚠️ {tune_msg}\n\n하한값(score_min)을 **{suggested}**으로 조정 후 재스캔하세요.")
        else:
            st.info(f"ℹ️ {tune_msg}\n\n하한값(score_min)을 **{suggested}**으로 조정 후 재스캔하세요.")

    if st.session_state.get("scan_interrupted") is True:
        st.error("스캔 중 오류 발생 — 아래는 저장된 부분 결과입니다.")

    if _is_partial:
        st.warning("⚠️ 전체 스캔이 완료되기 전 중단되었습니다. 아래는 부분 결과입니다.")

    if df.empty:
        if mode == "full":
            st.warning(f"조건에 맞는 종목 없음 — {score_range_label} 범위 내 종목이 없습니다.")
        else:
            st.warning("조건에 맞는 종목 없음 — 박스권 종목이 없습니다.")
    else:
        if not _is_partial:
            if mode == "full" and not is_fb:
                st.success(f"{market} {scanned}개 중 **{found}개** 박스권 후보 ({ratio}%) | {score_range_label} 범위")
            elif mode == "full" and is_fb:
                st.error(f"🚨 제한 모드 — {scanned}개 중 **{found}개** 후보 ({ratio}%) | {score_range_label} 범위")
            else:
                st.success(f"후보군 {scanned}개 분석 완료 — **{found}개** 결과 ({ratio}%)")

        df = df.sort_values(by=["점수", "거래량"], ascending=[False, False]).reset_index(drop=True)

        # 종목명 str 보장
        if "종목명" in df.columns:
            df["종목명"] = df["종목명"].astype(str)

        # ── 판단 라벨 함수 ──────────────────────────────────────
        def _box_label(score):
            if score >= 80:
                return "🟢 박스권 가능성 높음"
            elif score >= 70:
                return "🟡 애매 구간"
            else:
                return "🔴 박스권 아님"

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
                        st.caption(_box_label(score))
                        st.write(f"{row['돌파신호']}")
                        st.write(f"🧠 {row['이유']}")

        st.divider()

        # ── 검증 요약 통계 ──────────────────────────────────────
        cnt_high = len(df[df["점수"] >= 80])
        cnt_mid  = len(df[(df["점수"] >= 70) & (df["점수"] < 80)])
        cnt_low  = len(df[df["점수"] < 70])
        st.markdown(
            f"🧪 **검증 요약** &nbsp;|&nbsp; "
            f"🟢 80점 이상: **{cnt_high}개** &nbsp; "
            f"🟡 70~79점: **{cnt_mid}개** &nbsp; "
            f"🔴 70점 미만: **{cnt_low}개**"
        )

        st.subheader("박스권 후보")
        if mode == "full":
            st.caption(f"{score_range_label} 범위 | 높을수록 박스권 가능성 높음 (100점 최고) | 시장: {market}")
        else:
            st.caption(f"높을수록 박스권 가능성 높음 (100점 최고) | 시장: {market}")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()

        name_list = df["종목명"].tolist()
        selected_name = st.selectbox("차트 볼 종목 선택", ["선택하세요"] + name_list)

        if selected_name != "선택하세요":
            ticker_code = df.loc[df["종목명"] == selected_name, "종목코드"].values[0]
            score = df.loc[df["종목명"] == selected_name, "점수"].values[0]

            # ── 검증용 태그 ────────────────────────────────────
            box_label = _box_label(score)
            st.subheader(f"{selected_name} — 최근 {analysis_days}일 가격 흐름")
            col_score, col_label = st.columns([1, 3])
            with col_score:
                st.metric(label="박스권 점수", value=f"{score}점")
            with col_label:
                st.markdown(f"**판단** &nbsp; {box_label}")
                st.caption("[검증용] — 알고리즘 기준 임시 분류")

            with st.spinner("차트 불러오는 중..."):
                chart_df = get_price_chart(ticker_code, days=analysis_days)
            if chart_df is None or chart_df.empty:
                st.warning("차트 데이터를 불러올 수 없습니다.")
            else:
                st.caption("빨강=상승 / 파랑=하락 | 일봉 기준")
                _render_price_flow_chart(chart_df)

                # ── 사용자 체크 유도 문구 ──────────────────────
                with st.expander("🔍 이 종목이 실제로 박스권처럼 보이나요? (검증 체크리스트)"):
                    st.markdown("""
- 📊 **횡보 구간인가?** — 일정 기간 동안 방향 없이 횡보하는가?
- 📐 **위/아래 변동이 제한되어 있는가?** — 고가/저가가 일정 범위 안에 갇혀있는가?
- 🔒 **추세 없이 갇혀 있는 느낌인가?** — MA20/MA60이 수평에 가까운가?

👉 위 3개가 모두 **Yes** → 점수와 일치 ✅  
👉 하나라도 **No** → 점수와 불일치 ⚠️ (엔진 재검토 필요)
""")

                # ── 사용자 판단 버튼 ────────────────────────────
                st.markdown("**📝 이 종목 판단 기록**")

                # 현재 기록 → 버튼 위로 이동
                existing = next(
                    (v for v in st.session_state["validation_log"] if v["종목코드"] == ticker_code),
                    None
                )
                if existing:
                    judge_emoji = {"맞음": "👍", "애매": "🤔", "아님": "👎"}.get(existing["판단"], "")
                    st.info(f"현재 기록: {judge_emoji} **{existing['판단']}** (점수 {existing['점수']}점) — 아래 버튼으로 수정 가능")
                else:
                    st.caption("아직 판단 기록 없음 — 아래에서 선택하세요")

                btn_col1, btn_col2, btn_col3 = st.columns(3)
                with btn_col1:
                    if st.button("👍 박스권 맞음", key=f"val_yes_{ticker_code}", use_container_width=True):
                        _save_validation(ticker_code, selected_name, score, "맞음")
                        st.toast("✅ 맞음으로 기록됐어요", icon="👍")
                with btn_col2:
                    if st.button("🤔 애매함", key=f"val_mid_{ticker_code}", use_container_width=True):
                        _save_validation(ticker_code, selected_name, score, "애매")
                        st.toast("🤔 애매함으로 기록됐어요", icon="🤔")
                with btn_col3:
                    if st.button("👎 아님", key=f"val_no_{ticker_code}", use_container_width=True):
                        _save_validation(ticker_code, selected_name, score, "아님")
                        st.toast("❌ 아님으로 기록됐어요", icon="👎")

# ── 검증 통계 & 다운로드 ───────────────────────────────────────
vlog = st.session_state.get("validation_log", [])
if vlog:
    st.divider()
    st.subheader("📊 검증 결과")

    # 샘플 수 경고 + 로그 개수
    vlog_count = len(vlog)
    st.caption(f"총 검증 데이터: {vlog_count}개")
    if vlog_count < 20:
        st.warning(f"⚠️ 검증 데이터가 부족합니다 ({vlog_count}개 / 최소 20개 필요) — 더 많은 종목을 판단해주세요")

    vdf = pd.DataFrame(vlog)

    def _vstat(df, min_s, max_s):
        sub = df[(df["점수"] >= min_s) & (df["점수"] < max_s)]
        if sub.empty:
            return {"맞음": 0, "애매": 0, "아님": 0, "합계": 0}
        counts = sub["판단"].value_counts().to_dict()
        return {
            "맞음": counts.get("맞음", 0),
            "애매": counts.get("애매", 0),
            "아님": counts.get("아님", 0),
            "합계": len(sub),
        }

    stat_high = _vstat(vdf, 80, 101)
    stat_mid  = _vstat(vdf, 70, 80)
    stat_low  = _vstat(vdf, 0,  70)

    col_h, col_m, col_l = st.columns(3)
    with col_h:
        st.markdown("**🟢 80점 이상**")
        st.markdown(f"- 맞음: **{stat_high['맞음']}**")
        st.markdown(f"- 애매: **{stat_high['애매']}**")
        st.markdown(f"- 아님: **{stat_high['아님']}**")
        st.caption(f"합계 {stat_high['합계']}개")
    with col_m:
        st.markdown("**🟡 70~79점**")
        st.markdown(f"- 맞음: **{stat_mid['맞음']}**")
        st.markdown(f"- 애매: **{stat_mid['애매']}**")
        st.markdown(f"- 아님: **{stat_mid['아님']}**")
        st.caption(f"합계 {stat_mid['합계']}개")
    with col_l:
        st.markdown("**🔴 70점 미만**")
        st.markdown(f"- 맞음: **{stat_low['맞음']}**")
        st.markdown(f"- 애매: **{stat_low['애매']}**")
        st.markdown(f"- 아님: **{stat_low['아님']}**")
        st.caption(f"합계 {stat_low['합계']}개")

    # 신뢰도 가이드
    with st.expander("📌 점수 신뢰도 판단 기준"):
        st.markdown("""
- 🟢 **80점 이상** → 맞음 비율 **70% 이상**이면 엔진 정상
- 🟡 **70~79점** → 애매 비율이 높아야 정상 (경계 구간)
- 🔴 **70점 미만** → 아님 비율이 높아야 정상 (걸러지는 구간)

👉 패턴이 위와 다르면 → **v11.6 점수 튜닝 필요**
""")

    st.divider()
    dl_col, clear_col = st.columns([3, 1])
    with dl_col:
        csv_data = vdf.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 검증 데이터 다운로드 (CSV)",
            data=csv_data,
            file_name="validation_log.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with clear_col:
        if st.button("🧹 검증 초기화", use_container_width=True):
            st.session_state["validation_log"] = []
            st.rerun()
