"""
KRX Open API standalone smoke test for Box Range Scanner v12.

Purpose:
- Verify that KRX_API_KEY can call KRX Open API.
- Inspect response shape before wiring it into streamlit_app.py.
- Identify fields needed by the existing scanner.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError as exc:
    raise RuntimeError("requests 패키지가 필요합니다. 먼저 `pip install requests`를 실행하세요.") from exc


API_URLS = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
}

API_IDS = {
    "KOSPI": "stk_bydd_trd",
    "KOSDAQ": "ksq_bydd_trd",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def default_bas_dd() -> str:
    day = datetime.now() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y%m%d")


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("OutBlock_1", "outBlock_1", "output", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def detect_auth_failure(status_code: int, body: str, payload: Any) -> bool:
    if status_code == 401:
        return True

    if isinstance(payload, dict):
        return str(payload.get("respCode", "")) == "401"

    return False


def request_with_header(url: str, params: dict[str, str], header_name: str, api_key: str) -> tuple[requests.Response | None, Any, str | None]:
    headers = {header_name: api_key}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
    except requests.RequestException as exc:
        return None, None, str(exc)

    payload = None
    try:
        payload = response.json()
    except json.JSONDecodeError:
        pass

    return response, payload, None


def response_succeeded(response: requests.Response | None, payload: Any) -> bool:
    if response is None:
        return False
    return response.status_code < 400 and not detect_auth_failure(response.status_code, response.text, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="KRX Open API standalone smoke test")
    parser.add_argument("--market", choices=sorted(API_URLS), default="KOSPI")
    parser.add_argument("--date", default=default_bas_dd(), help="기준일 YYYYMMDD")
    parser.add_argument("--body-limit", type=int, default=800)
    args = parser.parse_args()

    load_dotenv(Path(__file__).with_name(".env"))
    load_dotenv(Path.cwd() / ".env")

    krx_api_key = os.getenv(f"KRX_API_KEY_{args.market}") or os.getenv("KRX_API_KEY")
    if not krx_api_key:
        raise RuntimeError(f"KRX_API_KEY_{args.market} 또는 KRX_API_KEY 환경변수가 설정되지 않았습니다.")

    url = API_URLS[args.market]
    params = {"basDd": args.date}

    print(f"api_id: {API_IDS[args.market]}")
    print(f"endpoint: {url}")
    print(f"market: {args.market}")
    print(f"basDd: {args.date}")

    attempts = []
    for header_name in ("AUTH_KEY", "Authorization"):
        response, payload, error = request_with_header(url, params, header_name, krx_api_key)
        status = response.status_code if response is not None else "REQUEST_FAILED"
        json_ok = payload is not None
        auth_failed = response is not None and detect_auth_failure(response.status_code, response.text, payload)
        attempts.append(
            {
                "header_name": header_name,
                "response": response,
                "payload": payload,
                "error": error,
                "status": status,
                "json_ok": json_ok,
                "auth_failed": auth_failed,
            }
        )
        print(f"[{header_name}] status_code: {status}, json_parse: {'OK' if json_ok else 'FAIL'}, auth_failed: {auth_failed}")
        if error:
            print(f"[{header_name}] HTTP 요청 실패: {error}")

    successful = next(
        (attempt for attempt in attempts if response_succeeded(attempt["response"], attempt["payload"])),
        None,
    )
    if successful is None:
        print("성공한 header 방식이 없습니다.")
        for attempt in attempts:
            response = attempt["response"]
            if response is not None:
                print(f"[{attempt['header_name']}] body_head:")
                print(response.text[: args.body_limit])
        return 4

    print(f"selected_header: {successful['header_name']}")
    response = successful["response"]
    payload = successful["payload"]

    print(f"status_code: {response.status_code}")
    body_head = response.text[: args.body_limit]
    print("body_head:")
    print(body_head)

    if payload is not None:
        print("json_parse: OK")
    else:
        print("json_parse: FAIL")
        print("응답은 왔지만 JSON이 아닙니다.")
        return 3

    if detect_auth_failure(response.status_code, response.text, payload):
        print("인증 실패로 보이는 응답입니다. KRX_API_KEY 또는 API 활용 신청 상태를 확인하세요.")
        return 4

    if response.status_code >= 400:
        print(f"HTTP 오류 응답입니다: {response.status_code}")
        return 5

    if isinstance(payload, dict):
        print(f"top_level_fields: {list(payload.keys())}")
    else:
        print(f"top_level_type: {type(payload).__name__}")

    rows = extract_rows(payload)
    print(f"row_count: {len(rows)}")
    if not rows:
        print("데이터 비어 있음: OutBlock_1/output/data 목록을 찾지 못했거나 행이 없습니다.")
        return 6

    first_row = rows[0]
    print(f"row_fields: {list(first_row.keys())}")
    print("first_row_sample:")
    print(json.dumps(first_row, ensure_ascii=False, indent=2)[: args.body_limit])

    scanner_fields = {
        "종목코드": first_row.get("ISU_CD"),
        "종목명": first_row.get("ISU_NM"),
        "종가": first_row.get("TDD_CLSPRC"),
        "시가": first_row.get("TDD_OPNPRC"),
        "고가": first_row.get("TDD_HGPRC"),
        "저가": first_row.get("TDD_LWPRC"),
        "거래량": first_row.get("ACC_TRDVOL"),
    }
    print("scanner_field_candidates:")
    print(json.dumps(scanner_fields, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
