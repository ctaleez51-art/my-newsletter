# 소스 4곳 측정
#
# 하는 일: 소스마다 목록 1페이지를 받아 아래를 잰다. REPORT.md 소스 채택표의 근거로 쓴다.
#   - 응답 상태, 전체 건수, 받은 건수, 최신 글 날짜
#   - 본문 길이: 받은 글마다 재고, 교안 G1 기준(600자 이상)을 넘는 글 수
#   - 원문 링크: 최신 글의 원문 주소를 만들고, 그 주소가 열리는지(응답 200)
# 결과는 화면에 표로 찍고 store/source_measure.json 에 저장한다.

import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

헤더 = {"User-Agent": "Mozilla/5.0"}
G1_기준 = 600          # 교안 G1: 본문 600자 이상
저장위치 = Path(__file__).parent / "store" / "source_measure.json"


def 태그벗기기(원본):
    """HTML 에서 태그를 지우고 글자만 남긴다. (tax-notice-summary/2_clean_body.py 와 같음)"""
    if not 원본:
        return ""
    글 = re.sub(r"<script.*?</script>|<style.*?</style>", "", 원본, flags=re.S | re.I)
    글 = re.sub(r"<br\s*/?>", "\n", 글, flags=re.I)
    글 = re.sub(r"</p>|</div>|</tr>", "\n", 글, flags=re.I)
    글 = re.sub(r"<[^>]+>", "", 글)
    글 = html.unescape(글).replace("\xa0", " ")
    글 = re.sub(r"[ \t]+", " ", 글)
    글 = re.sub(r"\n\s*\n+", "\n", 글)
    return 글.strip()


def 열리는지(주소):
    try:
        return requests.get(주소, headers=헤더, timeout=20).status_code == 200
    except requests.RequestException:
        return False


# ── 국세청 보도자료 / 공지사항 (같은 화면 구조) ──────────────────────────

def 국세청(이름, mi, bbsId):
    목록주소 = f"https://www.nts.go.kr/nts/na/ntt/selectNttList.do?mi={mi}&bbsId={bbsId}"
    응답 = requests.get(목록주소, headers=헤더, timeout=20)
    화면 = 응답.text

    전체 = re.search(r'pge_totle">\s*전체<strong>([\d,]+)</strong>', 화면)
    # 목록 한 줄: <a ... data-id="번호" title="제목" class="nttInfoBtn"> ... <td data-table="date">2026.11.02.</td>
    줄들 = re.findall(
        r'data-id="(\d+)" title="([^"]*)"\s+class="nttInfoBtn".*?<td data-table="date">([\d.]+)</td>',
        화면, flags=re.S,
    )

    글들 = []
    for 번호, 제목, 날짜 in 줄들:
        원문 = f"https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi={mi}&bbsId={bbsId}&nttSn={번호}"
        상세 = requests.get(원문, headers=헤더, timeout=20).text
        # 본문은 bbsV_cont 부터 첨부파일 칸(bbsV_atchmnfl) 앞까지. 첨부가 없는 글은 이전·다음글 칸(bbsV_prne) 앞까지
        본문칸 = re.search(r'class="bbsV_cont">(.*?)<(?:div class="bbsV_atchmnfl"|ul class="bbsV_prne")',
                        상세, flags=re.S)
        본문 = 태그벗기기(본문칸.group(1)) if 본문칸 else ""
        글들.append({"날짜": 날짜.strip(".").replace(".", "-"), "제목": html.unescape(제목),
                    "본문길이": len(본문), "원문": 원문})
        time.sleep(0.3)

    return 정리(이름, 목록주소, 응답.status_code, 전체.group(1) if 전체 else None, 글들)


# ── 홈택스 공지사항 (JSON, 본문이 목록과 함께 옴) ──────────────────────────

def 홈택스():
    주소 = ("https://hometax.go.kr/wqAction.do?actionId=ATXPPBAA001R01"
          "&screenId=UTXPPBAA26&popupYn=false&realScreenId=")
    보낼내용 = {"blrdNo": "1", "endDt": "", "schCntn": "", "schType": "all", "strtDt": "",
             "tbbsClsfCd": "all",
             "pageInfoVO": {"totalCount": "0", "pageSize": "10", "pageNum": "1"}}
    응답 = requests.post(주소, json=보낼내용, headers=헤더, timeout=20)
    결과 = 응답.json()

    글들 = []
    for 글 in 결과["intgTbbsInqrDVOList"]:
        if 글.get("prfcAnncMttrYn") == "Y":     # 맨 위 고정공지는 최신 글이 아니므로 뺀다
            continue
        수정일 = 글["lstAltDtm"][:8]             # 홈택스 목록은 수정일 순
        글들.append({"날짜": f"{수정일[:4]}-{수정일[4:6]}-{수정일[6:]}", "제목": 글["tbbsTtl"],
                    "본문길이": len(태그벗기기(글["tbbsCntn"])),
                    "원문": None})               # 글마다 주소가 없다

    return 정리("홈택스 공지사항", 주소, 응답.status_code,
              결과["pageInfoVO"]["totalCount"], 글들)


