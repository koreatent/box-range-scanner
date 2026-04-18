"""
streamlit_app.py — v8.3
박스권 스캐너 컨트롤룸

변경 이력:
  v8.3 - threshold 사이드바 슬라이더 추가 (실시간 조정)
         스캔 후 후보 비율 자동 계산 → threshold 튜닝 권고 배너 표시
         20% 초과 → +5 권고 / 10% 미만 → -5 권고
         제한 모드(fallback)에서도 동일 튜닝 로직 적용
         후보 비율 % 전 모드 표시
"""

import streamlit as st
from datetime import datetime, timedelta
from pykrx import stock
from box_range_scanner import run_scan, get_kospi_tickers

# -- 상수 --------------------------------------------------
FALLBACK_TICKERS = [
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
FAST_SCORE_THRESHOLD    = 0
DEFAULT_FULL_THRESHOLD  = 70
RATIO_HIGH              = 20.0   # 이 % 초과 시 threshold +5 권고
RATIO_LOW               = 10.0   # 이 % 미만 시 threshold -5 권고
# ----------------------------------------------------------


def get_price_chart(ticker_code):
    end   = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=90)).strftime("%Y%m%d")
    df = stock.get_market_ohlcv_by_date(start, end, ticker_code)
    if df is None or df.empty:
        return None
    return df[["종가"]]


def calc_suggested_threshold(current_threshold, ratio):
    """
    후보 비율 기준 threshold 튜닝 권고값 계산
    20% 초과 → +5 / 10% 미만 → -5 / 그 사이 → 현재값 유지
    """
    if ratio > RATIO_HIGH:
        return current_threshold + 5, f"후보 비율 {ratio}% > {RATIO_HIGH}% — threshold {current_threshold} → {current_threshold + 5} 권고"
    elif ratio < RATIO_LOW:
        return current_threshold - 5, f"후보 비율 {ratio}% < {RATIO_LOW}% — threshold {current_threshold} → {current_threshold - 5} 권고"
    else:
        return current_threshold, None   # 조정 불필요


# -- 화면 구성 ---------------------------------------------
st.title("박스권 스캐너 컨트롤룸")

# -- 사이드바 ----------------------------------------------
st.sidebar.header("스캔 설정")

scan_mode = st.sidebar.radio(
    "스캔 모드",
    ["⚡ 빠른 스캔 (후보군)", "🔍 전체 스캔 (코스피 전종목)"],
    index=0,
)
is_full_scan = scan_mode.startswith("🔍")

if is_full_scan:
    st.sidebar.info("코스피 전종목을 스캔합니다.\n약 900~1,000개 종목 대상.\n소요 시간: 수 분 예상.")

    # threshold 슬라이더 (전체 스캔 전용)
    # 이전 스캔에서 권고값이 있으면 기본값으로 반영
    default_thresh = st.session_state.get("suggested_threshold", DEFAULT_FULL_THRESHOLD)
    full_threshold = st.sidebar.slider(
        "박스권 점수 threshold",
        min_value=40, max_value=95, value=default_thresh, step=5,
        help="이 점수 이상인 종목만 결과에 표시됩니다. 후보 비율에 따라 자동 권고값이 제시됩니다."
    )
    st.sidebar.caption(f"현재: {full_threshold}점 이상 | 목표 비율: {RATIO_LOW}~{RATIO_HIGH}%")
    st.caption(f"🔍 전체 스캔 모드 — threshold: {full_threshold}점 이상")
else:
    top_n = st.sidebar.slider("후보군 종목 수", min_value=15, max_value=100, value=50, step=5)
    st.caption("⚡ 빠른 스캔 모드 — 거래량 상위 후보군을 분석합니다.")
    full_threshold = DEFAULT_FULL_THRESHOLD  # 빠른 스캔은 사용 안 함

# -- 스캔 버튼 ---------------------------------------------
btn_label = "🔍 코스피 전체 스캔 시작" if is_full_scan else "⚡ 빠른 스캔 시작"

