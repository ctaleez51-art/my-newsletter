# 소스 후보 6곳 측정
#
# 하는 일: 소스마다 최신 글 10건을 받아 아래를 잰다. REPORT.md 소스 채택표의 근거로 쓴다.
#   - 응답 상태, 전체 건수, 받은 건수, 최신 글 날짜
#   - 글마다 본문 상태 라벨
#       정상          본문 칸을 찾았고 글자가 있음
#       이미지·첨부만  본문 칸은 있는데 글자가 거의 없고(30자 미만) 이미지나 첨부파일만 있음
#       빈 본문       본문 칸은 있는데 글자도 이미지·첨부도 없음
#       잘림          본문 칸(또는 본문 요청)을 찾지 못함 = 수집 실패
#   - 원문 링크: 최신 글의 원문 주소가 열리는지(응답 200)
# 판정 기준(측정 전에 정함): G3 응답 200, 본문 확보(정상 라벨이 1건 이상).
# 글자 수로 거르지는 않는다. 30자는 "글자가 없다"를 가리는 값이다(tax-notice-summary/2_clean_body.py 와 같음).
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
글자없음_기준 = 30
받을건수 = 10
저장위치 = Path(__file__).parent / "store" / "source_measure.json"
법령정보 = "https://taxlaw.nts.go.kr/action.do"


def 태그벗기기(원본):
    """HTML 에서 태그를 지우고 글자만 남긴다."""
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


def 라벨(본문칸, 이미지첨부=False):
    """본문칸: None 이면 본문 위치를 못 찾은 것. 문자열이면 HTML 이 섞인 본문."""
    if 본문칸 is None:
        return "잘림", 0
    글자 = len(태그벗기기(본문칸))
    if 글자 >= 글자없음_기준:
        return "정상", 글자
    if 이미지첨부 or re.search(r"<img", 본문칸, flags=re.I):
        return "이미지·첨부만", 글자
    return ("정상" if 글자 else "빈 본문"), 글자


def 글(날짜, 제목, 본문칸, 원문, 이미지첨부=False):
    상태, 글자 = 라벨(본문칸, 이미지첨부)
    return {"날짜": 날짜, "제목": 제목, "상태": 상태, "본문길이": 글자, "원문": 원문}


def 열리는지(주소):
    try:
        return requests.get(주소, headers=헤더, timeout=20).status_code == 200
    except requests.RequestException:
        return False


def 법령정보_요청(actionId, 조건):
    응답 = requests.post(법령정보, data={"actionId": actionId, "paramData": json.dumps(조건)},
                       headers=헤더, timeout=30)
    return 응답, 응답.json()["data"][actionId]


