# 세법 뉴스레터 실행 — 로컬과 GitHub Actions 가 모두 이 파일을 실행한다.
#
# 하는 일: graph.py 의 워크플로를 한 번 돌리고, 로그를 화면에 찍고, store/metrics.jsonl 에 실행 기록 한 줄을 더한다.
# DRY_RUN=1(기본)이면 Discord 로 보내지 않는다. 실제로 보내려면 DRY_RUN=0.

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from graph import SOURCES, STORE, run

sys.stdout.reconfigure(encoding="utf-8")
KST = timezone(timedelta(hours=9))             # Actions 서버는 UTC 라 한국 시각을 직접 적는다

out = run()
for line in out["log"]:
    print(line)

c = out["collected"]
metrics = {
    "at": datetime.now(KST).isoformat(timespec="seconds"),
    "dry_run": os.getenv("DRY_RUN", "1") != "0",
    "seen_before": len(out["seen"]),
    "collected": len(c),
    "by_source": {name: sum(i["source"] == name for i in c) for name, _ in SOURCES},
    "by_status": {k: sum(i["status"] == k for i in c) for k in ("정상", "이미지·첨부만", "빈 본문", "잘림")},
    "picked": len(out["picked"]),
    "drafted": len(out["drafted"]),
    "verify_pass": len(out["checked"]),
    "verify_demoted": len(out["demoted"]),
    "demoted_reasons": [{"title": d["title"], "problems": d["problems"]} for d in out["demoted"]],
    "picks": [{"source": p["source"], "title": p["title"], "event": p["event"], "reason": p["pick_reason"]}
              for p in out["picked"]],
    "sent": out["sent"],
}
STORE.mkdir(exist_ok=True)
with open(STORE / "metrics.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
print(f"\nmetrics.jsonl 에 기록: 수집 {len(c)} · 선별 {len(out['picked'])} · 검수 합격 {len(out['checked'])} · {out['sent']}")

if out["sent"] == "발송 실패":
    sys.exit(1)                                 # Actions 에서 실패로 표시되게