if st.button(btn_label, use_container_width=True):

    if is_full_scan:
        # ── 전체 스캔 ──────────────────────────────────────
        with st.spinner("코스피 전종목 목록 수집 중..."):
            tickers, ticker_count, ref_date = get_kospi_tickers()

        is_fallback = False

        if not tickers:
            st.warning("코스피 전체 조회 실패 — 제한 모드로 전환됩니다.")
            tickers      = FALLBACK_TICKERS
            is_fallback  = True
            ticker_count = len(FALLBACK_TICKERS)
            ref_date     = None
        else:
            st.success(f"✅ ohlcv_by_ticker 정상 — {ref_date} 기준 {ticker_count}개 종목 확인")

        total = len(tickers)

        if is_fallback:
            st.info(f"제한 모드: {total}개 기본 종목으로 스캔합니다. (threshold: {full_threshold}점)")
        else:
            st.info(f"코스피 {total}개 종목 스캔 시작 (threshold: {full_threshold}점 이상)")

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
            )
            progress_bar.progress(1.0)
            status_text.text("스캔 완료")

            # 후보 비율 계산 + 튜닝 권고
            ratio = round(len(df) / processed_count * 100, 1) if processed_count > 0 else 0
            suggested, tune_msg = calc_suggested_threshold(full_threshold, ratio)

            st.session_state.update({
                "result":               df,
                "scan_total":           total,
                "scan_processed":       processed_count,
                "scan_fail":            fail_count,
                "scan_mode":            "full",
                "scan_fallback":        is_fallback,
                "scan_ticker_cnt":      ticker_count,
                "scan_ref_date":        ref_date,
                "scan_ratio":           ratio,
                "scan_threshold_used":  full_threshold,
                "suggested_threshold":  suggested,
                "tune_msg":             tune_msg,
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
                ratio = round(len(df) / processed_count * 100, 1) if processed_count > 0 else 0

                st.session_state.update({
                    "result":              df,
                    "scan_total":          len(tickers),
                    "scan_processed":      processed_count,
                    "scan_fail":           fail_count,
                    "scan_mode":           "fast",
                    "scan_fallback":       False,
                    "scan_ratio":          ratio,
                    "scan_threshold_used": FAST_SCORE_THRESHOLD,
                    "tune_msg":            None,
                })
            except Exception as e:
                st.error(f"오류 발생: {e}")

# -- 결과 표시 ----------------------------------------------
if "result" in st.session_state:
    df         = st.session_state["result"]
    mode       = st.session_state.get("scan_mode", "fast")
    scanned    = st.session_state.get("scan_total", 0)
    processed  = st.session_state.get("scan_processed", 0)
    fail       = st.session_state.get("scan_fail", 0)
    is_fb      = st.session_state.get("scan_fallback", False)
    ratio      = st.session_state.get("scan_ratio", 0)
    used_thresh = st.session_state.get("scan_threshold_used", 0)
    tune_msg   = st.session_state.get("tune_msg", None)
    found      = len(df)

    st.divider()

    # 4개 지표
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 대상",    f"{scanned}개")
    col2.metric("정상 처리",  f"{processed}개")
    col3.metric("실패/스킵",  f"{fail}개")
    col4.metric("박스권 후보", f"{found}개  ({ratio}%)")

    # threshold 튜닝 권고 배너
    if tune_msg:
        suggested = st.session_state.get("suggested_threshold", used_thresh)
        if ratio > RATIO_HIGH:
            st.warning(f"⚠️ {tune_msg}\n\n사이드바 슬라이더를 **{suggested}**으로 조정 후 재스캔하세요.")
        else:
            st.info(f"ℹ️ {tune_msg}\n\n사이드바 슬라이더를 **{suggested}**으로 조정 후 재스캔하세요.")

    if df.empty:
        if mode in ("full", ) or is_fb:
            st.warning(f"조건에 맞는 종목 없음 — {used_thresh}점 이상 박스권 종목이 없습니다.")
        else:
            st.warning("결과 없음 — 평일 오후 4시 이후 다시 실행해보세요.")
    else:
        if mode == "full" and not is_fb:
            st.success(f"코스피 {scanned}개 중 **{found}개** 박스권 후보 ({ratio}%) | threshold {used_thresh}점")
        elif is_fb:
            st.info(f"제한 모드 — {scanned}개 중 **{found}개** 후보 ({ratio}%) | threshold {used_thresh}점")
        else:
            st.success(f"후보군 {scanned}개 분석 완료 — **{found}개** 결과 ({ratio}%)")

        st.subheader("박스권 후보")
        st.caption(f"threshold {used_thresh}점 이상 | 높을수록 박스권 가능성 높음 (100점 최고)")
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