def 날짜(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


# ── 국세청 보도자료 / 공지사항 (같은 화면 구조) ──────────────────────────

def 국세청(이름, mi, bbsId):
    목록주소 = f"https://www.nts.go.kr/nts/na/ntt/selectNttList.do?mi={mi}&bbsId={bbsId}"
    응답 = requests.get(목록주소, headers=헤더, timeout=20)
    전체 = re.search(r'pge_totle">\s*전체<strong>([\d,]+)</strong>', 응답.text)
    줄들 = re.findall(
        r'data-id="(\d+)" title="([^"]*)"\s+class="nttInfoBtn".*?<td data-table="date">([\d.]+)</td>',
        응답.text, flags=re.S)

    글들 = []
    for 번호, 제목, 게시일 in 줄들[:받을건수]:
        원문 = f"https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi={mi}&bbsId={bbsId}&nttSn={번호}"
        상세 = requests.get(원문, headers=헤더, timeout=20).text
        # 본문은 bbsV_cont 부터 첨부파일 칸(bbsV_atchmnfl) 앞까지. 첨부가 없는 글은 이전·다음글 칸(bbsV_prne) 앞까지
        칸 = re.search(r'class="bbsV_cont">(.*?)<(?:div class="bbsV_atchmnfl"|ul class="bbsV_prne")',
                      상세, flags=re.S)
        글들.append(글(게시일.strip(".").replace(".", "-"), html.unescape(제목),
                    칸.group(1) if 칸 else None, 원문, 이미지첨부="bbsV_atchmnfl" in 상세))
        time.sleep(0.3)
    return 정리(이름, 목록주소, 응답.status_code, 전체.group(1) if 전체 else None, 글들)


# ── 기획재정부 세제실 보도·참고자료 ──────────────────────────────────

def 기재부():
    목록주소 = ("https://www.mofe.go.kr/nw/nes/nesdta.do?searchBbsId1=MOSFBBS_000000000028"
             "&menuNo=4010100&kwd1=%EC%84%B8%EC%A0%9C%EC%8B%A4&searchSilDeptId1=1051010"
             "&searchKeyword1=&searchCondition3=1")
    응답 = requests.get(목록주소, headers=헤더, timeout=20)
    전체 = re.search(r"총 게시글 : <span>([\d,]+)", 응답.text)
    줄들 = re.findall(r"fn_egov_select\('(MOSF_\d+)'\);\">([^<]*)</a>.*?<span class=\"date\">([\d.]+)</span>",
                    응답.text, flags=re.S)

    글들 = []
    for 글ID, 제목, 게시일 in 줄들[:받을건수]:
        원문 = ("https://www.mofe.go.kr/nw/nes/detailNesDtaView.do?searchBbsId1=MOSFBBS_000000000028"
              f"&searchNttId1={글ID}&menuNo=4010100")
        상세 = requests.get(원문, headers=헤더, timeout=20).text
        # 본문 칸(editorCont)부터 첨부파일 칸(fileInfo) 앞까지
        칸 = re.search(r'class="editorCont">(.*?)<div class="fileInfo', 상세, flags=re.S)
        글들.append(글(게시일.strip(".").replace(".", "-"), html.unescape(제목.strip()),
                    칸.group(1) if 칸 else None, 원문, 이미지첨부="FileDown.do" in 상세))
        time.sleep(0.3)
    return 정리("기획재정부 세제실", 목록주소, 응답.status_code, 전체.group(1) if 전체 else None, 글들)


# ── 홈택스 공지사항 (JSON, 본문이 목록과 함께 옴) ──────────────────────────

def 홈택스():
    주소 = ("https://hometax.go.kr/wqAction.do?actionId=ATXPPBAA001R01"
          "&screenId=UTXPPBAA26&popupYn=false&realScreenId=")
    보낼내용 = {"blrdNo": "1", "endDt": "", "schCntn": "", "schType": "all", "strtDt": "",
             "tbbsClsfCd": "all",
             "pageInfoVO": {"totalCount": "0", "pageSize": str(받을건수 + 3), "pageNum": "1"}}
    응답 = requests.post(주소, json=보낼내용, headers=헤더, timeout=20)
    결과 = 응답.json()

    글들 = []
    for 항목 in 결과["intgTbbsInqrDVOList"]:
        if 항목.get("prfcAnncMttrYn") == "Y":     # 맨 위 고정공지는 최신 글이 아니므로 뺀다
            continue
        글들.append(글(날짜(항목["lstAltDtm"]), 항목["tbbsTtl"],   # 홈택스 목록은 수정일 순
                    항목["tbbsCntn"] or "", None))             # 글마다 주소가 없다
    return 정리("홈택스 공지사항", 주소, 응답.status_code,
              결과["pageInfoVO"]["totalCount"], 글들[:받을건수])


# ── 국세법령정보시스템 판례·결정례 (JSON) ─────────────────────────────

def 판례결정례():
    # 사이트 화면(USEPDI001M.do)이 보내는 요청과 같다. 등록일자 최신순, 문서구분 전체.
    응답, 결과 = 법령정보_요청("ASIPDI002PR01", {
        "collectionName": "precedent,precedent_gr", "sortField": "FRS_RGT_DTM/DESC",
        "startCount": 1, "viewCount": 받을건수,
        "dcmClCdCtl": ["001_05", "001_06", "001_07", "001_08", "001_09", "001_10", "003_01"]})
    전체 = sum(int(c["count"]) for c in 결과["top"][0]["categoryMap"]["SUB_ID_CATEGORY"])

    글들 = []
    for 항목 in 결과["body"]:
        문서 = 항목["dcm"]
        # 전문은 원문 화면(USEPDA002P.do)이 부르는 요청으로 받는다. 판결문이 dcmHwpEditorDVOList 에 온다.
        try:
            _, 상세 = 법령정보_요청("ASIQTB002PR01", {"dcmDVO": {"ntstDcmId": 문서["DOC_ID"]}})
            본문칸 = "\n".join(e.get("dcmFleByte") or "" for e in 상세.get("dcmHwpEditorDVOList") or [])
            첨부 = bool(상세.get("cmnFleBrkdDVO") or 상세.get("fleDVOList"))
        except Exception:
            본문칸, 첨부 = None, False
        글들.append(글(날짜(문서["FRS_RGT_DTM"]), f"[{문서['NTST_DCM_CL_NM']}] {문서['TTL']}", 본문칸,
                    f"https://taxlaw.nts.go.kr/pd/USEPDA002P.do?ntstDcmId={문서['DOC_ID']}", 첨부))
        time.sleep(0.3)
    return 정리("국세법령정보시스템 판례·결정례", 법령정보, 응답.status_code, 전체, 글들)


# ── 국세법령정보시스템 최신개정법령 (JSON) ────────────────────────────

def 최신개정법령():
    # 사이트 화면(USESTI001M.do)이 보내는 요청과 같다. 공포일 최신순.
    응답, 결과 = 법령정보_요청("ASISTI001MR01", {"pageIndex": 1, "recordCountPerPage": 받을건수})

    글들 = []
    for 항목 in 결과["ltstRvsnStttDVOList"]:
        # 본문(개정이유·개정내용)은 원문 화면(USESTA003P.do)이 부르는 요청으로 받는다.
        try:
            _, 상세 = 법령정보_요청("ASISTA003PR01", {"ntstBscId": 항목["ntstBscId"],
                                                  "ntstBrkdId": 항목["ntstBrkdId"]})
            dvo = 상세.get("txaStttDVO")
            본문칸 = (dvo.get("ntstRvsnRsn") or "") + "\n" + (dvo.get("ntstRvsnCntn") or "") if dvo else ""
        except Exception:
            본문칸 = None
        글들.append(글(날짜(항목["ntstPmgDt"]), 항목["ltstRvsnNm"], 본문칸,
                    "https://taxlaw.nts.go.kr/st/USESTA003P.do"
                    f"?ntstBscId={항목['ntstBscId']}&ntstBrkdId={항목['ntstBrkdId']}"))
        time.sleep(0.3)
    return 정리("국세법령정보시스템 최신개정법령", 법령정보, 응답.status_code, 결과["recordCount"], 글들)


def 정리(이름, 주소, 상태, 전체, 글들):
    최신 = max(글들, key=lambda g: g["날짜"]) if 글들 else None
    라벨수 = {k: sum(g["상태"] == k for g in 글들) for k in ("정상", "이미지·첨부만", "빈 본문", "잘림")}
    원문열림 = 열리는지(최신["원문"]) if 최신 and 최신["원문"] else None
    return {
        "소스": 이름,
        "요청주소": 주소,
        "응답상태": 상태,
        "전체건수": int(str(전체).replace(",", "")) if 전체 is not None else None,
        "받은건수": len(글들),
        "최신글날짜": 최신["날짜"] if 최신 else None,
        "최신글제목": 최신["제목"] if 최신 else None,
        "라벨수": 라벨수,
        "원문링크": 최신["원문"] if 최신 else None,
        "원문열림": 원문열림,
        "판정": {"G3_응답200": 상태 == 200, "본문확보": 라벨수["정상"] > 0},
        "글들": 글들,
    }


측정 = []
for 이름, 함수 in (("국세청 보도자료", lambda: 국세청("국세청 보도자료", 2201, 1028)),
                 ("국세청 공지사항", lambda: 국세청("국세청 공지사항", 2207, 1011)),
                 ("홈택스 공지사항", 홈택스),
                 ("국세법령정보시스템 판례·결정례", 판례결정례),
                 ("기획재정부 세제실", 기재부),
                 ("국세법령정보시스템 최신개정법령", 최신개정법령)):
    try:
        측정.append(함수())
    except Exception as e:                       # 한 곳이 실패해도 나머지는 잰다
        측정.append({"소스": 이름, "오류": repr(e)})

print(f"측정 시각: {datetime.now():%Y-%m-%d %H:%M}\n")
for m in 측정:
    if "오류" in m:
        print(f"■ {m['소스']}  — 실패: {m['오류']}\n")
        continue
    원문 = {True: "열림", False: "안 열림", None: "글마다 주소 없음"}[m["원문열림"]]
    라벨 = " · ".join(f"{k} {v}" for k, v in m["라벨수"].items())
    판정 = "통과" if all(m["판정"].values()) else "탈락"
    print(f"■ {m['소스']}  [{판정}]")
    print(f"  응답 {m['응답상태']} | 전체 {m['전체건수']:,}건 | 받은 {m['받은건수']}건 | 최신 {m['최신글날짜']}")
    print(f"  본문 상태: {라벨}")
    print(f"  원문 링크: {원문}")
    print(f"  최신 글: {m['최신글제목']}\n")

저장위치.parent.mkdir(exist_ok=True)
저장위치.write_text(json.dumps({"측정시각": datetime.now().isoformat(timespec="seconds"),
                             "판정기준": {"G3_응답200": "목록 요청 응답 200",
                                      "본문확보": "받은 글 중 '정상' 라벨이 1건 이상"},
                             "결과": 측정}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"저장: {저장위치}")
