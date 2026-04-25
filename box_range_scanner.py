"""
box_range_scanner.py — v10.0
박스권 탐지 모듈

변경 이력:
  v10.0 - run_scan() 처리 완료 ticker 추적 추가
          partial_callback에 processed_tickers 전달
  v9.0 - 입력 레이어 리팩토링 (멈추지 않는 스캐너)
         get_market_tickers(market) : FDR → 캐시 → fallback 순 ticker 확보
         get_price_source_for_scan() : FDR-KRX → FDR-NAVER → yfinance → 캐시 순
         소스 혼합 금지 — 한 스캔 내 단일 source 고정
         시장 선택: KOSPI / KOSDAQ / ALL 지원
  v8.2 - analyze_box 점수 체계 연속화 (0~100 연속)
  v8.1 - get_market_ticker_list() 제거
"""

from datetime import datetime, timedelta
import pandas as pd

try:
    import FinanceDataReader as fdr
    _FDR_AVAILABLE = True
except ImportError:
    _FDR_AVAILABLE = False

try:
    from pykrx import stock as krx_stock
    _KRX_AVAILABLE = True
except ImportError:
    _KRX_AVAILABLE = False

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


# ── fallback 종목 (시장별 분리) ────────────────────────────────
FALLBACK_KOSPI = [
    "005930","000660","207940","005380","035420",
    "000270","068270","105560","055550","086790",
    "096770","003490","051910","017670","015760",
    "034220","090430","066570","030200","032830",
    "011170","003550","009150","006400","010950",
]

FALLBACK_KOSDAQ = [
    "247540","091990","196170","263750","357780",
    "086900","145020","112040","039030","041510",
    "122870","095340","041920","078600","240810",
    "064760","950130","083790","067160","214150",
    "253450","237690","096530","035900","036810",
]

# ── 세션 캐시 ──────────────────────────────────────────────────
_ticker_cache = {}
_price_cache  = {}


def _today_str():
    return datetime.today().strftime("%Y%m%d")

