"""
streamlit_app.py - 박스권 스캐너 컨트롤룸 v7.2
"""
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
from datetime import datetime, timedelta
from pykrx import stock

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

try:
    from modules.box_range_scanner import run_scan, get_top_volume_tickers
except ModuleNotFoundError:
    from box_range_scanner import run_scan, get_top_volume_tickers

SIGNAL_EMOJI = {
    "돌파 임박": "🟢",
    "관찰 필요": "🟡",
    "신호 약함": "⚪",
    "이탈 주의": "🔴",
}

def signal_label(signal):
    return f"{SIGNAL_EMOJI.get(signal, '')} {signal}"

def get_ohlcv(ticker_code: str):
    end   = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=90)).strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv_by_date(start, end, ticker_code)
        return None if (df is None or df.empty) else df
    except Exception:
        return None

st.set_page_config(page_title="박스권 스캐너", page_icon="📦", layout="wide")
st.title("📦 박스권 스캐너 컨트롤룸 v7.2")
st.caption("거래량 상위 종목을 자동으로 스캔합니다.")

with st.sidebar:
    st.header("⚙️ 설정")
    top_n = st.slider("스캔 종목 수 (거래량 상위)", min_value=20, max_value=200, value=100, step=10)
    extra_raw = st.text_area(
        "추가 종목코드 입력 (줄바꿈 또는 쉼표로 구분)",
        placeholder="예)\n323410\n207940",
        height=100,
    )
    st.divider()
    min_score = st.slider("최소 점수 필터", min_value=0, max_value=100, value=0, step=10)
    signal_filter = st.multiselect(
        "돌파신호 필터",
        options=["돌파 임박", "관찰 필요", "신호 약함", "이탈 주의"],
        default=["돌파 임박", "관찰 필요", "신호 약함", "이탈 주의"],
    )
    st.divider()
    st.caption("코스피 + 코스닥 거래량 기준 자동 정렬")

extra_tickers = []
if extra_raw.strip():
    raw_list = extra_raw.replace(",", "\n").splitlines()
    extra_tickers = [t.strip() for t in raw_list if t.strip().isdigit()]

col1, col2 = st.columns([3, 1])
with col1:
    run_btn = st.button("🔍 박스권 스캔 시작", use_container_width=True)
with col2:
    clear_btn = st.button("🗑️ 결과 초기화", use_container_width=True)

if clear_btn:
    st.session_state.pop("result", None)
    st.rerun()

