# 세법 뉴스레터 — LangGraph 워크플로
#
# 교안 §2 뼈대: State 와 노드 다섯 개. 노드마다 하나씩 실제 코드로 갈아 끼운다.
# 이 파일은 불러오기만 해도 안전해야 한다(교안 §8). 실행은 맨 아래 __main__ 또는 run.py 에서만.

import html
import json
import operator
import re
import time
from pathlib import Path
from typing import Annotated, TypedDict

import requests
import yaml
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pydantic import BaseModel, Field

STORE = Path(__file__).parent / "store"
SEEN = STORE / "seen.json"          # 지난번까지 가져온 글 번호. 발행이 끝난 뒤에 갱신한다(⑤).
UA = {"User-Agent": "Mozilla/5.0"}
NO_TEXT = 30                        # 이 글자 수 미만이면 "글자가 없다"로 본다. 길이로 거르지는 않는다.


class Brief(TypedDict):
    seen:      list                             # 지난번까지 가져온 글 번호
    collected: list                             # ① 수집한 새 글 — 전부 발행한다
    picked:    list                             # ② 선별해 자세히 다룰 글
    drafted:   Annotated[list, operator.add]    # ③ 취재한 초안 — 워커들이 나눠 채운다
    log:       Annotated[list, operator.add]    # 무슨 일이 있었는지


# ── ① 수집: 소스 5곳 ────────────────────────────────────────────
# 요청 방식은 measure_sources.py 로 확인한 것과 같다. 소스마다 최신 글 목록을 받아
# 아래 항목으로 통일한다.
#   id      소스 안에서 글을 구별하는 번호 (seen.json 에 적는 값)
#   title · url · source · at(게시일, 홈택스는 수정일) · body(본문 글자)
#   status  정상 / 이미지·첨부만 / 빈 본문 / 잘림(본문을 못 찾음 = 수집 실패)

def strip_tags(s):
    if not s:
        return ""
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def item(source, id_, title, url, at, raw_body, has_media=False):
    """raw_body 가 None 이면 본문 위치를 못 찾은 것(잘림)."""
    body = strip_tags(raw_body) if raw_body is not None else ""
    if raw_body is None:
        status = "잘림"
    elif len(body) >= NO_TEXT:
        status = "정상"
    elif has_media or re.search(r"<img", raw_body, flags=re.I):
        status = "이미지·첨부만"
    else:
        status = "정상" if body else "빈 본문"
    return {"id": f"{source}:{id_}", "title": title, "url": url, "source": source,
            "at": at, "body": body, "status": status}


def ymd(s):
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def nts(source, mi, bbs, seen):
    """국세청 보도자료·공지사항. 목록 한 페이지 10건, 새 글만 상세 페이지에서 본문을 받는다."""
    page = requests.get(f"https://www.nts.go.kr/nts/na/ntt/selectNttList.do?mi={mi}&bbsId={bbs}",
                        headers=UA, timeout=20).text
    rows = re.findall(r'data-id="(\d+)" title="([^"]*)"\s+class="nttInfoBtn".*?'
                      r'<td data-table="date">([\d.]+)</td>', page, flags=re.S)
    out = []
    for no, title, day in rows:
        if f"{source}:{no}" in seen:
            continue
        url = f"https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi={mi}&bbsId={bbs}&nttSn={no}"
        detail = requests.get(url, headers=UA, timeout=20).text
        # 본문은 bbsV_cont 부터 첨부파일 칸 앞까지. 첨부가 없는 글은 이전·다음글 칸 앞까지
        m = re.search(r'class="bbsV_cont">(.*?)<(?:div class="bbsV_atchmnfl"|ul class="bbsV_prne")',
                      detail, flags=re.S)
        out.append(item(source, no, html.unescape(title), url, day.strip(".").replace(".", "-"),
                        m.group(1) if m else None, "bbsV_atchmnfl" in detail))
        time.sleep(0.3)
    return out, len(rows)


