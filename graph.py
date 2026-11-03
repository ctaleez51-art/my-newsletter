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
from langgraph.types import Send
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
    checked:   list                             # ④ 검수 합격 — 자세히 싣는다
    demoted:   list                             # ④ 재요약 뒤에도 불합격 — 제목·링크 목록으로 내린다
    sent:      str                              # ⑤ 발행 결과: 발송 완료 / DRY_RUN / 발송 실패
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


def retry(fn):
    """본문 요청은 가끔 한 번씩 실패한다. 한 번 더 시도하고, 그래도 실패하면 예외를 그대로 올린다."""
    try:
        return fn()
    except Exception:
        time.sleep(1)
        return fn()


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
        try:
            detail = retry(lambda: requests.get(url, headers=UA, timeout=20).text)
        except Exception:
            detail = ""                         # 두 번 다 실패하면 본문 칸을 못 찾은 것(잘림)으로 남는다
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
            full = retry(lambda: taxlaw("ASIQTB002PR01", {"dcmDVO": {"ntstDcmId": d["DOC_ID"]}}))
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
            dvo = retry(lambda: taxlaw("ASISTA003PR01", {"ntstBscId": r["ntstBscId"],
                                                         "ntstBrkdId": r["ntstBrkdId"]})).get("txaStttDVO")
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
    title: str = Field(description="고른 글의 제목. 목록에 적힌 그대로 옮겨 쓴다")
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
    norm = lambda t: re.sub(r"\W", "", t)       # 따옴표·공백·괄호 차이는 무시하고 글자만 비교
    titles = [norm(it["title"]) for it in items]
    same = lambda i: (0 <= i < len(items) and titles[i] and norm(p.title)
                      and (titles[i] in norm(p.title) or norm(p.title) in titles[i]))
    seen, picks = set(), []
    for p in out.picks:
        # 번호와 제목이 어긋나면(모델이 번호를 헷갈림) 제목으로 바로잡는다. 둘 다 안 맞으면 버린다.
        if not same(p.index):
            p.index = next((i for i in range(len(items)) if same(i)), -1)
        if p.index >= 0 and p.index not in seen:     # 없는 번호·같은 번호는 버린다
            seen.add(p.index)
            picks.append(p)
    return picks


def select(s: dict) -> dict:                   # ② 중요도 선별 — 예선(소스 종류별) → 본선
    items, per, target = s["collected"], AUDIENCE["per_group"], AUDIENCE["target"]
    # 자세히 다룰 후보는 본문에 글자가 있는 글(정상)만. 이미지·첨부뿐인 글을 요약시키면 제목만 보고 지어낸다.
    # 후보에서 빠진 글도 목록에는 제목·링크로 실린다.
    textless = [it for it in items if it["status"] != "정상"]
    log = [f"   후보 제외 (본문 글자 없음) {len(textless)}건 — 목록에는 실림"] if textless else []
    survivors = []                              # (글, 예선에서 붙은 이유·라벨 또는 None)
    for group, sources in AUDIENCE["groups"].items():
        chunk = [it for it in items if it["source"] in sources and it["status"] == "정상"]
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


# ── ③ 요약: 선정된 글마다 워커 하나 (Send 팬아웃) ───────────────────────

class Draft(BaseModel):
    headline: str = Field(description="20자 내외의 한국어 헤드라인")
    summary:  str = Field(description="세 문장 요약. ~합니다체, 과장 없이 건조하게")
    why:      str = Field(description="사업자·세무사무실이 무엇을 챙겨야 하는지, 왜 중요한지 한 문장")


DRAFT_SYS = (f"당신은 세법 소식 뉴스레터 기자입니다. 독자는 {AUDIENCE['audience']}\n"
             "아래 원문을 읽고 헤드라인·요약·왜 중요한지를 쓰세요.\n"
             "원문에 없는 날짜·금액·대상·조건은 쓰지 마세요.\n"
             "summary 는 정확히 세 문장이고, 모든 문장을 '~니다.'로 끝내세요.\n"
             "why 는 한 문장으로, '~니다.'로 끝내세요.\n"
             "'주목된다·기대를 모은다' 같은 기자체 표현은 쓰지 마세요.")


class ReportIn(TypedDict):                     # 워커가 받는 것은 글 하나뿐
    item: dict


def fan_report(s: dict):                       # 선정된 글 수만큼 워커를 펼친다
    if not s["picked"]:
        return ["verify"]                      # 고른 글이 없으면 펼칠 것 없이 다음으로
    return [Send("report", {"item": it}) for it in s["picked"]]


