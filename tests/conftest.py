"""
테스트 공통 설정 (v11.7)
- 네트워크 차단: KRX/가격 소스/종목명 조회를 가짜 데이터로 교체
- AppTest는 앱 파일을 임시 폴더에 복사해 실행 (ticker_cache.csv가 저장소에 생기지 않도록)
"""
import os
import shutil
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import box_range_scanner as brs  # noqa: E402
import modules.krx_api as krx_api  # noqa: E402
from modules import scan_guard  # noqa: E402


def make_price_df(ticker, days=80):
    """종목코드로 결정되는 고정 가격 데이터. 일부는 데이터 부족(분석 실패) 종목."""
    seed = int(ticker)
    if seed % 17 == 0:
        days = 10  # 신규상장처럼 20거래일 미만 → 분석 단계 예외 → 실패 1회
    rng = np.random.default_rng(seed)
    amp = 0.02 + (seed % 9) * 0.03
    base = 10000 + seed
    t = np.arange(days)
    close = base * (1 + amp * np.sin(t / 4.0) + rng.normal(0, 0.004, days))
    return pd.DataFrame(
        {
            "종가": close,
            "고가": close * 1.01,
            "저가": close * 0.99,
            "거래량": rng.integers(1_000, 100_000, days),
        }
    )


class FakeMarket:
    """가짜 시장. fetch 호출 횟수를 세고, 지정한 호출에서 끼어들기(on_call)를 실행할 수 있다."""

    def __init__(self, n=250, missing_every=23):
        self.tickers = [f"{i:06d}" for i in range(1, n + 1)]
        self.missing_every = missing_every
        self.calls = 0
        self.on_call = None  # callable(call_no) — 테스트에서 재실행 시뮬레이션용

    def fetch(self, ticker, start, end):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        if int(ticker) % self.missing_every == 0:
            return None  # 수집 실패
        return make_price_df(ticker)

    def daily_trade(self, bas_dd=None, market="KOSPI", **kwargs):
        return pd.DataFrame(
            {"종목코드": self.tickers, "종목명": [f"종목{t}" for t in self.tickers], "basDd": bas_dd or "20261127"}
        )


@pytest.fixture
def fake_market(monkeypatch):
    market = FakeMarket()

    def price_source(tickers, start, end, market_name="KOSPI", **kwargs):
        return {"source": "TEST", "fetch_fn": market.fetch, "cache_date": None, "log": ["[INFO] price source: TEST"]}

    monkeypatch.setattr(brs, "get_price_source_for_scan", lambda t, s, e, market="KOSPI": price_source(t, s, e))
    monkeypatch.setattr(brs, "_get_ticker_name", lambda t: t)
    monkeypatch.setattr(krx_api, "fetch_krx_daily_trade", market.daily_trade)
    monkeypatch.setattr(krx_api, "fetch_krx_price_history", lambda *a, **k: None)
    with scan_guard._LOCK:
        scan_guard._LIVE.clear()
    return market


@pytest.fixture
def app_path(tmp_path):
    dst = tmp_path / "streamlit_app.py"
    shutil.copy(os.path.join(ROOT, "streamlit_app.py"), dst)
    return str(dst)