def hometax(seen):
    """홈택스 공지사항. 본문이 목록과 함께 온다. 수정일 순이라 고쳐 올린 글도 새 글로 본다."""
    url = ("https://hometax.go.kr/wqAction.do?actionId=ATXPPBAA001R01"
           "&screenId=UTXPPBAA26&popupYn=false&realScreenId=")
    body = {"blrdNo": "1", "endDt": "", "schCntn": "", "schType": "all", "strtDt": "",
            "tbbsClsfCd": "all", "pageInfoVO": {"totalCount": "0", "pageSize": "20", "pageNum": "1"}}
    rows = requests.post(url, json=body, headers=UA, timeout=20).json()["intgTbbsInqrDVOList"]
    out = []
    for r in rows:
        id_ = f"{r['tbbsSn']}@{r['lstAltDtm']}"          # 글번호 + 수정일시: 고치면 새 번호가 된다
        if f"홈택스 공지사항:{id_}" in seen:
            continue
        # 글마다 주소가 없어 공지사항 목록 주소를 링크로 쓴다
        out.append(item("홈택스 공지사항", id_, r["tbbsTtl"],
                        "https://hometax.go.kr/websquare/websquare.html?w2xPath=/ui/pp/index_pp.xml&menuCd=index3",
                        ymd(r["lstAltDtm"]), r["tbbsCntn"] or ""))
    return out, len(rows)


TAXLAW = "https://taxlaw.nts.go.kr/action.do"


def taxlaw(action, param):
    r = requests.post(TAXLAW, data={"actionId": action, "paramData": json.dumps(param)},
                      headers=UA, timeout=30)
    return r.json()["data"][action]


def precedent(seen):
    """국세법령정보시스템 판례·결정례. 등록일자 최신순 20건. 전문은 원문 화면이 부르는 요청으로 받는다."""
    rows = taxlaw("ASIPDI002PR01", {
        "collectionName": "precedent,precedent_gr", "sortField": "FRS_RGT_DTM/DESC",
        "startCount": 1, "viewCount": 20,
        "dcmClCdCtl": ["001_05", "001_06", "001_07", "001_08", "001_09", "001_10", "003_01"]})["body"]
    out = []
    for r in rows:
        d = r["dcm"]
        if f"판례·결정례:{d['DOC_ID']}" in seen:
            continue
        try:
            full = taxlaw("ASIQTB002PR01", {"dcmDVO": {"ntstDcmId": d["DOC_ID"]}})
            raw = "\n".join(e.get("dcmFleByte") or "" for e in full.get("dcmHwpEditorDVOList") or [])
            media = bool(full.get("cmnFleBrkdDVO") or full.get("fleDVOList"))
        except Exception:
            raw, media = None, False
        out.append(item("판례·결정례", d["DOC_ID"], f"[{d['NTST_DCM_CL_NM']}] {d['TTL']}",
                        f"https://taxlaw.nts.go.kr/pd/USEPDA002P.do?ntstDcmId={d['DOC_ID']}",
                        ymd(d["FRS_RGT_DTM"]), raw, media))
        time.sleep(0.3)
    return out, len(rows)


def amendment(seen):
    """국세법령정보시스템 최신개정법령. 공포일 최신순 20건. 본문은 개정이유 + 개정내용."""
    rows = taxlaw("ASISTI001MR01", {"pageIndex": 1, "recordCountPerPage": 20})["ltstRvsnStttDVOList"]
    out = []
    for r in rows:
        if f"최신개정법령:{r['ntstBrkdId']}" in seen:
            continue
        try:
            dvo = taxlaw("ASISTA003PR01", {"ntstBscId": r["ntstBscId"],
                                           "ntstBrkdId": r["ntstBrkdId"]}).get("txaStttDVO")
            raw = f"{dvo.get('ntstRvsnRsn') or ''}\n{dvo.get('ntstRvsnCntn') or ''}" if dvo else ""
        except Exception:
            raw = None
        out.append(item("최신개정법령", r["ntstBrkdId"], r["ltstRvsnNm"],
                        "https://taxlaw.nts.go.kr/st/USESTA003P.do"
                        f"?ntstBscId={r['ntstBscId']}&ntstBrkdId={r['ntstBrkdId']}",
                        ymd(r["ntstPmgDt"]), raw))
        time.sleep(0.3)
    return out, len(rows)


