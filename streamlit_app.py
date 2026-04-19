"""
streamlit_app.py — v9.0
박스권 스캐너 컨트롤룸

변경 이력:
  v9.0 - 입력 레이어 리팩토링 (멈추지 않는 스캐너)
         시장 선택 UI (KOSPI / KOSDAQ / ALL)
         멀티소스 fallback 구조 (FDR → NAVER → yfinance → 캐시)
         소스 투명도 UI (컨트롤룸 배지 한 줄)
         상태별 컬러 코드 (정상/경고/위기)
         캐시 사용 시 날짜 배지
  v8.3 - threshold 슬라이더, 튜닝 권고 배너
"""

import streamlit as st
from datetime import datetime, timedelta

from box_range_scanner import (
    run_scan,
    get_market_tickers,
    get_price_source_for_scan,
    FALLBACK_KOSPI,
    FALLBACK_KOSDAQ,
    _get_date,
)

# ── 상수 ──────────────────────────────────────────────────────
FAST_SCORE_THRESHOLD   = 0
DEFAULT_FULL_THRESHOLD = 70
RATIO_HIGH             = 20.0
RATIO_LOW              = 10.0

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
    elif ratio < RATIO_LOW:
        return current - 5, f"후보 비율 {ratio}% < {RATIO_LOW}% — threshold {current} → {current-5} 권고"
    return current, None


def get_price_chart(ticker_code):
    end   = _get_date(1)
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
    """
    소스 조합 기준 상태 결정
    정상: FDR 계열 정상
    경고: CACHE 포함
    위기: FALLBACK 포함
    """
    if is_fallback or ticker_src == "FALLBACK":
        return "위기"
    if "CACHE" in (ticker_src, price_src) or cache_date:
        return "경고"
    return "정상"


def _render_badge_row(market, ticker_src, price_src, mode_label, cache_date, status):
    """컨트롤룸 정보 배지 한 줄"""
    cols = st.columns(5)
    items = [
        ("시장",         market),
        ("ticker 소스",  ticker_src),
        ("가격 소스",    price_src),
        ("모드",         mode_label),
        ("캐시",         f"📅 {cache_date}" if cache_date else "없음"),
    ]
    for col, (label, val) in zip(cols, items):
        col.markdown(f"**{label}**  \n`{val}`")

    if status == "정상":
        st.success("✅ 정상 수집 — FDR 기반 실시간 데이터")
    elif status == "경고":
        st.warning(f"⚠️ 캐시 데이터 사용 중 — [{cache_date}] 기준 최근 성공 데이터로 스캔되었습니다")
    else:
        st.error("🚨 제한 모드 — 현재 데이터가 제한적입니다. fallback 종목 기준으로 스캔 중")


# ── 화면 구성 ──────────────────────────────────────────────────
st.title("박스권 스캐너 컨트롤룸")

# ── 사이드바 ───────────────────────────────────────────────────
st.sidebar.header("스캔 설정")

# 시장 선택
market_choice = st.sidebar.selectbox(
    "시장 선택",
    ["KOSPI", "KOSDAQ", "ALL"],
    index=0,
    help="KOSPI / KOSDAQ / ALL(전체 병합)"
)

scan_mode = st.sidebar.radio(
    "스캔 모드",
    ["⚡ 빠른 스캔 (후보군)", "🔍 전체 스캔 (전종목)"],
    index=0,
)
is_full_scan = scan_mode.startswith("🔍")

if is_full_scan:
    market_label = {"KOSPI":"코스피","KOSDAQ":"코스닥","ALL":"전체(KOSPI+KOSDAQ)"}.get(market_choice, market_choice)
    st.sidebar.info(f"{market_label} 전종목 스캔\n소요 시간: 수 분 예상")

    default_thresh = st.session_state.get("suggested_threshold", DEFAULT_FULL_THRESHOLD)
    full_threshold = st.sidebar.slider(
        "박스권 점수 threshold",
        min_value=40, max_value=95, value=default_thresh, step=5,
        help="이 점수 이상인 종목만 결과에 표시됩니다."
    )
    st.sidebar.caption(f"현재: {full_threshold}점 이상 | 목표 비율: {RATIO_LOW}~{RATIO_HIGH}%")
    st.caption(f"🔍 전체 스캔 모드 ({market_choice}) — threshold: {full_threshold}점 이상")