def write_draft(it, problems=None):
    """글 한 건을 요약한다. problems 가 있으면 지난 요약의 불합격 사유를 알려 주고 다시 쓰게 한다."""
    body = it["body"][:6000]                   # 본문은 collect 에서 이미 받아 두었다
    msgs = [{"role": "system", "content": DRAFT_SYS},
            {"role": "user", "content": f"제목: {it['title']}\n\n{body}"}]
    if problems:
        msgs.append({"role": "user", "content": "지난 요약은 검수에서 떨어졌습니다. 아래를 고쳐 다시 쓰세요.\n"
                                                + "\n".join(f"- {p}" for p in problems)})
    d = llm().chat.completions.parse(model="gpt-4.1-mini", temperature=0, messages=msgs,
                                     response_format=Draft).choices[0].message.parsed
    return {**it, "body": body, **d.model_dump()}


def report(s: ReportIn) -> dict:               # ③ 요약 — 글 한 건을 맡는다
    it = s["item"]
    try:
        d = write_draft(it)
    except Exception as e:                     # 한 건이 실패해도 나머지 워커는 계속
        return {"drafted": [], "log": [f"③ 취재 실패 [{it['source']}] {it['title'][:40]} ({type(e).__name__})"]}
    return {"drafted": [d], "log": [f"③ 취재   [{it['source']}] {d['headline']}"]}


# ── ④ 검수: LLM 원문 대조 + 코드 규칙 ──────────────────────────────────
# 불합격이면 사유를 붙여 한 번 다시 요약한다. 다시 불합격이면 자세히 싣지 않고 목록으로 내린다.

class Verdict(BaseModel):
    ok: bool = Field(description="요약·헤드라인·why 가 모두 원문에 근거하면 true")
    problems: list[str] = Field(description="원문에 없거나 원문과 다른 내용. 문제가 없으면 빈 목록")


VERIFY_SYS = ("당신은 세법 뉴스레터 검수자입니다. 원문과 요약을 대조하세요.\n"
              "헤드라인·요약·why 에 원문에 없거나 원문과 다른 날짜·금액·대상·조건·사실이 있으면 불합격입니다.\n"
              "원문 내용을 줄이거나 쉽게 바꿔 쓴 것은 문제가 아닙니다.")


def sentences(text):
    return [x for x in re.split(r"(?<=[.?!])\s+", text.strip()) if x]


def rule_problems(d):
    """문체·문장 수 검사. 코드로 확실히 잴 수 있는 것만 본다."""
    out = []
    summ = sentences(d["summary"])
    if len(summ) != 3:
        out.append(f"summary 가 세 문장이 아니라 {len(summ)}문장입니다.")
    if any(not x.endswith("니다.") for x in summ):
        out.append("summary 에 '~니다.'로 끝나지 않는 문장이 있습니다.")
    why = sentences(d["why"])
    if len(why) != 1:
        out.append(f"why 가 한 문장이 아니라 {len(why)}문장입니다.")
    if any(not x.endswith("니다.") for x in why):
        out.append("why 가 '~니다.'로 끝나지 않습니다.")
    return out


def llm_problems(d):
    v = llm().chat.completions.parse(
        model="gpt-4.1-mini", temperature=0,
        messages=[{"role": "system", "content": VERIFY_SYS},
                  {"role": "user", "content": f"[원문]\n제목: {d['title']}\n{d['body']}\n\n"
                                              f"[요약]\n헤드라인: {d['headline']}\n요약: {d['summary']}\n"
                                              f"why: {d['why']}"}],
        response_format=Verdict).choices[0].message.parsed
    return [] if v.ok else (v.problems or ["원문과 다른 내용이 있습니다."])


def check(d):
    return rule_problems(d) + llm_problems(d)


def verify(s: dict) -> dict:                   # ④ 검수 — 불합격이면 1회 재요약, 재불합격이면 목록으로
    checked, demoted, log = [], [], []
    for d in s["drafted"]:
        name = f"[{d['source']}] {d['headline']}"
        try:
            problems = check(d)
            if problems:
                log.append(f"   ✘ {name} — 재요약\n" + "".join(f"       └ {p}\n" for p in problems).rstrip())
                d = write_draft(d, problems)
                problems = check(d)
        except Exception as e:                 # 검수 자체가 실패하면 확인 안 된 요약이므로 싣지 않는다
            problems = [f"검수 실패 ({type(e).__name__})"]
        if problems:
            demoted.append({**d, "problems": problems})
            log.append(f"   ✘ {name} — 목록으로 내림\n" + "".join(f"       └ {p}\n" for p in problems).rstrip())
        else:
            checked.append(d)
            log.append(f"   ✔ {name}")
    return {"checked": checked, "demoted": demoted,
            "log": [f"④ 검수   {len(s['drafted'])}건 → 합격 {len(checked)} · 목록으로 내림 {len(demoted)}"] + log}


# ── ⑤ 발행: Discord 웹훅 ──────────────────────────────────────────
# 핵심 글은 embed 하나씩 자세히, 나머지 새 글은 전부 소스별 제목·링크 목록으로 싣는다.
# DRY_RUN=1(기본)이면 보내지 않고 보낼 내용만 로그에 찍는다. seen.json 은 실제 발송이 모두 성공한 뒤에만 저장한다.

