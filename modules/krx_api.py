from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests


API_URLS = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
}

API_IDS = {
    "KOSPI": "stk_bydd_trd",
    "KOSDAQ": "ksq_bydd_trd",
}

FIELD_MAP = {
    "ISU_CD": "종목코드",
    "ISU_SRT_CD": "종목코드",
    "isuCd": "종목코드",
    "isuSrtCd": "종목코드",
    "ISU_NM": "종목명",
    "ISU_ABBRV": "종목명",
    "isuNm": "종목명",
    "isuAbbrv": "종목명",
    "TDD_OPNPRC": "시가",
    "TDD_HGPRC": "고가",
    "TDD_LWPRC": "저가",
    "TDD_CLSPRC": "종가",
    "ACC_TRDVOL": "거래량",
}

NUMERIC_COLUMNS = ["시가", "고가", "저가", "종가", "거래량"]
_DAILY_TRADE_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


class KRXAuthError(RuntimeError):
    pass


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    _load_env_file(Path.cwd() / ".env")


def _normalize_market(market: str) -> str:
    market_up = str(market or "").upper()
    if market_up not in API_URLS:
        raise ValueError(f"지원하지 않는 KRX 시장입니다: {market}")
    return market_up


def _get_api_key(market: str | None = None) -> str:
    _load_env()
    market_up = str(market or "").upper()
    market_key = os.getenv(f"KRX_API_KEY_{market_up}") if market_up else None
    api_key = market_key or os.getenv("KRX_API_KEY")
    if not api_key:
        key_name = f"KRX_API_KEY_{market_up} 또는 KRX_API_KEY" if market_up else "KRX_API_KEY"
        raise RuntimeError(f"{key_name} 환경변수가 설정되지 않았습니다.")
    return api_key


def get_krx_endpoint_info(market: str) -> dict[str, str]:
    market_up = _normalize_market(market)
    return {
        "market": market_up,
        "api_id": API_IDS[market_up],
        "url": API_URLS[market_up],
    }


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("OutBlock_1", "outBlock_1", "output", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _is_auth_failed(status_code: int, payload: Any) -> bool:
    if status_code == 401:
        return True
    if isinstance(payload, dict):
        return str(payload.get("respCode", "")) == "401"
    return False


def _request_daily_trade(bas_dd: str, market: str) -> pd.DataFrame:
    market_up = _normalize_market(market)
    endpoint = get_krx_endpoint_info(market_up)

    response = requests.get(
        endpoint["url"],
        params={"basDd": bas_dd},
        headers={"AUTH_KEY": _get_api_key(market_up)},
        timeout=20,
    )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"KRX 응답이 JSON이 아닙니다. status_code={response.status_code}") from exc

    if _is_auth_failed(response.status_code, payload):
        raise KRXAuthError(
            "KRX 인증 실패: "
            f"market={market_up}, api_id={endpoint['api_id']}, url={endpoint['url']}, "
            "AUTH_KEY 또는 해당 API 활용 신청 승인 상태를 확인하세요."
        )

    if response.status_code >= 400:
        raise RuntimeError(f"KRX HTTP 오류: status_code={response.status_code}, body={response.text[:300]}")

    rows = _extract_rows(payload)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["시장"] = market_up
    df["basDd"] = bas_dd
    return df


def _candidate_dates(bas_dd: str, max_retry_days: int) -> list[str]:
    start = datetime.strptime(bas_dd, "%Y%m%d")
    return [(start - timedelta(days=offset)).strftime("%Y%m%d") for offset in range(max_retry_days + 1)]


def fetch_krx_daily_trade(
    bas_dd: str,
    market: str = "KOSPI",
    retry_previous: bool = True,
    max_retry_days: int = 10,
    verbose: bool = True,
) -> pd.DataFrame:
    dates = _candidate_dates(bas_dd, max_retry_days if retry_previous else 0)
    df = pd.DataFrame()
    used_bas_dd = bas_dd

    for candidate_bas_dd in dates:
        df = _request_daily_trade(candidate_bas_dd, market)
        if not df.empty:
            used_bas_dd = candidate_bas_dd
            if verbose and candidate_bas_dd != bas_dd:
                print(f"[INFO] KRX data found: requested={bas_dd}, used={candidate_bas_dd}, rows={len(df)}")
            break
        if verbose:
            print(f"[WARN] KRX data empty: basDd={candidate_bas_dd}, market={market.upper()}")

    if df.empty:
        if verbose:
            print(f"[WARN] KRX data not found within {max_retry_days} days: requested={bas_dd}, market={market.upper()}")
        return pd.DataFrame(columns=["종목코드", "종목명", "시장", "basDd", *NUMERIC_COLUMNS])

    df = df.rename(columns=FIELD_MAP)
    required = ["종목코드", "종목명", "시장", "basDd", *NUMERIC_COLUMNS]
    for col in required:
        if col not in df.columns:
            df[col] = pd.NA

    df["종목코드"] = df["종목코드"].astype(str).str.strip().str.zfill(6)
    df["종목명"] = df["종목명"].astype(str)
    df["시장"] = df["시장"].astype(str).str.upper()
    df["basDd"] = used_bas_dd

    for col in NUMERIC_COLUMNS:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
            .replace("-", "0")
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[df["종목코드"].str.len() == 6]
    df = df.drop_duplicates("종목코드").sort_values("종목코드").reset_index(drop=True)
    return df


def _calendar_dates(start: str, end: str) -> list[str]:
    start_day = datetime.strptime(start, "%Y%m%d")
    end_day = datetime.strptime(end, "%Y%m%d")
    if start_day > end_day:
        start_day, end_day = end_day, start_day

    dates = []
    day = start_day
    while day <= end_day:
        dates.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return dates


def _fetch_krx_daily_trade_cached(bas_dd: str, market: str) -> pd.DataFrame:
    key = (market.upper(), bas_dd)
    if key not in _DAILY_TRADE_CACHE:
        _DAILY_TRADE_CACHE[key] = fetch_krx_daily_trade(
            bas_dd=bas_dd,
            market=market,
            retry_previous=False,
            verbose=False,
        )
    return _DAILY_TRADE_CACHE[key]


def fetch_krx_price_history(ticker: str, start: str, end: str, market: str = "KOSPI") -> pd.DataFrame | None:
    ticker = str(ticker).strip().zfill(6)
    markets = ["KOSPI", "KOSDAQ"] if market.upper() == "ALL" else [market.upper()]
    rows = []

    for bas_dd in _calendar_dates(start, end):
        for market_name in markets:
            try:
                daily_df = _fetch_krx_daily_trade_cached(bas_dd, market_name)
            except KRXAuthError:
                raise
            except Exception:
                continue
            if daily_df is None or daily_df.empty:
                continue
            matched = daily_df[daily_df["종목코드"].astype(str).str.zfill(6) == ticker]
            if not matched.empty:
                rows.append(matched.iloc[0])
                break

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["일자"] = pd.to_datetime(df["basDd"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["일자"]).sort_values("일자").set_index("일자")
    return df[["시가", "고가", "저가", "종가", "거래량"]].copy()


def get_krx_tickers(bas_dd: str, market: str = "KOSPI") -> list[str]:
    df = fetch_krx_daily_trade(bas_dd=bas_dd, market=market)
    if df.empty:
        raise RuntimeError(f"KRX 데이터 없음: basDd={bas_dd}, market={market}")
    return df["종목코드"].astype(str).str.zfill(6).tolist()
