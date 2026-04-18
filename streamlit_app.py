"""
streamlit_app.py — v8.0
박스권 스캐너 컨트롤룸

변경 이력:
  v8.0 - 스캔 모드 선택 추가 (빠른 스캔 / 코스피 전체 스캔)
         전체 스캔 시 진행바 + 결과 요약 표시
         기존 빠른 스캔 모드 완전 보존
"""

import streamlit as st
from datetime import datetime, timedelta
from pykrx import stock
from box_range_scanner import run_scan, get_kospi_tickers

# -- 상수 --------------------------------------------------
FALLBACK_TICKERS = [
    "005930", "000660", "035420", "051910", "068270",
    "105560", "055550", "017670", "015760", "034220",
    "096770", "003490", "000270", "090430", "086790",
]
FAST_SCORE_THRESHOLD  = 0   # 빠른 스캔: 전부 표시
FULL_SCORE_THRESHOLD  = 60  # 전체 스캔: 60점 이상만
# ----------------------------------------------------------


def get_price_chart(ticker_code):
    """종목코드 기준 90일 종가 데이터 반환"""
    end   = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=90)).strftime("%Y%m%d")
    df = stock.get_market_ohlcv_by_date(start, end, ticker_code)
    if df is None or df.empty:
        return None
    return df[["종가"]]


# -- 화면 구성 ---------------------------------------------
st.title("박스권 스캐너 컨트롤룸")

# 사이드바 — 스캔 모드 선택
st.sidebar.header("스캔 설정")
scan_mode = st.sidebar.radio(
    "스캔 모드",
    ["⚡ 빠른 스캔 (후보군)", "🔍 전체 스캔 (코스피 전종목)"],
    index=0,
)

is_full_scan = scan_mode.startswith("🔍")

if is_full_scan:
    st.sidebar.info("코스피 전종목을 스캔합니다.\n약 900~1,000개 종목 대상.\n소요 시간: 수 분 예상.")
    st.caption("🔍 전체 스캔 모드 — 코스피 전종목에서 박스권 종목을 탐색합니다.")
else:
    top_n = st.sidebar.slider("후보군 종목 수", min_value=15, max_value=100, value=15, step=5)
    st.caption("⚡ 빠른 스캔 모드 — 거래량 상위 후보군을 분석합니다.")

# 스캔 버튼
btn_label = "🔍 코스피 전체 스캔 시작" if is_full_scan else "⚡ 빠른 스캔 시작"
if st.button(btn_label, use_container_width=True):

    if is_full_scan:
        # ── 전체 스캔 ──────────────────────────────────────
        with st.spinner("코스피 전종목 목록 수집 중..."):
            tickers = get_kospi_tickers()

        if not tickers:
            st.error("코스피 종목 목록을 가져올 수 없습니다. 잠시 후 다시 시도해주세요.")
        else:
            total = len(tickers)
            st.info(f"코스피 {total}개 종목 스캔 시작")

            progress_bar  = st.progress(0)
            status_text   = st.empty()

            def update_progress(current, total, name):
                ratio = current / total
                progress_bar.progress(ratio)
                status_text.text(f"({current} / {total}) {name}")

            try:
                df = run_scan(
                    tickers=tickers,
                    progress_callback=update_progress,
                    score_threshold=FULL_SCORE_THRESHOLD,
                )
                progress_bar.progress(1.0)
                status_text.text(f"스캔 완료 — {total}개 종목 검색")
                st.session_state["result"]       = df
                st.session_state["scan_total"]   = total
                st.session_state["scan_mode"]    = "full"
            except Exception as e:
                st.error(f"스캔 중 오류: {e}")

    else:
        # ── 빠른 스캔 ─────────────────────────────────────
        with st.spinner("분석 중..."):
            try:
                # 거래량 상위 top_n 가져오기 시도, 실패 시 fallback
                today_str = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
                try:
                    top_df = stock.get_market_trading_volume_by_ticker(today_str, market="KOSPI")
                    if top_df is not None and not top_df.empty:
                        tickers = top_df.sort_values("거래량", ascending=False).head(top_n).index.tolist()
                    else:
                        tickers = FALLBACK_TICKERS
                except Exception:
                    tickers = FALLBACK_TICKERS

                df = run_scan(tickers=tickers, score_threshold=FAST_SCORE_THRESHOLD)
                st.session_state["result"]     = df
                st.session_state["scan_total"] = len(tickers)
                st.session_state["scan_mode"]  = "fast"
            except Exception as e:
                st.error(f"오류 발생: {e}")

# -- 결과 표시 ----------------------------------------------
if "result" in st.session_state:
    df        = st.session_state["result"]
    mode      = st.session_state.get("scan_mode", "fast")
    scanned   = st.session_state.get("scan_total", 0)

    st.divider()

    if df.empty:
        if mode == "full":
            st.warning("조건에 맞는 종목 없음 — 박스권 기준(60점 이상)을 만족하는 종목이 없습니다.")
        else:
            st.warning("결과 없음 — 평일 오후 4시 이후 다시 실행해보세요.")
    else:
        # 결과 요약
        found = len(df)
        if mode == "full":
            st.success(f"코스피 {scanned}개 중 **{found}개** 박스권 후보 발견 (60점 이상)")
        else:
            st.success(f"후보군 {scanned}개 중 **{found}개** 분석 완료")

        st.subheader("박스권 후보")
        st.caption("점수가 높을수록 박스권 가능성이 높습니다. (100점 최고)")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.divider()

        # 종목 선택 + 차트
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