SOURCES = [
    ("국세청 보도자료", lambda seen: nts("국세청 보도자료", 2201, 1028, seen)),
    ("국세청 공지사항", lambda seen: nts("국세청 공지사항", 2207, 1011, seen)),
    ("홈택스 공지사항", hometax),
    ("판례·결정례",     precedent),
    ("최신개정법령",    amendment),
]


def load_seen():
    return json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else []


def collect(s: dict) -> dict:                  # ① 자료 수집 — 지난번까지 가져온 적 없는 글만
    seen = set(s["seen"])
    items, dead, lines = [], [], []
    for name, fetch in SOURCES:
        try:
            new, listed = fetch(seen)
        except Exception as e:
            dead.append(name)                   # 한 곳이 죽어도 나머지는 계속
            lines.append(f"   {name}: 응답 없음 ({type(e).__name__})")
            continue
        items += new
        count = {k: sum(i["status"] == k for i in new) for k in ("정상", "이미지·첨부만", "빈 본문", "잘림")}
        label = " · ".join(f"{k} {v}" for k, v in count.items() if v)
        lines.append(f"   {name}: 목록 {listed}건 중 새 글 {len(new)}건" + (f" ({label})" if label else ""))
        if seen and listed and len(new) == listed:
            # 받은 목록이 전부 새 글이면 그보다 앞의 글을 놓쳤을 수 있다
            lines.append(f"   ⚠ {name}: 목록 전부가 새 글 — 목록 밖에 더 있을 수 있음")
    return {"collected": items,
            "log": [f"① 수집   새 글 {len(items)}건 (지난번까지 본 글 {len(seen)}건 제외)"
                    + (f" · 응답 없음 {dead}" if dead else "")] + lines}


# ── ② 선별: 소스 종류별 예선 → 본선 ──────────────────────────────────
# 묶음은 건수가 아니라 소스 종류(audience.yaml groups)로 나눈다. 판례가 건수로 소식을 덮지 않게 하고,
# "새 소식이 메인"이라는 기준을 구조에도 반영하기 위해서다. 고르지 않은 글도 버리지 않는다(목록에 실린다).

class Pick(BaseModel):
    index: int = Field(description="후보 목록에서의 번호")
    reason: str = Field(description="왜 골랐는지 한 문장")
    event: str = Field(description="이 글이 다루는 사건을 짧은 라벨로. 같은 사건이면 같은 라벨")


class Shortlist(BaseModel):
    picks: list[Pick]


AUDIENCE = yaml.safe_load((Path(__file__).parent / "audience.yaml").read_text(encoding="utf-8"))
CRITERIA = (f"당신은 세법 소식 뉴스레터 편집자입니다. 독자는 {AUDIENCE['audience']}\n"
            + "".join(f"- {line}\n" for line in AUDIENCE["importance"])
            + "자세히 다루지 않을 것: " + ", ".join(AUDIENCE["exclude"]))
_client = None


def llm():
    global _client                              # 불러오기만 할 때는 키가 없어도 되도록, 처음 쓸 때 만든다
    if _client is None:
        load_dotenv(Path(__file__).parent / ".env")
        _client = OpenAI()
    return _client


