"""
box_range_scanner.py — v8.2
박스권 탐지 모듈

변경 이력:
  v8.2 - analyze_box 점수 체계 연속화 (40/70/100 3단계 → 0~100 연속)
         각 조건을 선형 보간으로 부분 감점 적용
         get_nearest_business_date 로그 반환 추가 (ticker 수 확인용)
  v8.1 - get_market_ticker_list() 제거 → get_market_ohlcv_by_ticker() 기반 전환
         processed_count 분리, fallback 제한 모드 추가
"""

from pykrx import stock
import pandas as pd
from datetime import datetime, timedelta


def get_date(days=0):
    return (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")


def _linear_penalty(value, ideal, danger, max_penalty=33.3):
    """value를 ideal~danger 구간에서 0~max_penalty로 선형 변환"""
    if value <= ideal:
        return 0.0
    if value >= danger:
        return max_penalty
    return (value - ideal) / (danger - ideal) * max_penalty


def analyze_box(df):
    """
    박스권 점수 계산 — 연속 점수 체계 (0~100)

    v8.2 변경:
      기존: 조건 충족 시 -30점 고정 (40/70/100 3단계만 존재)
      변경: 각 조건을 선형 보간으로 부분 감점 → 연속 분포

    감점 기준 (각 최대 33.3점):
      range_width  : 이상 ≤0.10, 위험 ≥0.40
      volatility   : 이상 ≤0.010, 위험 ≥0.040
      ma_slope_rel : 이상 ≤0.003, 위험 ≥0.015
    """
    close      = df['종가']
    mean_price = close.mean()

    range_width  = (df['고가'].max() - df['저가'].min()) / mean_price
    volatility   = close.pct_change().std()
    ma           = close.rolling(20).mean()
    ma_slope_rel = abs(ma.diff().mean()) / mean_price if mean_price > 0 else 0

    penalty_range = _linear_penalty(range_width,  ideal=0.10, danger=0.40)
    penalty_vol   = _linear_penalty(volatility,   ideal=0.010, danger=0.040)
    penalty_slope = _linear_penalty(ma_slope_rel, ideal=0.003, danger=0.015)

    score = max(0, round(100 - penalty_range - penalty_vol - penalty_slope))

    # 이유 문구 (각 조건별 심각도 반영)
    reasons = []
    if penalty_range >= 22:
        reasons.append("변동폭 과다")
    elif penalty_range >= 11:
        reasons.append("변동폭 주의")

    if penalty_vol >= 22:
        reasons.append("변동성 과다")
    elif penalty_vol >= 11:
        reasons.append("변동성 주의")

    if penalty_slope >= 22:
        reasons.append("추세 강함")
    elif penalty_slope >= 11:
        reasons.append("추세 주의")

    if not reasons:
        reasons.append("박스권 안정")

    return score, ", ".join(reasons)


def get_breakout_signal(df):
    """돌파 신호 계산"""
    if df is None or df.empty or len(df) < 2:
        return "⚪ 데이터부족"
    close    = df['종가']
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


def get_nearest_business_date(max_days=7):
    """
    최근 영업일 날짜 + 코스피 전체 OHLCV DataFrame 반환
    반환: (date_str, df, ticker_count)
      ticker_count: 조회된 종목 수 (로그 확인용)
    """
    for i in range(1, max_days + 1):
        d = (datetime.today() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv_by_ticker(d, market="KOSPI")
            if df is not None and not df.empty:
                return d, df, len(df)
        except Exception:
            continue
    return None, None, 0


def get_kospi_tickers():
    """
    코스피 전체 ticker 리스트 반환
    get_market_ohlcv_by_ticker() index에서 추출
    반환: (tickers, ticker_count, date_str)
    """
    date, df, count = get_nearest_business_date()

    if df is None or df.empty:
        return [], 0, None

    tickers = [str(idx).zfill(6) for idx in df.index.tolist()]
    return tickers, count, date


def run_scan(tickers=None, progress_callback=None, score_threshold=0):
    """
    박스권 스캔 실행

    tickers           : 종목코드 리스트. None이면 fallback 사용.
    progress_callback : (current, total, name) 호출 함수.
    score_threshold   : 이 점수 이상인 종목만 결과에 포함.
    반환: (DataFrame, processed_count, fail_count)
    """
    if tickers is None:
        tickers = [
            "005930", "000660", "035420", "051910", "068270",
            "105560", "055550", "017670", "015760", "034220",
            "096770", "003490", "000270", "090430", "086790"
        ]

    start           = get_date(90)
    end             = get_date(1)
    total           = len(tickers)
    results         = []
    fail_count      = 0
    processed_count = 0

    for i, t in enumerate(tickers):
        name = t
        try:
            name = stock.get_market_ticker_name(t)
        except Exception:
            pass

        if progress_callback:
            progress_callback(i + 1, total, name)

        try:
            df = stock.get_market_ohlcv_by_date(start, end, t)
            if df is None or df.empty:
                fail_count += 1
                continue

            processed_count += 1

            score, reason = analyze_box(df)

            if score < score_threshold:
                continue

            signal = get_breakout_signal(df)
            volume = int(df['거래량'].iloc[-1]) if '거래량' in df.columns else 0

            results.append({
                "종목코드": t,
                "종목명":   name,
                "점수":     score,
                "거래량":   volume,
                "이유":     reason,
                "돌파신호": signal,
            })
        except Exception:
            fail_count += 1
            continue

    if not results:
        empty = pd.DataFrame(columns=["종목코드", "종목명", "점수", "거래량", "이유", "돌파신호"])
        return empty, processed_count, fail_count

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("점수", ascending=False).reset_index(drop=True)
    return result_df, processed_count, fail_count
