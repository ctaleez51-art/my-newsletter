# 세법 뉴스레터 — LangGraph 워크플로
#
# 교안 §2 뼈대: State 와 빈 노드 다섯 개. 노드마다 하나씩 실제 코드로 갈아 끼운다.
# 이 파일은 불러오기만 해도 안전해야 한다(교안 §8). 실행은 맨 아래 __main__ 또는 run.py 에서만.

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class Brief(TypedDict):
    hours:     int                              # 수집 time window(시간)
    collected: list                             # ① 수집한 글
    picked:    list                             # ② 선별해 남긴 글
    drafted:   Annotated[list, operator.add]    # ③ 취재한 초안 — 워커들이 나눠 채운다
    log:       Annotated[list, operator.add]    # 무슨 일이 있었는지


def collect(s: dict) -> dict:                  # ① 자료 수집
    return {"collected": [], "log": [f"① 수집   {s['hours']}시간 time window · 0건 (빈 노드)"]}


def select(s: dict) -> dict:                   # ② 중요도 선별
    return {"picked": [], "log": [f"② 선별   {len(s['collected'])} → 0건 (빈 노드)"]}


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


INIT = {"hours": 24,
        "collected": [], "picked": [], "drafted": [], "log": []}


def run():
    return build().compile().invoke(INIT)


if __name__ == "__main__":
    for line in run()["log"]:
        print(line)