def ask_picks(items, n):
    listing = "\n".join(f"{i}. [{it['source']}] {it['title']}" for i, it in enumerate(items))
    sys_ = (f"{CRITERIA}\n\n아래 목록에서 중요한 순서대로 {n}건을 고르세요.\n"
            "같은 사건을 다룬 글에는 같은 event 라벨을 붙이세요.")
    out = llm().chat.completions.parse(
        model="gpt-4.1-mini", temperature=0,
        messages=[{"role": "system", "content": sys_}, {"role": "user", "content": listing}],
        response_format=Shortlist).choices[0].message.parsed
    seen, picks = set(), []
    for p in out.picks:                         # 없는 번호·같은 번호는 버린다
        if 0 <= p.index < len(items) and p.index not in seen:
            seen.add(p.index)
            picks.append(p)
    return picks


def select(s: dict) -> dict:                   # ② 중요도 선별 — 예선(소스 종류별) → 본선
    items, per, target = s["collected"], AUDIENCE["per_group"], AUDIENCE["target"]
    log = []
    survivors = []                              # (글, 예선에서 붙은 이유·라벨 또는 None)
    for group, sources in AUDIENCE["groups"].items():
        chunk = [it for it in items if it["source"] in sources]
        if len(chunk) <= per:                   # 고를 필요가 없으면 LLM 을 부르지 않는다
            survivors += [(it, None) for it in chunk]
            log.append(f"   예선 {group}: {len(chunk)}건 → 전부 통과 ({per}건 이하)")
            continue
        picks = ask_picks(chunk, per)
        survivors += [(chunk[p.index], p) for p in picks]
        log.append(f"   예선 {group}: {len(chunk)}건 → {len(picks)}건")

    if len(survivors) <= target:
        finals = [(it, p) for it, p in survivors]
        log.append(f"   본선: {len(survivors)}건 → 전부 선정 ({target}건 이하)")
    else:
        # 같은 사건이 겹치면 건너뛰고 다음 순위로 채울 수 있게 넉넉히 받는다(교안 891행)
        ranked = ask_picks([it for it, _ in survivors], target + 3)
        finals, events = [], set()
        for p in ranked:
            if p.event in events:
                log.append(f"   본선 중복 사건 건너뜀 [{p.event}] {survivors[p.index][0]['title'][:40]}")
                continue
            events.add(p.event)
            finals.append((survivors[p.index][0], p))
            if len(finals) == target:
                break
        log.append(f"   본선: {len(survivors)}건 → {len(finals)}건")

    picked = []
    for it, p in finals:
        picked.append({**it, "event": p.event if p else "", "pick_reason": p.reason if p else ""})
        log.append(f"   ✔ [{it['source']}] {it['title'][:50]}"
                   + (f"\n       └ [{p.event}] {p.reason}" if p else ""))
    return {"picked": picked,
            "log": [f"② 선별   {len(items)} → 예선 {len(survivors)} → {len(picked)}건"] + log}


def report(s: dict) -> dict:                   # ③ 요약
    return {"drafted": [], "log": [f"③ 취재   {len(s['picked'])}건 (빈 노드)"]}


def verify(s: dict) -> dict:                   # ④ 검수
    return {"log": [f"④ 검수   {len(s['drafted'])}건 (빈 노드)"]}


def publish(s: dict) -> dict:                  # ⑤ 발행
    return {"log": [f"⑤ 발행   {len(s['drafted'])}건 (빈 노드)"]}


def build():                                    # 노드를 이름으로 찾아 붙인다
    g = StateGraph(Brief)
    for name in ("collect", "select", "report", "verify", "publish"):
        g.add_node(name, globals()[name])
    g.add_edge(START, "collect")
    g.add_edge("collect", "select")
    g.add_edge("select", "report")
    g.add_edge("report", "verify")
    g.add_edge("verify", "publish")
    g.add_edge("publish", END)
    return g


def run():
    init = {"seen": load_seen(), "collected": [], "picked": [], "drafted": [], "log": []}
    return build().compile().invoke(init)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for line in run()["log"]:
        print(line)
