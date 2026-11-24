"""스크럼 문서를 스크럼 보드의 이슈로 올리거나 갱신한다.

    python3 scripts/scrum_sync.py docs/scrum/2026-W48.md                 # 주간 → 주간 스크럼 보드
    python3 scripts/scrum_sync.py docs/scrum/2026-11-25.md               # 데일리 → 데일리 스크럼 보드
    python3 scripts/scrum_sync.py docs/scrum/2026-11-25.md --status review  # 새벽 작업 검토 대기
    python3 scripts/scrum_sync.py docs/scrum/2026-11-25.md --close  # 검토 완료(데일리)·마감(주간), 이슈 닫음

- 이슈 제목은 문서 첫 줄(`# 데일리 스크럼 · 2026-11-25 (수)`)에서 만든다: `[데일리 스크럼] 2026-11-25 (수)`
- 같은 제목의 이슈가 있으면 본문만 고치고, 없으면 만들어 해당 보드에 올린다 (Status, 날짜, 주차).
  '계획 안 함'으로 닫힌 옛 이슈는 무시한다.
- 데일리 이슈는 그 주의 주간 이슈의 하위 이슈로 붙인다.
- 문서 안의 상대 링크는 main 브랜치의 GitHub 링크로 바꾼다.
- gh CLI(project 권한)가 필요하다. 업무 카드는 업무 보드(1번)에서 따로 관리한다.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

REPO = "Sen-Yu/recipe-chatbot-v2"
OWNER = "Sen-Yu"
BLOB = f"https://github.com/{REPO}/blob/main/"
BOARDS = {
    "daily": {
        "number": 2,
        "id": "PVT_kwHOAs83gc4BtBgs",
        "label": "scrum:daily",
        "status": "PVTSSF_lAHOAs83gc4BtBgszhrM4Xc",
        "options": {"open": "01fa6eb8", "review": "21ad6c37", "done": "e85e9f24"},
        "week": "PVTIF_lAHOAs83gc4BtBgszhrM4e8",
        "date": "PVTF_lAHOAs83gc4BtBgszhrM4d0",
    },
    "weekly": {
        "number": 3,
        "id": "PVT_kwHOAs83gc4BtBg1",
        "label": "scrum:weekly",
        "status": "PVTSSF_lAHOAs83gc4BtBg1zhrM4gg",
        "options": {"open": "86d50dc3", "done": "54ffb86e"},
        "week": "PVTIF_lAHOAs83gc4BtBg1zhrM4nA",
    },
}


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def gql(query: str) -> dict:
    return json.loads(gh("api", "graphql", "-f", f"query={query}"))["data"]


def parse(path: Path) -> tuple[str, str, str, dt.date]:
    """(종류, 이슈 제목, 본문, 기준 날짜)"""
    text = path.read_text(encoding="utf-8")
    head, body = text.split("\n", 1)
    m = re.match(r"# (데일리|주간) 스크럼 · (\S+)(.*)", head)
    if not m:
        raise SystemExit(f"첫 줄 형식이 다릅니다: {head}")
    kind = "daily" if m.group(1) == "데일리" else "weekly"
    title = f"[{m.group(1)} 스크럼] {m.group(2)}{m.group(3)}"
    if kind == "daily":
        day = dt.date.fromisoformat(m.group(2))
    else:
        year, week = m.group(2).split("-W")
        day = dt.date.fromisocalendar(int(year), int(week), 1)
    base = path.parent.relative_to(Path.cwd()) if path.is_absolute() else path.parent

    def absolute(link: re.Match) -> str:
        target = link.group(1)
        if re.match(r"https?://|#", target):
            return link.group(0)
        parts: list[str] = []
        for p in (base / target).parts:
            if p == "..":
                parts.pop()
            elif p != ".":
                parts.append(p)
        return f"]({BLOB}{'/'.join(parts)})"

    body = re.sub(r"\]\(([^)]+)\)", absolute, body).strip()
    body += f"\n\n---\n원본 문서: [{path.name}]({BLOB}{base.as_posix()}/{path.name})"
    return kind, title, body, day


def find_issue(title: str, prefix: bool = False) -> dict | None:
    """제목이 같은(prefix=True면 그 말로 시작하는) 이슈. '계획 안 함'으로 닫힌 이슈는 뺀다."""
    fields = "number,title,url,id,stateReason"
    out = gh("issue", "list", "--repo", REPO, "--state", "all", "--json", fields, "--limit", "300")
    match = (lambda t: t.startswith(title)) if prefix else (lambda t: t == title)
    live = [i for i in json.loads(out) if i.get("stateReason") != "NOT_PLANNED"]
    return next((i for i in live if match(i["title"])), None)


def week_iteration(field_id: str, day: dt.date) -> str | None:
    q = '{node(id:"' + field_id + '"){... on ProjectV2IterationField'
    data = gql(q + "{configuration{iterations{id startDate duration}}}}}")
    for it in data["node"]["configuration"]["iterations"]:
        start = dt.date.fromisoformat(it["startDate"])
        if start <= day < start + dt.timedelta(days=it["duration"]):
            return it["id"]
    return None


def item_id(board: dict, issue_number: int) -> str | None:
    q = '{repository(owner:"%s",name:"%s"){issue(number:%d){projectItems(first:10){nodes{id project{id}}}}}}'
    owner, name = REPO.split("/")
    nodes = gql(q % (owner, name, issue_number))["repository"]["issue"]["projectItems"]["nodes"]
    return next((n["id"] for n in nodes if n["project"]["id"] == board["id"]), None)


def set_status(board: dict, item: str, key: str) -> None:
    gh("project", "item-edit", "--id", item, "--project-id", board["id"], "--field-id", board["status"],
       "--single-select-option-id", board["options"][key])  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--status", choices=["open", "review", "done"], help="보드 칸 (review는 데일리만)")
    ap.add_argument("--close", action="store_true", help="검토 완료·마감으로 옮기고 이슈를 닫는다")
    args = ap.parse_args()
    kind, title, body, day = parse(Path(args.doc))
    board = BOARDS[kind]

    issue = find_issue(title)
    if issue:
        gh("issue", "edit", str(issue["number"]), "--repo", REPO, "--body", body)
        item = item_id(board, issue["number"])
        print(f"갱신: #{issue['number']} {title}")
    else:
        label = board["label"]
        url = gh("issue", "create", "--repo", REPO, "--title", title, "--body", body, "--label", label)
        num = str(board["number"])
        added = gh("project", "item-add", num, "--owner", OWNER, "--url", url, "--format", "json")
        item = json.loads(added)["id"]
        set_status(board, item, "open")
        if "date" in board:
            gh("project", "item-edit", "--id", item, "--project-id", board["id"], "--field-id", board["date"],
               "--date", day.isoformat())  # fmt: skip
        if week := week_iteration(board["week"], day):
            gh("project", "item-edit", "--id", item, "--project-id", board["id"], "--field-id", board["week"],
               "--iteration-id", week)  # fmt: skip
        issue = find_issue(title)
        print(f"만듦: {url}")
        if kind == "daily":
            y, w, _ = day.isocalendar()
            parent = find_issue(f"[주간 스크럼] {y}-W{w:02d}", prefix=True)
            if parent:
                link = f'addSubIssue(input:{{issueId:"{parent["id"]}",subIssueId:"{issue["id"]}"}})'
                gql(f"mutation{{{link}{{issue{{number}}}}}}")
                print(f"주간 스크럼 #{parent['number']}의 하위 이슈로 붙임")
                # GitHub가 하위 이슈를 부모의 보드(주간)에도 자동으로 올리므로 빼 둔다
                weekly = BOARDS["weekly"]
                if stray := item_id(weekly, issue["number"]):
                    gh("project", "item-delete", str(weekly["number"]), "--owner", OWNER, "--id", stray)

    if args.status and item:
        set_status(board, item, args.status)
    if args.close:
        if item:
            set_status(board, item, "done")
        gh("issue", "close", str(issue["number"]), "--repo", REPO)
        print("닫음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
