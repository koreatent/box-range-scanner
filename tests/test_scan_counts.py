"""v11.7 집계 회귀 테스트: 정상 + 실패 == 대상 수 (1종목 1회 집계)"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import box_range_scanner as brs


def _df(n):
    return pd.DataFrame({
        "종가": np.linspace(100, 110, n), "고가": np.linspace(101, 111, n),
        "저가": np.linspace(99, 109, n), "거래량": [1000] * n,
    })


def _scan(data, threshold=0):
    brs._get_ticker_name = lambda t: t  # 네트워크 호출 차단
    fetch = lambda t, s, e: data[t]() if callable(data[t]) else data[t]
    return brs.run_scan(tickers=list(data), score_threshold=threshold,
                        price_source_info={"fetch_fn": fetch})


def test_short_history_counted_once():
    # 신규상장처럼 20거래일 이하 → analyze_box 예외 경로
    df, ok, fail, done = _scan({"A": _df(60), "B": _df(10), "C": _df(3)})
    assert (ok, fail) == (1, 2)
    assert ok + fail == len(done) == 3


def test_mixed_failures_sum_to_total():
    def boom():
        raise RuntimeError("api")
    data = {"A": _df(60), "B": None, "C": pd.DataFrame(),
            "D": pd.DataFrame({"x": [1]}), "E": boom, "F": _df(5), "G": _df(40)}
    df, ok, fail, done = _scan(data)
    assert ok + fail == len(data)
    assert ok == 2


def test_threshold_does_not_change_counts():
    data = {"A": _df(60), "B": _df(10)}
    _, ok0, f0, _ = _scan(data, threshold=0)
    _, ok1, f1, _ = _scan(data, threshold=101)
    assert (ok0, f0) == (ok1, f1) == (1, 1)
