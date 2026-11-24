"""스크럼 문서를 칸반 보드의 이슈로 올리거나 갱신한다.

    python3 scripts/scrum_sync.py docs/scrum/2026-11-25.md      # 데일리
    python3 scripts/scrum_sync.py docs/scrum/2026-W48.md        # 주간
    python3 scripts/scrum_sync.py docs/scrum/2026-11-25.md --close

- 이슈 제목은 문서 첫 줄(`# 데일리 스크럼 · 2026-11-25 (수)`)에서 만든다: `[데일리 스크럼] 2026-11-25 (수)`
- 같은 제목의 이슈가 있으면 본문만 고치고, 없으면 만들어 보드에 올린다 (종류, 날짜, 주차 필드).
- 데일리 이슈는 그 주의 주간 이슈가 있으면 하위 이슈로 붙인다.
- 문서 안의 상대 링크는 main 브랜치의 GitHub 링크로 바꾼다.
- gh CLI(project 권한)가 필요하다.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

REPO = "Sen-Yu/recipe-chatbot-v2"
OWNER = "Sen-Yu"
PROJECT = 1
PROJECT_ID = "PVT_kwHOAs83gc4BtBQZ"
FIELDS = {
    "kind": "PVTSSF_lAHOAs83gc4BtBQZzhrMu6g",
    "date": "PVTF_lAHOAs83gc4BtBQZzhrMu70",
    "week": "PVTIF_lAHOAs83gc4BtBQZzhrMu74",
}
KIND = {"daily": "84f60d80", "weekly": "6a3b0f14"}
LABEL = {"daily": "scrum:daily", "weekly": "scrum:weekly"}
BLOB = f"https://github.com/{REPO}/blob/main/"


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
        resolved = Path(*[p for p in (base / target).parts])
        parts: list[str] = []
        for p in resolved.parts:
            if p == "..":
                parts.pop()
            elif p != ".":
                parts.append(p)
        return f"]({BLOB}{'/'.join(parts)})"

    body = re.sub(r"\]\(([^)]+)\)", absolute, body).strip()
    body += f"\n\n---\n원본 문서: [{path.name}]({BLOB}{base.as_posix()}/{path.name})"
    return kind, title, body, day


def find_issue(title: str, prefix: bool = False) -> dict | None:
    """제목이 같은(prefix=True면 그 말로 시작하는) 이슈. 검색 색인 지연을 피하려고 목록에서 직접 찾는다."""
    fields = "number,title,url,id"
    out = gh("issue", "list", "--repo", REPO, "--state", "all", "--json", fields, "--limit", "200")
    match = (lambda t: t.startswith(title)) if prefix else (lambda t: t == title)
    return next((i for i in json.loads(out) if match(i["title"])), None)


def week_iteration(day: dt.date) -> str | None:
    q = "{node(id:\"" + FIELDS["week"] + "\"){... on ProjectV2IterationField"
    data = gql(q + "{configuration{iterations{id startDate duration}}}}}")
    for it in data["node"]["configuration"]["iterations"]:
        start = dt.date.fromisoformat(it["startDate"])
        if start <= day < start + dt.timedelta(days=it["duration"]):
            return it["id"]
    return None


def edit(item: str, field: str, *args: str) -> None:
    gh("project", "item-edit", "--id", item, "--project-id", PROJECT_ID, "--field-id", FIELDS[field], *args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--close", action="store_true", help="검토가 끝나 이슈를 닫는다")
    args = ap.parse_args()
    kind, title, body, day = parse(Path(args.doc))

    issue = find_issue(title)
    if issue:
        gh("issue", "edit", str(issue["number"]), "--repo", REPO, "--body", body)
        print(f"갱신: #{issue['number']} {title}")
    else:
        url = gh("issue", "create", "--repo", REPO, "--title", title, "--body", body, "--label", LABEL[kind])
        issue = find_issue(title) or {"url": url}
        added = gh("project", "item-add", str(PROJECT), "--owner", OWNER, "--url", url, "--format", "json")
        item = json.loads(added)["id"]
        edit(item, "kind", "--single-select-option-id", KIND[kind])
        edit(item, "date", "--date", day.isoformat())
        if (week := week_iteration(day)):
            edit(item, "week", "--iteration-id", week)
        print(f"만듦: {url}")
        if kind == "daily":
            y, w, _ = day.isocalendar()
            parent = find_issue(f"[주간 스크럼] {y}-W{w:02d}", prefix=True)
            if parent:
                child = find_issue(title)["id"]
                gql(f'mutation{{addSubIssue(input:{{issueId:"{parent["id"]}",subIssueId:"{child}"}}){{issue{{number}}}}}}')
                print(f"주간 스크럼 #{parent['number']}의 하위 이슈로 붙임")
    if args.close:
        gh("issue", "close", str(find_issue(title)["number"]), "--repo", REPO)
        print("닫음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
