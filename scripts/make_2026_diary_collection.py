"""生成 2026 日记合集：收集 mi-notes 下标题形如 2026.M.D 的笔记，按创建时间排序、去重、按月分组。"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mi-notes")
OUT = ROOT / "分析" / "2026日记合集.md"
YEAR = 2026

TITLE_RE = re.compile(rf"^{YEAR}\.(\d{{1,2}})\.(\d{{1,2}})\.?$")
FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

entries = []
for md in ROOT.rglob("*.md"):
    if "assets" in md.parts or md.resolve() == OUT.resolve():
        continue
    text = md.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        continue
    fm = dict(re.findall(r'^(\w+): "(.*)"$', m.group(1), re.M))
    title = fm.get("title", "")
    tm = TITLE_RE.match(title)
    if not tm:
        continue
    month, day = int(tm.group(1)), int(tm.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        continue
    body = text[m.end():]
    # 去掉正文开头的标题行
    body = re.sub(rf"^\s*#\s*{re.escape(title)}\s*\n", "", body).strip()
    # 规范化用于去重：去掉所有空白与零宽字符
    norm = re.sub(r"[\s⁢​‌‍]+", "", body)
    created = fm.get("created", "")
    entries.append({
        "title": title, "created": created, "body": body,
        "norm": norm, "path": md, "month": month, "day": day,
    })

# 去重：同一规范化内容保留创建时间最早（路径最短）的一篇
seen = {}
dups = []
for e in entries:
    key = e["norm"]
    if key in seen:
        dups.append(e["path"].name)
        prev = seen[key]
        if (e["created"], len(str(e["path"]))) < (prev["created"], len(str(prev["path"]))):
            seen[key] = e
    else:
        seen[key] = e
entries = sorted(seen.values(), key=lambda e: e["created"])

lines = [f"# {YEAR} 年日记合集", ""]
if entries:
    lines.append(
        f"> 共 {len(entries)} 篇，{entries[0]['created'][:10].replace('-', '-')} → "
        f"{entries[-1]['created'][:10]}。由 mi-notes 中标题形如 `{YEAR}.M.D.` 的笔记提取、按创建时间排序、去重后生成。"
    )
lines.append(f"> 图片路径形如 `../assets/<digest>`，相对本文件所在目录可能失效，原文见仓库对应 md。")
lines.append("")
lines.append("")
month = None
for e in entries:
    if e["month"] != month:
        month = e["month"]
        cnt = sum(1 for x in entries if x["month"] == month)
        lines.append("")
        lines.append(f"## {YEAR} 年 {month} 月（{cnt} 篇）")
        lines.append("")
    dt = e["created"][5:16]  # MM-DD HH:MM
    lines.append(f"### {dt} · {e['title']}")
    lines.append("")
    lines.append(e["body"])
    lines.append("")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"写入 {OUT}：{len(entries)} 篇，去重移除 {len(dups)} 篇重复")