if run_btn:
    with st.spinner("거래량 상위 종목 목록 가져오는 중..."):
        auto_tickers, ticker_status = get_top_volume_tickers(top_n)

    # 상태 메시지
    if ticker_status.startswith("ok:"):
        date_used = ticker_status.split(":")[1]
        st.success(f"✅ 거래량 데이터 정상 조회 ({date_used} 기준) — {len(auto_tickers)}개 종목")
    else:
        st.error("❌ 거래량 데이터 조회 실패 → 기본 15종목으로 대체 (평일 장 마감 후 재시도 권장)")

    all_tickers = list(dict.fromkeys(auto_tickers + extra_tickers))
    total = len(all_tickers)

    st.info(f"📋 스캔 대상: {total}개 종목 (거래량 자동 {len(auto_tickers)}개 + 직접 입력 {len(extra_tickers)}개)")

    progress_bar = st.progress(0)
    status_text  = st.empty()

    def on_progress(current, total, name):
        progress_bar.progress(current / total)
        status_text.text(f"분석 중... ({current}/{total}) {name}")

    try:
        result_df = run_scan(all_tickers, progress_callback=on_progress)
        progress_bar.progress(1.0)
        status_text.text(f"✅ 스캔 완료 — {len(result_df)}개 결과")
        st.session_state["result"] = result_df
        st.session_state["scanned_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        st.error(f"오류 발생: {e}")

if "result" in st.session_state:
    df_all     = st.session_state["result"]
    scanned_at = st.session_state.get("scanned_at", "-")
    st.divider()

    if df_all.empty:
        st.warning("결과 없음 — 평일 오후 4시 이후 다시 실행해보세요.")
    else:
        df = df_all[df_all["점수"] >= min_score]
        if signal_filter:
            df = df[df["돌파신호"].isin(signal_filter)]
        df = df.reset_index(drop=True)

        df_display = df.copy()
        df_display["거래량"]  = df_display["거래량"].apply(lambda x: f"{x:,}" if x > 0 else "데이터 없음")
        df_display["돌파신호"] = df_display["돌파신호"].apply(signal_label)

        h1, h2 = st.columns([3, 1])
        with h1:
            st.subheader("📊 박스권 후보")
            st.caption(f"스캔 시각: {scanned_at}  |  표시 종목: {len(df)}개")
        with h2:
            csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label="⬇️ CSV 다운로드",
                data=csv_bytes,
                file_name=f"box_scan_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        if df.empty:
            st.info("조건에 맞는 종목이 없습니다. 필터를 조정해보세요.")
        else:
            st.dataframe(df_display, use_container_width=True, hide_index=True)
            st.divider()

            name_list     = df["종목명"].tolist()
            selected_name = st.selectbox("📈 차트 볼 종목 선택", ["선택하세요"] + name_list)

            if selected_name != "선택하세요":
                row         = df.loc[df["종목명"] == selected_name].iloc[0]
                ticker_code = row["종목코드"]
                score       = row["점수"]
                reason      = row["이유"]
                avg_vol     = row["거래량"]
                signal      = row["돌파신호"]
                avg_vol_str = f"{avg_vol:,}" if avg_vol > 0 else "데이터 없음"

                st.subheader(f"{selected_name} — 최근 90일 종가")
                st.caption(
                    f"종목코드: {ticker_code}  |  박스권 점수: {score}점  |  "
                    f"최근 5일 평균 거래량: {avg_vol_str}  |  이유: {reason}  |  "
                    f"돌파신호: {SIGNAL_EMOJI.get(signal,'')} {signal}"
                )

                with st.spinner("차트 불러오는 중..."):
                    ohlcv = get_ohlcv(ticker_code)

                if ohlcv is None or ohlcv.empty:
                    st.warning("차트 데이터를 불러올 수 없습니다.")
                else:
                    has_vol = '거래량' in ohlcv.columns and ohlcv['거래량'].sum() > 0
                    fig, axes = plt.subplots(
                        2, 1, figsize=(12, 7), sharex=True,
                        gridspec_kw={"height_ratios": [3, 1]}
                    )
                    ax1, ax2 = axes

                    ax1.plot(ohlcv.index, ohlcv["종가"], color="#4C9BE8", linewidth=1.5)
                    ax1.set_title(f"{selected_name} 최근 90일 종가", fontsize=13)
                    ax1.set_ylabel("종가 (원)")
                    ax1.grid(True, alpha=0.3)
                    ax1.axhline(y=ohlcv["종가"].max(), color="#FF6B6B", linestyle="--",
                                linewidth=1, alpha=0.7, label="상단")
                    ax1.axhline(y=ohlcv["종가"].min(), color="#51CF66", linestyle="--",
                                linewidth=1, alpha=0.7, label="하단")
                    ax1.legend(fontsize=9)

                    if has_vol:
                        ax2.bar(ohlcv.index, ohlcv["거래량"], color="#A0C4FF", width=0.8)
                        ax2.set_title("최근 90일 거래량", fontsize=11)
                        ax2.set_ylabel("거래량")
                    else:
                        ax2.text(0.5, 0.5, "거래량 데이터 없음",
                                 ha='center', va='center', transform=ax2.transAxes)

                    ax2.grid(True, alpha=0.3)
                    plt.xticks(rotation=45)
                    plt.tight_layout()
                    st.pyplot(fig)
                    plt.close(fig)