else:
    top_n = st.sidebar.slider("후보군 종목 수", min_value=15, max_value=100, value=50, step=5)
    st.caption(f"⚡ 빠른 스캔 모드 ({market_choice}) — 거래량 상위 후보군 분석")
    full_threshold = DEFAULT_FULL_THRESHOLD


# ── 스캔 버튼 ──────────────────────────────────────────────────
btn_label = f"🔍 {market_choice} 전체 스캔 시작" if is_full_scan else f"⚡ {market_choice} 빠른 스캔 시작"

if st.button(btn_label, use_container_width=True):

    if is_full_scan:
        # ── 전체 스캔 ─────────────────────────────────────────
        with st.spinner(f"{market_choice} 종목 목록 수집 중..."):
            ticker_result = get_market_tickers(market_choice)

        tickers      = ticker_result["tickers"]
        ticker_src   = ticker_result["source"]
        ticker_logs  = ticker_result["log"]
        ticker_cache_date = ticker_result.get("cache_date")
        is_fallback  = (ticker_src == "FALLBACK")

        # ticker 로그 출력
        for log in ticker_logs:
            if log.startswith("[INFO]"):
                st.success(log)
            elif log.startswith("[WARN]"):
                st.warning(log)
            elif log.startswith("[ERROR]"):
                st.error(log)

        if not tickers:
            st.error("종목 수집 전체 실패 — 잠시 후 재시도 해주세요.")
            st.stop()

        # 가격 소스 결정
        start = _get_date(90)
        end   = _get_date(1)
        with st.spinner("가격 데이터 소스 확인 중..."):
            price_info = get_price_source_for_scan(tickers, start, end)

        for log in price_info["log"]:
            if log.startswith("[INFO]"):
                st.success(log)
            elif log.startswith("[WARN]"):
                st.warning(log)
            elif log.startswith("[ERROR]"):
                st.error(log)

        price_src   = price_info["source"]
        price_cache_date = price_info.get("cache_date")
        cache_date  = ticker_cache_date or price_cache_date

        total = len(tickers)
        st.info(f"{market_choice} {total}개 종목 스캔 시작 (threshold: {full_threshold}점 이상)")

        progress_bar = st.progress(0)
        status_text  = st.empty()

        def update_progress(current, total, name):
            progress_bar.progress(current / total)
            status_text.text(f"({current} / {total}) {name}")

        try:
            df, processed_count, fail_count = run_scan(
                tickers=tickers,
                progress_callback=update_progress,
                score_threshold=full_threshold,
                price_source_info=price_info,
            )
            progress_bar.progress(1.0)
            status_text.text("스캔 완료")

            ratio = round(len(df) / processed_count * 100, 1) if processed_count > 0 else 0
            suggested, tune_msg = calc_suggested_threshold(full_threshold, ratio)

            st.session_state.update({
                "result":              df,
                "scan_total":          total,
                "scan_processed":      processed_count,
                "scan_fail":           fail_count,
                "scan_mode":           "full",
                "scan_fallback":       is_fallback,
                "scan_market":         market_choice,
                "scan_ticker_src":     ticker_src,
                "scan_price_src":      price_src,
                "scan_cache_date":     cache_date,
                "scan_ratio":          ratio,
                "scan_threshold_used": full_threshold,
                "suggested_threshold": suggested,
                "tune_msg":            tune_msg,
            })
        except Exception as e:
            st.error(f"스캔 중 오류: {e}")

    else:
        # ── 빠른 스캔 ─────────────────────────────────────────
        with st.spinner("분석 중..."):
            try:
                today_str = _get_date(1)
                tickers = None

                if _KRX_AVAILABLE:
                    try:
                        krx_market = "KOSDAQ" if market_choice == "KOSDAQ" else "KOSPI"
                        top_df = krx_stock.get_market_trading_volume_by_ticker(today_str, market=krx_market)
                        if top_df is not None and not top_df.empty:
                            tickers = [str(t).zfill(6) for t in
                                       top_df.sort_values("거래량", ascending=False).head(top_n).index.tolist()]
                    except Exception:
                        pass

                if not tickers:
                    fb = FALLBACK_KOSDAQ if market_choice == "KOSDAQ" else FALLBACK_KOSPI
                    tickers = fb

                # ALL 빠른 스캔: 두 시장 거래량 상위 병합
                if market_choice == "ALL" and _KRX_AVAILABLE:
                    try:
                        tickers_k = []
                        tickers_q = []
                        for mkt, store in [("KOSPI", tickers_k), ("KOSDAQ", tickers_q)]:
                            td = krx_stock.get_market_trading_volume_by_ticker(today_str, market=mkt)
                            if td is not None and not td.empty:
                                store += [str(t).zfill(6) for t in
                                          td.sort_values("거래량", ascending=False).head(top_n // 2).index.tolist()]
                        merged = list(dict.fromkeys(tickers_k + tickers_q))
                        merged.sort()
                        tickers = merged if merged else tickers
                    except Exception:
                        pass

                start = _get_date(90)
                end   = _get_date(1)
                price_info = get_price_source_for_scan(tickers, start, end)

                df, processed_count, fail_count = run_scan(
                    tickers=tickers,
                    score_threshold=FAST_SCORE_THRESHOLD,
                    price_source_info=price_info,
                )
                ratio = round(len(df) / processed_count * 100, 1) if processed_count > 0 else 0

                st.session_state.update({
                    "result":              df,
                    "scan_total":          len(tickers),
                    "scan_processed":      processed_count,
                    "scan_fail":           fail_count,
                    "scan_mode":           "fast",
                    "scan_fallback":       False,
                    "scan_market":         market_choice,
                    "scan_ticker_src":     "PYKRX",
                    "scan_price_src":      price_info["source"],
                    "scan_cache_date":     price_info.get("cache_date"),
                    "scan_ratio":          ratio,
                    "scan_threshold_used": FAST_SCORE_THRESHOLD,
                    "tune_msg":            None,
                    "suggested_threshold": DEFAULT_FULL_THRESHOLD,
                })
            except Exception as e:
                st.error(f"오류 발생: {e}")


# ── 결과 표시 ──────────────────────────────────────────────────
if "result" in st.session_state:
    df          = st.session_state["result"]
    mode        = st.session_state.get("scan_mode", "fast")
    scanned     = st.session_state.get("scan_total", 0)
    processed   = st.session_state.get("scan_processed", 0)
    fail        = st.session_state.get("scan_fail", 0)
    is_fb       = st.session_state.get("scan_fallback", False)
    ratio       = st.session_state.get("scan_ratio", 0)
    used_thresh = st.session_state.get("scan_threshold_used", 0)
    tune_msg    = st.session_state.get("tune_msg", None)
    found       = len(df)
    market      = st.session_state.get("scan_market", "KOSPI")
    ticker_src  = st.session_state.get("scan_ticker_src", "-")
    price_src   = st.session_state.get("scan_price_src", "-")
    cache_date  = st.session_state.get("scan_cache_date")
    mode_label  = "전체 스캔" if mode == "full" else "빠른 스캔"
    status      = _source_status(ticker_src, price_src, is_fb, cache_date)

    st.divider()

    # 컨트롤룸 정보 배지
    _render_badge_row(market, ticker_src, price_src, mode_label, cache_date, status)

    st.divider()

    # 4개 지표
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 대상",     f"{scanned}개")
    col2.metric("정상 처리",   f"{processed}개")
    col3.metric("실패/스킵",   f"{fail}개")
    col4.metric("박스권 후보", f"{found}개  ({ratio}%)")

    # threshold 튜닝 권고
    if tune_msg:
        suggested = st.session_state.get("suggested_threshold", used_thresh)
        if ratio > RATIO_HIGH:
            st.warning(f"⚠️ {tune_msg}\n\n사이드바 슬라이더를 **{suggested}**으로 조정 후 재스캔하세요.")
        else:
            st.info(f"ℹ️ {tune_msg}\n\n사이드바 슬라이더를 **{suggested}**으로 조정 후 재스캔하세요.")

    if df.empty:
        st.warning(f"조건에 맞는 종목 없음 — threshold {used_thresh}점 이상 박스권 종목이 없습니다.")
    else:
        if mode == "full" and not is_fb:
            st.success(f"{market} {scanned}개 중 **{found}개** 박스권 후보 ({ratio}%) | threshold {used_thresh}점")
        elif is_fb:
            st.error(f"🚨 제한 모드 — {scanned}개 중 **{found}개** 후보 ({ratio}%) | threshold {used_thresh}점")
        else:
            st.success(f"후보군 {scanned}개 분석 완료 — **{found}개** 결과 ({ratio}%)")

        # 🔽 정렬: 1순위 점수(내림차순), 2순위 거래량(내림차순)
        df = df.sort_values(by=["점수", "거래량"], ascending=[False, False]).reset_index(drop=True)

        # ── TOP 5 요약 카드 ──────────────────────────────────
        top_n_cards = min(len(df), 5)
        top_df = df.head(top_n_cards)

        st.subheader("🏆 지금 봐야 할 종목 TOP 5")
        st.caption("점수 + 거래량 기준 — 즉시 판단용")

        # 🔹 모바일 대응: 2열 그리드
        cols_per_row = 2 if top_n_cards >= 4 else top_n_cards
        rows = [top_df[i:i+cols_per_row] for i in range(0, len(top_df), cols_per_row)]

        for row_df in rows:
            cols = st.columns(len(row_df))
            for col, (_, row) in zip(cols, row_df.iterrows()):
                with col:
                    with st.container(border=True):

                        # 종목명 + 코드
                        st.markdown(f"**{row['종목명']}**")
                        st.caption(f"`{row['종목코드']}`")

                        # 점수 + 강도 이모지
                        score = row['점수']
                        if score >= 90:
                            emoji = "🔥"
                        elif score >= 80:
                            emoji = "⚡"
                        else:
                            emoji = "🟡"

                        st.metric(label="점수", value=f"{score}점 {emoji}")

                        # 돌파 신호
                        st.write(f"{row['돌파신호']}")

                        # 이유 (강조)
                        st.write(f"🧠 {row['이유']}")

        st.divider()

        # ── 박스권 후보 테이블 ────────────────────────────────
        st.subheader("박스권 후보")
        st.caption(f"threshold {used_thresh}점 이상 | 높을수록 박스권 가능성 높음 (100점 최고) | 시장: {market}")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()

        name_list     = df["종목명"].tolist()
        selected_name = st.selectbox("차트 볼 종목 선택", ["선택하세요"] + name_list)

        if selected_name != "선택하세요":
            ticker_code = df.loc[df["종목명"] == selected_name, "종목코드"].values[0]
            score       = df.loc[df["종목명"] == selected_name, "점수"].values[0]
            st.subheader(f"{selected_name} — 최근 90일 종가")
            st.caption(f"박스권 점수: {score}점")
            with st.spinner("차트 불러오는 중..."):
                chart_df = get_price_chart(ticker_code)
            if chart_df is None or chart_df.empty:
                st.warning("차트 데이터를 불러올 수 없습니다.")
            else:
                st.line_chart(chart_df)
