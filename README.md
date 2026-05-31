[README.md](https://github.com/user-attachments/files/28436672/README.md)
# 박스권 스캐너 컨트롤룸

한국 주식시장(KOSPI / KOSDAQ / ALL)에서 박스권 후보를 스캔하고, TOP5 추천 카드, 후보 테이블, 캔들 차트, 사용자 검증 기록까지 한 화면에서 확인하는 Streamlit 앱입니다.

배포 주소: https://appapppy-arrvupjezplekjddatmdmg.streamlit.app

> Disclaimer  
> 이 도구는 참고용 분석 도구입니다. 제공되는 점수, 신호, 후보 목록은 알고리즘 기반 분석 결과이며 투자 권유나 매매 추천이 아닙니다. 모든 투자 판단과 책임은 사용자 본인에게 있습니다.

---

## 현재 상태 요약

- KRX Open API 기반 ticker / 가격 데이터 연결 완료
- KOSPI, KOSDAQ, ALL 통합 스캔 검증 완료
- KRX 실패 시 FDR, pykrx, cache, fallback 순서 유지
- 전체 스캔 chunk 자동 이어달리기 및 완료 판정 안정화
- Streamlit Cloud 배포용 secrets / `.env` 분리
- 종목명 매핑 보정 완료
- 차트 데이터도 KRX_API 우선 조회
- 후보 테이블 HTML 렌더링 및 42px 단위 wheel 스크롤 적용

---

## 파일 구조

```text
Box/
├─ streamlit_app.py          # Streamlit UI 메인
├─ box_range_scanner.py      # 박스권 스캔 엔진
├─ modules/
│  └─ krx_api.py             # KRX Open API 데이터 fetch 모듈
├─ test_krx_api.py           # KRX API 단독 호출 테스트
├─ requirements.txt          # 배포/실행 패키지
├─ .env.example              # 환경변수 예시
├─ .gitignore                # 민감정보/캐시/로그 제외
└─ README.md
```

로컬 실행 중 생성될 수 있는 `.env`, `.streamlit/secrets.toml`, `ticker_cache.csv`, `validation_log.csv`, `*.log`, `__pycache__/` 등은 Git 업로드 대상이 아닙니다.

---

## 설치 및 실행

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

KRX API 단독 테스트:

```bash
python test_krx_api.py --market KOSPI --date 20260430
python test_krx_api.py --market KOSDAQ --date 20260430
```

문법 확인:

```bash
python -m py_compile streamlit_app.py box_range_scanner.py modules/krx_api.py
```

---

## KRX API 설정

API 키는 코드에 직접 넣지 않습니다.

로컬 개발에서는 프로젝트 루트에 `.env`를 만들고 아래 값을 설정합니다.

```env
KRX_API_KEY=your_krx_open_api_key_here
```

Streamlit Cloud에서는 앱 설정의 Secrets에 아래처럼 등록합니다.

```toml
KRX_API_KEY = "your_krx_open_api_key_here"
```

시장별 키가 별도로 필요한 경우 선택적으로 사용할 수 있습니다.

```env
KRX_API_KEY_KOSPI=your_kospi_api_key_here
KRX_API_KEY_KOSDAQ=your_kosdaq_api_key_here
```

실제 API 키는 GitHub에 올리지 않습니다.

---

## KRX API 만료일 관리

KRX Open API는 승인 기간이 제한되어 있으므로 운영 메모로 만료일을 관리합니다. 만료일은 앱 코드에 하드코딩하지 않습니다.

현재 확인된 만료일:

| API | 만료일 |
| --- | --- |
| 유가증권 일별매매정보 | 2026/06/03 |
| 코스닥 종목기본정보 | 2026/06/05 |

추가 승인 예정 API:

- 코스닥 일별매매정보
- ETF 일별매매정보
- ETN 일별매매정보
- 유가증권 종목기본정보
- 코넥스 일별매매정보
- 코넥스 종목기본정보

만료 또는 권한 문제가 발생하면 `401 Unauthorized API Call` 로그를 우선 확인합니다. 로그에는 `market`, `api_id`, `url`, `status_code`를 확인할 수 있게 남기되 실제 API 키는 출력하지 않습니다.

---

## 데이터 소스 우선순위

### Ticker 수집

```text
1. KRX_API
2. FDR StockListing
3. PYKRX
4. 로컬 ticker_cache.csv
5. 최종 fallback
```

ALL 모드는 KOSPI와 KOSDAQ을 각각 수집한 뒤 종목코드와 종목명 map을 함께 병합합니다. KRX 기준일이 휴장일이거나 빈 데이터이면 최대 10일 전까지 순차 재시도합니다.

### 가격 데이터

```text
1. KRX_API
2. CACHE
3. PYKRX_FALLBACK
4. FALLBACK
```

스캔 본체와 차트 조회 모두 KRX_API를 우선 사용합니다. KRX 가격 데이터가 비어 있거나 실패할 때만 fallback 경로를 사용합니다.

---

## 스캔 모드

### 빠른 스캔

- 거래량 상위 후보군 기준으로 빠르게 스캔
- KOSPI / KOSDAQ / ALL 선택 가능
- 최종 결과에는 종목코드와 종목명이 보정되어 표시됨

### 전체 스캔

- 선택 시장 전체 종목 대상
- chunk 단위 실행
- chunk 완료 후 자동 rerun으로 다음 chunk 이어달리기
- 사용자가 직접 재개 버튼을 반복해서 누르지 않아도 완료까지 진행
- 실패/스킵 종목도 “시도 완료”로 계산

완료 판정 기준:

```text
scan_processed + scan_fail >= scan_total
```

새 전체 스캔을 시작하면 이전 partial/session 상태를 초기화합니다. 단, `validation_log`는 유지합니다.

---

## 분석 기준

- 분석기간은 앱 코드의 `analysis_days` 고정값을 사용합니다.
- 현재 코드 기준: `analysis_days = 95`
- 차트, 스캔 설명, validation context도 같은 기간 값을 사용합니다.
- 점수 계산 로직과 박스권 판단 로직은 `run_scan` 엔진에서 처리합니다.

---

## 결과 화면

### TOP5 카드

- 점수 내림차순, 동점 시 거래량 기준 정렬
- 사용자 검증 기록이 있으면 최근 검증 라벨 표시
- 검증 기록은 현재 조건 기준으로 해석하며 영구 제외 목록으로 사용하지 않음

### 후보 테이블

표시 컬럼:

- 종목코드
- 종목명
- 점수
- 거래량
- 이유
- 돌파신호

후보 테이블은 HTML/CSS 기반으로 렌더링됩니다. 내부 `.candidate-table-wrap` 영역이 직접 스크롤을 담당하며, wheel 이벤트를 42px 단위로 제어해 한 행씩 읽기 쉽게 조정했습니다.

### 차트

- KRX_API 가격 데이터를 우선 사용
- 캔들 차트 + MA20 + MA60
- 거래량 보조 차트
- 선택 dropdown에는 종목명이 실제 이름으로 표시됨
- 차트 조회 로그에는 `chart_source`, `selected_code`, `selected_name`, `market`, `row_count`를 확인할 수 있음

---

## 사용자 검증 기록

차트를 보고 사용자가 직접 판단을 남길 수 있습니다.

- 박스권 맞음
- 애매
- 박스권 아님

특징:

- 동일 조건의 동일 종목은 최신 판단으로 overwrite
- `validation_log.csv` 저장/다운로드 시 Excel 한글 깨짐 방지를 위해 `utf-8-sig` 사용
- validation_log 초기화 버튼으로 UI 기록 초기화 가능
- Git 업로드 대상 아님

---

## 후보 비율 해석

후보 비율이 높을 때는 slider를 자동 변경하지 않고 점수 분포와 권고 문구만 표시합니다.

권장 해석:

```text
80점 이상: 집중 검토
70~79점: 실전 검토 후보
60~69점: 넓게 관찰
40~80점: 후보가 많을 수 있음
```

점수 계산 로직은 README나 UI 권고와 별개로 변경하지 않습니다.

---

## KRX 통합 검증 결과

### KOSPI

- `ticker_source = KRX_API`
- `price_source = KRX_API`
- 총 대상 약 948~949개
- fallback 제한 모드 없음
- TOP5 / 후보 테이블 / 차트 정상 출력

### KOSDAQ

- `ticker_source = KRX_API`
- `price_source = KRX_API`
- `scan_total = 1823`
- `scan_processed = 1820`
- `scan_fail = 3`
- `chunk = 19 / 19` 완료
- fallback 제한 모드 없음
- TOP5 정상 출력

### ALL

- `ticker_source = KRX_API`
- `price_source = KRX_API`
- `scan_total = 2771`
- `scan_processed = 2768`
- `scan_fail = 3`
- `chunk = 28 / 28` 완료
- fallback 제한 모드 없음
- 후보 626개
- 후보 비율 22.6%
- TOP5 / 테이블 / 차트 정상 출력

---

## Streamlit Cloud 배포 체크리스트

배포 전 확인:

- `.env` 업로드 금지
- `.streamlit/secrets.toml` 업로드 금지
- `ticker_cache.csv` 업로드 금지
- `validation_log.csv` 업로드 금지
- `*.log` 업로드 금지
- `__pycache__/`, `*.pyc` 업로드 금지
- GitHub에는 코드, README, requirements, `.env.example`, `.gitignore`만 포함

배포 후 확인:

- Secrets에 `KRX_API_KEY` 등록
- KOSPI 전체 스캔에서 ticker/price source가 `KRX_API`인지 확인
- KOSDAQ 전체 스캔에서 ticker/price source가 `KRX_API`인지 확인
- ALL 통합 스캔에서 fallback 제한 모드가 없는지 확인
- TOP5 / 후보 테이블 / 차트 / validation_log 동작 확인

---

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| `401 Unauthorized API Call` | API 키, API 승인 상태, 만료일, market별 endpoint |
| KRX 데이터가 비어 있음 | 휴장일 여부, 기준일 보정 로그, 최대 10일 재시도 결과 |
| ticker source가 FALLBACK | KRX/FDR/pykrx/cache 실패 로그 확인 |
| 종목명이 코드로 표시됨 | ticker name map 생성/병합 로그 확인 |
| 차트 데이터 없음 | `chart_source`, `row_count`, 선택 종목코드 확인 |
| 후보가 너무 많음 | 점수 구간 분포를 보고 70~80 또는 60~80 범위 검토 |
| py_compile 권한 오류 | `__pycache__` 잠금 또는 권한 문제 확인 |

---

## 버전 메모

| 버전 | 주요 내용 |
| --- | --- |
| v11.6 | KRX_API 통합(ticker/가격), 기준일 10일 재시도, 상태 UI 개선, 후보 테이블 wheel 스크롤 42px 단위 제어 |
| v11.5 | validation_log 기반 사용자 검증, TOP5 라벨, CSV 저장 개선 |

---

## 팀 메모

- 구현 담당 / 코드 실행 / 개발 보조: 꼬덱(KODEK)
- 상위 담당: 지피팀장
- 검증 협업: 클로팀장
- 시각화 협업: 쩸이팀장
- 최종 결정: 디예몬
