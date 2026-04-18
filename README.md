[README.md](https://github.com/user-attachments/files/26856967/README.md)

# 📦 박스권 스캐너 컨트롤룸

코스피 + 코스닥 거래량 상위 종목을 자동으로 스캔해서
박스권 후보 및 돌파 임박 종목을 찾아주는 분석 도구입니다.

---

## ✅ 주요 기능

- 거래량 상위 N개 종목 자동 스캔 (기본 100개)
- 박스권 점수 계산 (변동성 / 추세 / 거래량 기반)
- 돌파신호 판정 (돌파 임박 / 관찰 필요 / 신호 약함 / 이탈 주의)
- 최소 점수 필터 + 돌파신호 필터
- 선택 종목 90일 종가 차트 + 거래량 차트
- 결과 CSV 다운로드

---

## 📁 파일 구조

```
box-range-scanner/
├── streamlit_app.py       # UI 메인
├── box_range_scanner.py   # 스캔 로직
└── requirements.txt       # 의존 패키지
```

---

## ⚙️ 설치 및 실행

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

---

## 📦 사용 패키지

- [pykrx](https://github.com/sharebook-kr/pykrx) — 한국 주식 데이터
- [streamlit](https://streamlit.io) — UI 프레임워크
- [pandas](https://pandas.pydata.org) — 데이터 처리
- [matplotlib](https://matplotlib.org) — 차트

---

## ⚠️ 주의사항

- 평일 장 마감 후 (오후 4시 이후) 실행 권장
- 주말 / 공휴일에는 거래량 데이터 조회 안 될 수 있음 → 기본 15종목으로 대체
- 100종목 스캔 시 약 3~5분 소요
- 투자 참고용 도구이며 투자 권유가 아닙니다
