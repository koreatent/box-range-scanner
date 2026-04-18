"""
box_range_scanner.py — v8.0
박스권 탐지 모듈

변경 이력:
  v8.0 - 코스피 전체 ticker 수집 함수 추가
         run_scan에 progress_callback 파라미터 추가 (기존 호환 유지)
         박스권 조건 만족 종목만 반환 (score_threshold 적용)
"""

from pykrx import stock
import pandas as pd
from datetime import datetime, timedelta


def get_date(days=0):
    return (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")


def analyze_box(df):
    """박스권 점수 계산 (기존 로직 보존)"""
    close = df['종가']
    range_width = (df['고가'].max() - df['저가'].min()) / close.mean()
    volatility = close.pct_change().std()
    ma = close.rolling(20).mean()
    ma_slope = abs(ma.diff().mean())

    score = 100
    reasons = []

    if range_width > 0.3:
        score -= 30
        reasons.append("변동폭 과다")
    if volatility > 0.05:
        score -= 30
        reasons.append("변동성 과다")
    if ma_slope > close.mean() * 0.02:
        score -= 30
        reasons.append("추세 존재")

    if not reasons:
        reasons.append("박스권 안정")

    return score, ", ".join(reasons)


def get_breakout_signal(df):
    """돌파 신호 계산"""
    if df is None or df.empty or len(df) < 2:
        return "⚪ 데이터부족"
    close = df['종가']
    box_high = df['고가'].max()
    box_low  = df['저가'].min()
    last     = close.iloc[-1]
    prev     = close.iloc[-2]

    if last > box_high * 0.98 and last > prev:
        return "🟢 상단돌파임박"
    elif last < box_low * 1.02 and last < prev:
        return "🔴 하단이탈임박"
    elif last > close.mean():
        return "🟡 박스권상단"
    else:
        return "⚪ 박스권중립"


def get_kospi_tickers():
    """코스피 전체 ticker 리스트 반환 (최근 영업일 기준)"""
    today = datetime.today()
    for offset in range(7):
        date_str = (today - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            tickers = stock.get_market_ticker_list(date_str, market="KOSPI")
            if tickers and len(tickers) > 0:
                return list(tickers)
        except Exception:
            continue
    return []


def run_scan(tickers=None, progress_callback=None, score_threshold=0):
    """
    박스권 스캔 실행

    tickers           : 종목코드 리스트. None이면 fallback 15종목 사용.
    progress_callback : (current, total, name) 호출 함수. 없으면 무시.
    score_threshold   : 이 점수 이상인 종목만 결과에 포함.
                        빠른 스캔 → 0 (전부), 전체 스캔 → 60
    반환값: DataFrame (종목코드, 종목명, 점수, 거래량, 이유, 돌파신호)
    """
    if tickers is None:
        tickers = [
            "005930", "000660", "035420", "051910", "068270",
            "105560", "055550", "017670", "015760", "034220",
            "096770", "003490", "000270", "090430", "086790"
        ]

    start = get_date(90)
    end   = get_date(1)
    total = len(tickers)

    results = []
    for i, t in enumerate(tickers):
        # 종목명 먼저 시도 (진행바에 표시)
        name = ""
        try:
            name = stock.get_market_ticker_name(t)
        except Exception:
            name = t

        if progress_callback:
            progress_callback(i + 1, total, name)

        try:
            df = stock.get_market_ohlcv_by_date(start, end, t)
            if df is None or df.empty:
                continue

            score, reason = analyze_box(df)

            if score < score_threshold:
                continue

            signal = get_breakout_signal(df)
            volume = int(df['거래량'].iloc[-1]) if '거래량' in df.columns else 0

            results.append({
                "종목코드": t,
                "종목명": name,
                "점수": score,
                "거래량": volume,
                "이유": reason,
                "돌파신호": signal,
            })
        except Exception:
            continue

    if not results:
        return pd.DataFrame(columns=["종목코드", "종목명", "점수", "거래량", "이유", "돌파신호"])

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("점수", ascending=False).reset_index(drop=True)
    return result_df