def _get_date(days=0):
    return (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════
# 1. TICKER 확보
# ══════════════════════════════════════════════════════════════

def _combine_sources(s1, s2):
    priority = {"FDR": 0, "CACHE": 1, "FALLBACK": 2}
    return s1 if priority.get(s1, 9) >= priority.get(s2, 9) else s2


def get_market_tickers(market="KOSPI"):
    """
    시장별 ticker 확보 (FDR → 캐시 → fallback)
    반환: {"tickers":[], "source":"FDR|CACHE|FALLBACK", "cache_date":str|None, "log":[]}
    """
    logs = []
    market_up = market.upper()

    if market_up == "ALL":
        kospi  = get_market_tickers("KOSPI")
        kosdaq = get_market_tickers("KOSDAQ")
        merged = list(dict.fromkeys(kospi["tickers"] + kosdaq["tickers"]))
        merged.sort()
        source = _combine_sources(kospi["source"], kosdaq["source"])
        logs = kospi["log"] + kosdaq["log"]
        logs.append(f"[INFO] ALL 병합 완료: {len(merged)}개 (종목코드 순 정렬)")
        return {
            "tickers":    merged,
            "source":     source,
            "cache_date": kospi.get("cache_date") or kosdaq.get("cache_date"),
            "log":        logs,
        }

    # 1순위: FDR
    if _FDR_AVAILABLE:
        try:
            df = fdr.StockListing(market_up)
            if df is not None and not df.empty:
                code_col = next((c for c in ["Code","Symbol","종목코드"] if c in df.columns), None)
                if code_col:
                    tickers = sorted([str(x).zfill(6) for x in df[code_col].tolist() if str(x).strip()])
                    _ticker_cache[market_up] = {"date": _today_str(), "tickers": tickers}
                    logs.append(f"[INFO] ticker source: FDR-{market_up} success ({len(tickers)}개)")
                    return {"tickers": tickers, "source": "FDR", "cache_date": None, "log": logs}
        except Exception as e:
            logs.append(f"[WARN] FDR StockListing({market_up}) failed: {e}")

    # 2순위: 캐시
    cached = _ticker_cache.get(market_up)
    if cached and cached.get("tickers"):
        logs.append(f"[WARN] ticker cache used: {cached['date']} ({len(cached['tickers'])}개)")
        return {"tickers": cached["tickers"], "source": "CACHE", "cache_date": cached["date"], "log": logs}

    # 3순위: fallback
    fb = FALLBACK_KOSPI if market_up == "KOSPI" else FALLBACK_KOSDAQ
    logs.append(f"[ERROR] fallback mode enabled ({market_up}): {len(fb)}개")
    return {"tickers": fb, "source": "FALLBACK", "cache_date": None, "log": logs}


# ══════════════════════════════════════════════════════════════
# 2. 가격 데이터 소스 결정
# ══════════════════════════════════════════════════════════════

def _normalize_df(df):
    rename_map = {
        "Open":"시가","High":"고가","Low":"저가","Close":"종가","Volume":"거래량",
        "open":"시가","high":"고가","low":"저가","close":"종가","volume":"거래량",
    }
    return df.rename(columns=rename_map)


def _fetch_fdr_krx(ticker, start, end):
    if not _FDR_AVAILABLE:
        return None
    try:
        df = fdr.DataReader(ticker, start=start, end=end, data_source="krx")
        if df is None or df.empty:
            return None
        df = _normalize_df(df)
        _price_cache[ticker] = {"date": _today_str(), "df": df}
        return df
    except Exception:
        return None


def _fetch_fdr_naver(ticker, start, end):
    if not _FDR_AVAILABLE:
        return None
    try:
        df = fdr.DataReader(ticker, start=start, end=end, data_source="naver")
        if df is None or df.empty:
            return None
        df = _normalize_df(df)
        _price_cache[ticker] = {"date": _today_str(), "df": df}
        return df
    except Exception:
        return None


def _fetch_yfinance(ticker, start, end):
    if not _YF_AVAILABLE:
        return None
    try:
        symbol = ticker + ".KS"
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = _normalize_df(df)
        _price_cache[ticker] = {"date": _today_str(), "df": df}
        return df
    except Exception:
        return None


def _fetch_from_cache(ticker, start, end):
    cached = _price_cache.get(ticker)
    return cached.get("df") if cached else None


def _fetch_pykrx(ticker, start, end):
    if not _KRX_AVAILABLE:
        return None
    try:
        df = krx_stock.get_market_ohlcv_by_date(start, end, ticker)
        if df is None or df.empty:
            return None
        _price_cache[ticker] = {"date": _today_str(), "df": df}
        return df
    except Exception:
        return None


def get_price_source_for_scan(tickers, start, end):
    """
    스캔 전체에 사용할 가격 소스 결정 (probe 종목으로 순차 시도)
    우선순위: FDR-KRX → FDR-NAVER → YFINANCE → CACHE → PYKRX
    반환: {"source":str, "fetch_fn":callable, "cache_date":str|None, "log":[]}
    """
    logs  = []
    probe = tickers[0] if tickers else None

    if _FDR_AVAILABLE and probe:
        try:
            df = fdr.DataReader(probe, start=start, end=end, data_source="krx")
            if df is not None and not df.empty and len(df) >= 5:
                logs.append("[INFO] price source: FDR-KRX success")
                return {"source":"FDR-KRX","fetch_fn":_fetch_fdr_krx,"cache_date":None,"log":logs}
        except Exception as e:
            logs.append(f"[WARN] price source FDR-KRX failed: {e}")

    if _FDR_AVAILABLE and probe:
        try:
            df = fdr.DataReader(probe, start=start, end=end, data_source="naver")
            if df is not None and not df.empty and len(df) >= 5:
                logs.append("[INFO] price source: FDR-NAVER success")
                return {"source":"FDR-NAVER","fetch_fn":_fetch_fdr_naver,"cache_date":None,"log":logs}
        except Exception as e:
            logs.append(f"[WARN] price source FDR-NAVER failed: {e}")

    if _YF_AVAILABLE and probe:
        try:
            symbol = probe + ".KS"
            df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
            if df is not None and not df.empty and len(df) >= 5:
                logs.append("[INFO] price source: YFINANCE success")
                return {"source":"YFINANCE","fetch_fn":_fetch_yfinance,"cache_date":None,"log":logs}
        except Exception as e:
            logs.append(f"[WARN] price source YFINANCE failed: {e}")

    cached = _price_cache.get(probe) if probe else None
    if cached:
        cache_date = cached.get("date","unknown")
        logs.append(f"[WARN] price source: CACHE used ({cache_date})")
        return {"source":"CACHE","fetch_fn":_fetch_from_cache,"cache_date":cache_date,"log":logs}

    logs.append("[ERROR] fallback mode enabled — using pykrx")
    return {"source":"PYKRX","fetch_fn":_fetch_pykrx,"cache_date":None,"log":logs}


# ══════════════════════════════════════════════════════════════
# 3. 분석 로직 (v8.2 유지)
# ══════════════════════════════════════════════════════════════

def _linear_penalty(value, ideal, danger, max_penalty=33.3):
    if value <= ideal:
        return 0.0
    if value >= danger:
        return max_penalty
    return (value - ideal) / (danger - ideal) * max_penalty


def analyze_box(df):
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

    reasons = []
    if penalty_range >= 22:   reasons.append("변동폭 과다")
    elif penalty_range >= 11: reasons.append("변동폭 주의")
    if penalty_vol >= 22:     reasons.append("변동성 과다")
    elif penalty_vol >= 11:   reasons.append("변동성 주의")
    if penalty_slope >= 22:   reasons.append("추세 강함")
    elif penalty_slope >= 11: reasons.append("추세 주의")
    if not reasons:           reasons.append("박스권 안정")

    return score, ", ".join(reasons)


def get_breakout_signal(df):
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


def _get_ticker_name(ticker):
    if _KRX_AVAILABLE:
        try:
            return krx_stock.get_market_ticker_name(ticker)
        except Exception:
            pass
    return ticker


# ══════════════════════════════════════════════════════════════
# 4. 메인 스캔
# ══════════════════════════════════════════════════════════════

def run_scan(tickers=None, progress_callback=None, score_threshold=0, price_source_info=None,
             partial_callback=None, save_every=10):
    """
    박스권 스캔 실행
    반환: (DataFrame, processed_count, fail_count, processed_tickers)

    processed_tickers:
        threshold 통과 여부와 무관하게, 실제 스캔 처리가 끝난 ticker 목록
        실패/skip ticker도 포함하여 resume 시 중복 재스캔을 방지

    partial_callback(partial_rows, processed_count, fail_count, current_index, total, processed_tickers)
        → save_every 종목 처리마다 호출 (중간 저장용)
    """
    if tickers is None:
        tickers = FALLBACK_KOSPI

    start = _get_date(120)
    end   = _get_date(1)

    if price_source_info is None:
        price_source_info = get_price_source_for_scan(tickers, start, end)

    fetch_fn        = price_source_info["fetch_fn"]
    total           = len(tickers)
    results         = []
    fail_count      = 0
    processed_count = 0
    processed_tickers = []

    for i, t in enumerate(tickers):
        name = _get_ticker_name(t)
        if progress_callback:
            progress_callback(i + 1, total, name)
        try:
            df = fetch_fn(t, start, end)
            if df is None or df.empty:
                fail_count += 1
            elif not {"종가","고가","저가"}.issubset(set(df.columns)):
                fail_count += 1
            else:
                processed_count += 1
                score, reason = analyze_box(df)
                if score >= score_threshold:
                    signal = get_breakout_signal(df)
                    volume = int(df['거래량'].iloc[-1]) if '거래량' in df.columns else 0
                    results.append({
                        "종목코드": t, "종목명": name, "점수": score,
                        "거래량": volume, "이유": reason, "돌파신호": signal,
                    })
        except Exception:
            fail_count += 1
        processed_tickers.append(t)

        # ── 중간 저장 (save_every 단위) ──────────────────────
        if partial_callback and ((i + 1) % save_every == 0 or (i + 1) == total):
            partial_callback(
                partial_rows=results.copy(),
                processed_count=processed_count,
                fail_count=fail_count,
                current_index=i + 1,
                total=total,
                processed_tickers=processed_tickers.copy(),
            )

    if not results:
        return (
            pd.DataFrame(columns=["종목코드","종목명","점수","거래량","이유","돌파신호"]),
            processed_count,
            fail_count,
            processed_tickers,
        )

    result_df = pd.DataFrame(results).sort_values("점수", ascending=False).reset_index(drop=True)
    return result_df, processed_count, fail_count, processed_tickers