# ── 국세법령정보시스템 판례·결정례 (JSON) ─────────────────────────────

def 판례결정례():
    주소 = "https://taxlaw.nts.go.kr/action.do"
    # 사이트 화면(USEPDI001M.do)이 보내는 요청과 같다. 등록일자 최신순, 문서구분 전체.
    조건 = {"collectionName": "precedent,precedent_gr", "sortField": "FRS_RGT_DTM/DESC",
          "startCount": 1, "viewCount": 10,
          "dcmClCdCtl": ["001_05", "001_06", "001_07", "001_08", "001_09", "001_10", "003_01"]}
    응답 = requests.post(주소, data={"actionId": "ASIPDI002PR01",
                                  "paramData": json.dumps(조건)}, headers=헤더, timeout=20)
    결과 = 응답.json()["data"]["ASIPDI002PR01"]
    전체 = sum(int(c["count"]) for c in 결과["top"][0]["categoryMap"]["SUB_ID_CATEGORY"])

    글들 = []
    for 항목 in 결과["body"]:
        문서 = 항목["dcm"]
        등록일 = 문서["FRS_RGT_DTM"][:8]
        요지 = 태그벗기기(문서.get("GIST_CNTN", ""))      # 목록에 오는 요지 발췌
        글들.append({"날짜": f"{등록일[:4]}-{등록일[4:6]}-{등록일[6:]}",
                    "제목": f"[{문서['NTST_DCM_CL_NM']}] {문서['TTL']}",
                    "본문길이": len(요지),
                    "원문": f"https://taxlaw.nts.go.kr/pd/USEPDA002P.do?ntstDcmId={문서['DOC_ID']}"})

    return 정리("국세법령정보시스템 판례·결정례", 주소, 응답.status_code, 전체, 글들)


def 정리(이름, 주소, 상태, 전체, 글들):
    최신 = max(글들, key=lambda g: g["날짜"]) if 글들 else None
    return {
        "소스": 이름,
        "요청주소": 주소,
        "응답상태": 상태,
        "전체건수": int(str(전체).replace(",", "")) if 전체 is not None else None,
        "받은건수": len(글들),
        "최신글날짜": 최신["날짜"] if 최신 else None,
        "최신글제목": 최신["제목"] if 최신 else None,
        "본문길이_최소": min(g["본문길이"] for g in 글들) if 글들 else None,
        "본문길이_최대": max(g["본문길이"] for g in 글들) if 글들 else None,
        "G1_600자이상": sum(g["본문길이"] >= G1_기준 for g in 글들),
        "원문링크": (최신["원문"] if 최신 else None),
        "원문열림": (열리는지(최신["원문"]) if 최신 and 최신["원문"] else None),
        "글들": 글들,
    }


측정 = []
for 함수 in (lambda: 국세청("국세청 보도자료", 2201, 1028),
             lambda: 국세청("국세청 공지사항", 2207, 1011),
             홈택스,
             판례결정례):
    try:
        측정.append(함수())
    except Exception as e:                       # 한 곳이 실패해도 나머지는 잰다
        측정.append({"소스": getattr(함수, "__name__", "?"), "오류": repr(e)})

print(f"측정 시각: {datetime.now():%Y-%m-%d %H:%M}\n")
for m in 측정:
    if "오류" in m:
        print(f"■ {m['소스']}  — 실패: {m['오류']}\n")
        continue
    원문 = {True: "열림", False: "안 열림", None: "글마다 주소 없음"}[m["원문열림"]]
    print(f"■ {m['소스']}")
    print(f"  응답 {m['응답상태']} | 전체 {m['전체건수']:,}건 | 받은 {m['받은건수']}건 | 최신 {m['최신글날짜']}")
    print(f"  본문 {m['본문길이_최소']}~{m['본문길이_최대']}자 | 600자 이상 {m['G1_600자이상']}/{m['받은건수']}건")
    print(f"  원문 링크: {원문}")
    print(f"  최신 글: {m['최신글제목']}\n")

저장위치.parent.mkdir(exist_ok=True)
저장위치.write_text(json.dumps({"측정시각": datetime.now().isoformat(timespec="seconds"),
                             "결과": 측정}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"저장: {저장위치}")
