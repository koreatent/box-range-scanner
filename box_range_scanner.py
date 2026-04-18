"""
box_range_scanner.py v7.0
- 시가총액 상위 N개 자동 종목 생성
- 돌파신호 + 이유 + 거래량 포함
"""

from pykrx import stock
import pandas as pd
from datetime import datetime, timedelta


def get_date(days=0):
    return (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")


def get_top_marketcap_tickers(top_n=100):
    """
    코스피 + 코스닥 시가총액 상위 top_n 종목 반환
    캐싱: 당일 1회만 조회
    """
    today = datetime.today().strftime("%Y%m%d")

    try:
        # 코스피
        kospi = stock.get_market_cap(today, market="KOSPI")
        # 코스닥
        kosdaq = stock.get_market_cap(today, market="KOSDAQ")

        combined = pd.concat([kospi, kosdaq])
        combined = combined[combined["시가총액"] > 0]
        combined = combined.sort_values("시가총액", ascending=False)

        tickers = combined.index.tolist()[:top_n]
        return tickers

    except Exception:
        # 조회 실패 시 전일 날짜로 재시도
        try:
            prev = (datetime.today() - timedelta(days=3)).strftime("%Y%m%d")
            kospi  = stock.get_market_cap(prev, market="KOSPI")
            kosdaq = stock.get_market_cap(prev, market="KOSDAQ")
            combined = pd.concat([kospi, kosdaq])
            combined = combined[combined["시가총액"] > 0]
            combined = combined.sort_values("시가총액", ascending=False)
            return combined.index.tolist()[:top_n]
        except Exception:
            # 최후 fallback: 기본 15종목
            return [
                "005930", "000660", "035420", "051910", "068270",
                "105560", "055550", "017670", "015760", "034220",
                "096770", "003490", "000270", "090430", "086790"
            ]


def detect_breakout_signal(df):
    close = df['종가']
    current_price = close.iloc[-1]
    upper_band = close.max()
    lower_band = close.min()

    has_volume = '거래량' in df.columns and df['거래량'].sum() > 0
    avg_vol_5  = df['거래량'].tail(5).mean()  if has_volume else 0
    avg_vol_20 = df['거래량'].tail(20).mean() if has_volume else 0

    near_upper  = current_price >= upper_band * 0.95
    close_upper = current_price >= upper_band * 0.90
    near_lower  = current_price <= lower_band * 1.05
    vol_up      = has_volume and avg_vol_20 > 0 and avg_vol_5 > avg_vol_20 * 1.2

    if near_upper and vol_up:
        return "돌파 임박"
    elif near_upper or close_upper:
        return "관찰 필요"
    elif near_lower:
        return "이탈 주의"
    else:
        return "신호 약함"


def analyze_box(df):
    close = df['종가']
    high  = df['고가']
    low   = df['저가']

    range_width = (high.max() - low.min()) / close.mean()
    volatility  = close.pct_change().std()
    ma          = close.rolling(20).mean()
    ma_slope    = abs(ma.diff().mean())

    has_volume = '거래량' in df.columns and df['거래량'].sum() > 0
    vol_cv     = df['거래량'].pct_change().std() if has_volume else 1.0
    avg_vol_5  = int(df['거래량'].tail(5).mean())  if has_volume else 0
    vol_recent = df['거래량'].tail(5).mean()        if has_volume else 0
    vol_prev   = df['거래량'].iloc[:-5].mean()      if has_volume and len(df) > 5 else vol_recent

    score   = 100
    reasons = []

    if range_width <= 0.15:
        reasons.append("변동폭 좁음")
    elif range_width > 0.3:
        score -= 30

    if volatility <= 0.02:
        reasons.append("변동성 낮음")
    elif volatility > 0.05:
        score -= 30

    if ma_slope <= close.mean() * 0.005:
        reasons.append("추세 없음")
    elif ma_slope > close.mean() * 0.02:
        score -= 30

    if vol_cv <= 0.5:
        reasons.append("거래량 안정")

    upper = close.quantile(0.95)
    lower = close.quantile(0.05)
    if (close >= upper).sum() >= 2 and (close <= lower).sum() >= 2:
        reasons.append("지지/저항 반복")

    if has_volume and vol_prev > 0 and vol_recent > vol_prev * 1.5:
        reasons.append("거래량 증가로 돌파 가능성")
        score -= 10

    if len(reasons) >= 3:
        reason_str = "박스권 패턴 복합"
    elif len(reasons) >= 1:
        reason_str = " + ".join(reasons)
    else:
        reason_str = "패턴 미약"

    signal = detect_breakout_signal(df)
    return score, reason_str, avg_vol_5, signal


def run_scan(tickers, progress_callback=None):
    """
    박스권 스캔 실행
    progress_callback(current, total, name): 진행 상황 콜백 (옵션)
    """
    start = get_date(90)
    end   = get_date(1)
    total = len(tickers)

    results = []
    for i, t in enumerate(tickers):
        name = ""
        try:
            name = stock.get_market_ticker_name(t) or t
            if progress_callback:
                progress_callback(i + 1, total, name)

            df = stock.get_market_ohlcv_by_date(start, end, t)
            if df is None or df.empty:
                continue

            score, reason, avg_vol, signal = analyze_box(df)
            results.append([t, name, score, avg_vol, reason, signal])
        except Exception:
            continue

    if not results:
        return pd.DataFrame(columns=["종목코드", "종목명", "점수", "거래량", "이유", "돌파신호"])

    result_df = pd.DataFrame(results, columns=["종목코드", "종목명", "점수", "거래량", "이유", "돌파신호"])
    result_df = result_df.sort_values("점수", ascending=False).reset_index(drop=True)
    return result_df
