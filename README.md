# 세법 뉴스레터 에이전트

국세청·홈택스·국세법령정보시스템에 새로 올라온 글을 매일 모아 Discord로 보내는 LangGraph 에이전트입니다.
새 글은 하나도 빼지 않고 전부 싣습니다. 그중 핵심 5건은 요약과 "왜 중요한가"를 붙여 자세히 싣고, 나머지는 제목·링크 목록으로 싣습니다.

설계, 소스 채택 기준, 실행 기록은 [REPORT.md](REPORT.md)에 있습니다.

## 흐름

수집(소스 5곳) → 선별(자세히 다룰 핵심 5건) → 요약 → 검수(원문 대조, 불합격이면 재요약) → 발행(Discord)

## 실행

```bash
pip install -r requirements.txt
python run.py            # DRY_RUN=1(기본): 보내지 않고 보낼 내용만 출력
DRY_RUN=0 python run.py  # 실제 발송
```

`.env`에 `OPENAI_API_KEY`, `DISCORD_WEBHOOK_URL`이 필요합니다.
GitHub Actions([daily.yml](.github/workflows/daily.yml))가 매일 12:00(KST)에 실행하고, 수동 실행도 할 수 있습니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| [graph.py](graph.py) | LangGraph 워크플로 (collect → select → report → verify → publish) |
| [run.py](run.py) | 한 번 실행하고 실행 기록을 `store/metrics.jsonl`에 한 줄 더함 |
| [audience.yaml](audience.yaml) | 독자, 중요도 기준, 선별 묶음 |
| [measure_sources.py](measure_sources.py) | 소스 후보 6곳 측정 (채택·탈락 근거) |
| [store/source_measure.json](store/source_measure.json) | 소스 측정 결과 |
| [store/seen.json](store/seen.json) | 이미 보낸 글 번호 (다음 실행에서 다시 보내지 않음) |
| [store/metrics.jsonl](store/metrics.jsonl) | 실행마다 수집·선별·검수·발송 기록 |
| [.github/workflows/](.github/workflows/) | `daily.yml` 매일 실행, `access-test.yml` 해외 서버 접속 확인 |
| [REPORT.md](REPORT.md) | 과제 보고서 |
