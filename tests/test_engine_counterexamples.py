"""
클로팀장 1단계 리뷰(관제실 7절)의 추가 반례 8개를 저장소 안 테스트로 옮긴 것.
※ 클로 원본은 저장소 밖에서 실행됐으므로, 관제실 설명을 기준으로 꼬드가 재구성함.
   클로 재검증 시 원본 반례와 대조 필요.
대상: box_range_scanner.run_scan (점수 계산식은 건드리지 않음)
"""
import numpy as np
import pandas as pd
import pytest

import box_range_scanner as brs
from conftest import make_price_df


def _df(n, amp=0.05):
    t = np.arange(n)
    close = 1000 * (1 + amp * np.sin(t / 4.0))
    return pd.DataFrame({"종가": close, "고가": close * 1.01, "저가": close * 0.99, "거래량": [1000] * n})


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(brs, "_get_ticker_name", lambda t: t)


def _scan(data, **kw):
    fetch = lambda t, s, e: data[t]() if callable(data[t]) else data[t]  # noqa: E731
    return brs.run_scan(tickers=list(data), price_source_info={"fetch_fn": fetch}, **kw)


def _boom():
    raise RuntimeError("fetch error")


MIXED = {
    "000001": _df(60), "000002": None, "000003": pd.DataFrame(), "000004": pd.DataFrame({"x": [1]}),
    "000005": _boom, "000006": _df(5), "000007": _df(40), "000008": _df(80, 0.3),
}


def test_ce1_every_ticker_recorded_exactly_once():
    _, ok, fail, done = _scan(MIXED)
    assert sorted(done) == sorted(MIXED) and len(done) == len(set(done))
    assert ok + fail == len(MIXED)


def test_ce2_breakout_signal_exception_counts_fail_once(monkeypatch):
    def bad_signal(df):
        raise ValueError("signal")
    monkeypatch.setattr(brs, "get_breakout_signal", bad_signal)
    df, ok, fail, done = _scan({"000001": _df(60), "000002": _df(60)})
    assert (ok, fail) == (0, 2) and df.empty


def test_ce3_volume_conversion_exception_counts_fail_once():
    bad = _df(60)
    bad.loc[bad.index[-1], "거래량"] = np.nan  # int(NaN) 예외
    _, ok, fail, _ = _scan({"000001": bad, "000002": _df(60)})
    assert (ok, fail) == (1, 1)


def test_ce4_partial_saves_match_position():
    seen = []

    def cb(partial_rows, processed_count, fail_count, current_index, total, processed_tickers):
        seen.append((processed_count + fail_count, current_index, len(processed_tickers)))

    _scan(MIXED, partial_callback=cb, save_every=3)
    assert seen and all(a == b == c for a, b, c in seen)
    assert seen[-1][1] == len(MIXED)


def _split_run(tickers, cuts, fetch):
    rows, ok, fail, done = [], 0, 0, []
    bounds = [0] + list(cuts) + [len(tickers)]
    for a, b in zip(bounds, bounds[1:]):
        df, o, f, d = brs.run_scan(tickers=tickers[a:b], price_source_info={"fetch_fn": fetch})
        rows.append(df); ok += o; fail += f; done += d
    merged = pd.concat(rows, ignore_index=True).sort_values("종목코드").reset_index(drop=True)
    return merged, ok, fail, done


@pytest.mark.parametrize("cuts", [(5, 10, 15), (1, 7, 19), (13,)])
def test_ce5_split_runs_equal_single_run(cuts):
    tickers = [f"{i:06d}" for i in range(1, 25)]
    fetch = lambda t, s, e: None if int(t) % 11 == 0 else make_price_df(t)  # noqa: E731
    single, ok1, f1, d1 = _split_run(tickers, (), fetch)
    split, ok2, f2, d2 = _split_run(tickers, cuts, fetch)
    assert (ok1, f1) == (ok2, f2) and sorted(d1) == sorted(d2)
    pd.testing.assert_frame_equal(single[["종목코드", "점수"]], split[["종목코드", "점수"]])


def test_ce6_threshold_boundary_inclusive():
    df_all, *_ = _scan({"000001": _df(60)})
    score = int(df_all["점수"].iloc[0])
    df_eq, *_ = _scan({"000001": _df(60)}, score_threshold=score)
    df_gt, ok, fail, _ = _scan({"000001": _df(60)}, score_threshold=score + 1)
    assert len(df_eq) == 1 and df_gt.empty


def test_ce7_rows_below_threshold_still_counted_as_processed():
    _, ok, fail, done = _scan({"000001": _df(60), "000002": _df(60)}, score_threshold=101)
    assert (ok, fail, len(done)) == (2, 0, 2)


def test_ce8_fetch_exception_counts_fail_once_and_recorded():
    _, ok, fail, done = _scan({"000001": _boom, "000002": _df(60)})
    assert (ok, fail) == (1, 1) and done == ["000001", "000002"]
