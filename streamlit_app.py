"""
streamlit_app.py — v8.2
박스권 스캐너 컨트롤룸

변경 이력:
  v8.2 - fallback 15종목 → 50종목으로 확장
         get_kospi_tickers() 반환형 변경에 맞게 언패킹 수정 (tickers, count, date)
         ohlcv_by_ticker 결과 ticker 수 로그 표시 추가
         전체 스캔 threshold: 60 → 70 (연속 점수 체계 기준)
"""

import streamlit as st
from datetime import datetime, timedelta
from pykrx import stock
from box_range_scanner import run_scan, get_kospi_tickers

# -- 상수 --------------------------------------------------
FALLBACK_TICKERS = [
    # 시가총액 상위 + 업종 분산 50종목
    "005930", "000660", "207940", "005380", "035420",
    "000270", "068270", "105560", "055550", "086790",
    "096770", "003490", "051910", "017670", "015760",
    "034220", "090430", "066570", "030200", "032830",
    "011170", "003550", "009150", "006400", "010950",
    "028260", "018260", "009830", "010130", "000100",
    "001040", "004020", "002790", "010140", "005490",
    "011200", "000720", "004170", "007070", "002380",
    "024110", "000810", "005830", "004000", "001450",
    "023530", "016360", "011790", "003010", "002030",
]
FAST_SCORE_THRESHOLD = 0   # 빠른 스캔: 전부 표시
FULL_SCORE_THRESHOLD = 70  # 전체 스캔: 70점 이상 (연속 점수 체계 기준)
# ----------------------------------------------------------


def get_price_chart(ticker_code):
    end   = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=90)).strftime("%Y%m%d")
    df = stock.get_market_ohlcv_by_date(start, end, ticker_code)
    if df is None or df.empty:
        return None
    return df[["종가"]]


# -- 화면 구성 ---------------------------------------------
st.title("박스권 스캐너 컨트롤룸")

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
    top_n = st.sidebar.slider("후보군 종목 수", min_value=15, max_value=100, value=50, step=5)
    st.caption("⚡ 빠른 스캔 모드 — 거래량 상위 후보군을 분석합니다.")

btn_label = "🔍 코스피 전체 스캔 시작" if is_full_scan else "⚡ 빠른 스캔 시작"

if st.button(btn_label, use_container_width=True):

    if is_full_scan:
        # ── 전체 스캔 ──────────────────────────────────────
        with st.spinner("코스피 전종목 목록 수집 중..."):
            tickers, ticker_count, ref_date = get_kospi_tickers()

        is_fallback = False

        if not tickers:
            st.warning("코스피 전체 조회 실패 — 제한 모드로 전환됩니다.")
            tickers     = FALLBACK_TICKERS
            is_fallback = True
        else:
            # 로그: ohlcv_by_ticker 결과 확인
            st.success(f"✅ ohlcv_by_ticker 정상 — {ref_date} 기준 {ticker_count}개 종목 확인")

        total = len(tickers)

        if is_fallback:
            st.info(f"제한 모드: {total}개 기본 종목으로 스캔합니다.")
        else:
            st.info(f"코스피 {total}개 종목 스캔 시작 (threshold: {FULL_SCORE_THRESHOLD}점 이상)")

        progress_bar = st.progress(0)
        status_text  = st.empty()

        def update_progress(current, total, name):
            progress_bar.progress(current / total)
            status_text.text(f"({current} / {total}) {name}")

        try:
            df, processed_count, fail_count = run_scan(
                tickers=tickers,
                progress_callback=update_progress,
                score_threshold=FULL_SCORE_THRESHOLD,
            )
            progress_bar.progress(1.0)
            status_text.text("스캔 완료")

            # 후보 비율 로그
            ratio = round(len(df) / processed_count * 100, 1) if processed_count > 0 else 0
            st.caption(f"📊 후보 비율: {processed_count}개 처리 중 {len(df)}개 통과 ({ratio}%)")

            st.session_state.update({
                "result":          df,
                "scan_total":      total,
                "scan_processed":  processed_count,
                "scan_fail":       fail_count,
                "scan_mode":       "full",
                "scan_fallback":   is_fallback,
                "scan_ticker_cnt": ticker_count,
                "scan_ref_date":   ref_date,
            })
        except Exception as e:
            st.error(f"스캔 중 오류: {e}")

    else:
        # ── 빠른 스캔 ─────────────────────────────────────
        with st.spinner("분석 중..."):
            try:
                today_str = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
                try:
                    top_df = stock.get_market_trading_volume_by_ticker(today_str, market="KOSPI")
                    if top_df is not None and not top_df.empty:
                        tickers = [str(t).zfill(6) for t in
                                   top_df.sort_values("거래량", ascending=False).head(top_n).index.tolist()]
                    else:
                        tickers = FALLBACK_TICKERS
                except Exception:
                    tickers = FALLBACK_TICKERS

                df, processed_count, fail_count = run_scan(
                    tickers=tickers,
                    score_threshold=FAST_SCORE_THRESHOLD,
                )
                st.session_state.update({
                    "result":         df,
                    "scan_total":     len(tickers),
                    "scan_processed": processed_count,
                    "scan_fail":      fail_count,
                    "scan_mode":      "fast",
                    "scan_fallback":  False,
                })
            except Exception as e:
                st.error(f"오류 발생: {e}")

# -- 결과 표시 ----------------------------------------------
if "result" in st.session_state:
    df        = st.session_state["result"]
    mode      = st.session_state.get("scan_mode", "fast")
    scanned   = st.session_state.get("scan_total", 0)
    processed = st.session_state.get("scan_processed", 0)
    fail      = st.session_state.get("scan_fail", 0)
    is_fb     = st.session_state.get("scan_fallback", False)
    found     = len(df)

    st.divider()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 대상",    f"{scanned}개")
    col2.metric("정상 처리",  f"{processed}개")
    col3.metric("실패/스킵",  f"{fail}개")
    col4.metric("박스권 후보", f"{found}개")

    if df.empty:
        if mode == "full":
            st.warning(f"조건에 맞는 종목 없음 — {FULL_SCORE_THRESHOLD}점 이상 박스권 종목이 없습니다.")
        else:
            st.warning("결과 없음 — 평일 오후 4시 이후 다시 실행해보세요.")
    else:
        ratio = round(found / processed * 100, 1) if processed > 0 else 0
        if mode == "full" and not is_fb:
            st.success(f"코스피 {scanned}개 중 **{found}개** 박스권 후보 발견 ({ratio}%)")
        elif is_fb:
            st.info(f"제한 모드 — {scanned}개 종목 중 **{found}개** 후보 ({ratio}%)")
        else:
            st.success(f"후보군 {scanned}개 분석 완료 — **{found}개** 결과 ({ratio}%)")

        st.subheader("박스권 후보")
        st.caption(f"점수 {FULL_SCORE_THRESHOLD if mode == 'full' else 0}점 이상 | 높을수록 박스권 가능성 높음 (100점 최고)")
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