DESC_MAX = 3800                                # embed 설명칸 4,096자 제한보다 조금 작게 자른다
MSG_MAX = 5800                                 # 메시지 하나의 embed 글자 합 6,000자 제한
COLOR = 0x2B6CB0


def esc(t):                                    # 제목의 [ ] 가 링크 문법을 깨지 않게
    return t.replace("[", "\\[").replace("]", "\\]")


def build_messages(s):
    """보낼 메시지 목록. 메시지마다 embed 최대 10개, 글자 합 MSG_MAX 이하."""
    today = time.strftime("%Y-%m-%d")
    top = s["checked"]
    if not s["collected"]:
        return [{"content": f"📮 세법 뉴스레터 {today} — 오늘은 새 글이 없습니다."}]

    embeds = [{"title": d["headline"][:256], "url": d["url"], "color": COLOR,
               "description": f"{d['summary']}\n\n**왜 중요한가** {d['why']}"[:DESC_MAX],
               "footer": {"text": f"{d['source']} · {d['at']} · 원문: {d['title'][:150]}"}} for d in top]

    # 나머지 새 글: 소스별 제목·링크. 길면 여러 embed 로 나눈다.
    shown = {d["id"] for d in top}
    rest = [it for it in s["collected"] if it["id"] not in shown]
    for name, _ in SOURCES:
        lines = [f"• [{esc(it['title'][:120])}]({it['url']}) `{it['at']}`"
                 for it in rest if it["source"] == name]
        part, n = [], 1
        for line in lines + [None]:
            if line is None or len("\n".join(part + [line])) > DESC_MAX:
                if part:
                    title = f"{name} 새 글 {len(lines)}건" + (f" ({n})" if n > 1 else "")
                    embeds.append({"title": title, "description": "\n".join(part), "color": 0x718096})
                    part, n = [], n + 1
            if line is not None:
                part.append(line)

    head = (f"📮 **세법 뉴스레터 {today}** — 새 글 {len(s['collected'])}건 중 "
            f"핵심 {len(top)}건을 자세히, 나머지 {len(rest)}건은 목록으로 싣습니다.")
    msgs, cur, size = [], [], 0
    for e in embeds:
        n = len(e["title"]) + len(e.get("description", "")) + len(e.get("footer", {}).get("text", ""))
        if cur and (len(cur) == 10 or size + n > MSG_MAX):
            msgs.append({"embeds": cur})
            cur, size = [], 0
        cur.append(e)
        size += n
    msgs.append({"embeds": cur})
    msgs[0]["content"] = head
    return msgs


def send(url, msg):
    r = requests.post(url + "?wait=true", json=msg, timeout=20)
    if r.status_code == 429:                   # 너무 빨리 보냈으면 알려 준 시간만큼 쉬고 한 번 더
        time.sleep(float(r.json().get("retry_after", 1)))
        r = requests.post(url + "?wait=true", json=msg, timeout=20)
    r.raise_for_status()


def publish(s: dict) -> dict:                  # ⑤ 발행 — 성공하면 seen.json 갱신
    import os
    load_dotenv(Path(__file__).parent / ".env")
    msgs = build_messages(s)
    dry = os.getenv("DRY_RUN", "1") != "0"
    head = f"⑤ 발행   핵심 {len(s['checked'])}건 + 목록, 메시지 {len(msgs)}개"
    if dry:
        preview = json.dumps(msgs, ensure_ascii=False, indent=1)
        return {"sent": "DRY_RUN", "log": [head + " — DRY_RUN=1 이라 보내지 않음 (seen.json 그대로)", preview]}
    try:
        for m in msgs:
            send(os.environ["DISCORD_WEBHOOK_URL"], m)
            time.sleep(0.5)
    except Exception as e:                     # 발송이 실패하면 seen 을 저장하지 않는다 → 다음 실행에서 다시 받는다
        return {"sent": "발송 실패", "log": [head + f" — 발송 실패 ({type(e).__name__}), seen.json 저장 안 함"]}
    seen = s["seen"] + [it["id"] for it in s["collected"]]
    STORE.mkdir(exist_ok=True)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")
    return {"sent": "발송 완료", "log": [head + f" — 발송 완료, seen.json {len(seen)}건 저장"]}


def build():                                    # 노드를 이름으로 찾아 붙인다
    g = StateGraph(Brief)
    for name in ("collect", "select", "report", "verify", "publish"):
        g.add_node(name, globals()[name])
    g.add_edge(START, "collect")
    g.add_edge("collect", "select")
    g.add_conditional_edges("select", fan_report, ["report", "verify"])   # 고정 엣지 대신 팬아웃
    g.add_edge("report", "verify")
    g.add_edge("verify", "publish")
    g.add_edge("publish", END)
    return g


def run():
    init = {"seen": load_seen(), "collected": [], "picked": [], "drafted": [],
            "checked": [], "demoted": [], "sent": "", "log": []}
    return build().compile().invoke(init)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for line in run()["log"]:
        print(line)
