#!/usr/bin/env python3
"""增量同步的比对与校验层 —— 纯本地计算，不联网、不写笔记。

和 mi_note_export.py 解耦：导出器负责网络与写盘，本模块只回答两个问题
    1. 跟云端索引比，哪些笔记需要重新拉正文？   plan_sync()
    2. 本地这份备份自身是否完好？               verify_local()

用法:
    py -3 mi_note_verify.py                  # 体检本地备份（不联网）
    py -3 mi_note_verify.py --deep           # 额外解析全部缓存 JSON，查有无损坏
    py -3 mi_note_verify.py --rebuild-state  # 清单丢了或坏了，从 .cache 重建

为什么要单独维护一份清单：逐条打开 .cache/<id>.json 读 modifyDate 来比对，
是 O(笔记数) 次文件打开 + JSON 解析，1000 条无所谓，10 万条要读几百 MB。
清单把
    id -> [modifyDate, 相对输出路径]
集中存进 .cache/_sync.json，比对就退化成「一次读盘 + 内存字典查找」，
IO 次数与笔记数无关。判定只看 modifyDate，永不比对正文内容。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime

STATE_FILE = "_sync.json"
STATE_VERSION = 2


class SyncState:
    """.cache/_sync.json —— 上次同步留下的清单。

    notes 的值故意用两元素列表而不是字典，10 万条时能省下约一半体积
    （紧凑写法下每条约 40 字节，10 万条约 4 MB，一次读取 ~100ms）。
    """

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.notes: dict[str, list] = {}   # id -> [modifyDate:int, relpath:str]
        self.last_sync = ""
        self.rebuilt = False               # 本次是否从缓存重建过

    @classmethod
    def load(cls, cache: pathlib.Path, quiet: bool = False) -> "SyncState":
        """读清单；缺失、过期或损坏时从 .cache 重建一份。"""
        st = cls(cache / STATE_FILE)
        raw: dict = {}
        if st.path.exists():
            try:
                raw = json.loads(st.path.read_text(encoding="utf-8"))
            except Exception as ex:
                print(f"  ! 清单损坏（{ex}），改从缓存重建", file=sys.stderr)
        if raw.get("version") == STATE_VERSION and isinstance(raw.get("notes"), dict):
            st.notes = {k: list(v) for k, v in raw["notes"].items()}
            st.last_sync = raw.get("lastSync", "")
            return st
        n = st.rebuild(cache)
        if n and not quiet:
            print(f"  清单缺失或版本过期，已从缓存重建 {n} 条（本次会重写一遍 Markdown）")
        return st

    def rebuild(self, cache: pathlib.Path) -> int:
        """从 .cache 的原始 JSON 重建清单，只在清单丢失时走一次。

        重建拿不到每条笔记的输出路径（那由导出器规划），relpath 先留空；
        留空意味着导出器无法确认文件位置，本次会照常重写 Markdown，
        写完清单里就有路径了，下次就能跳过。
        """
        self.notes = {}
        for f in cache.glob("*.json"):
            if f.name.startswith("_"):      # _folders / _sync 是元数据，不是笔记
                continue
            try:
                m = int(json.loads(f.read_text(encoding="utf-8")).get("modifyDate") or 0)
            except Exception:
                continue  # 坏缓存不入清单，导出器会当新笔记重新拉
            self.notes[f.stem] = [m, ""]
        self.rebuilt = True
        return len(self.notes)

    def save(self, **extra) -> None:
        """原子写出：先写临时文件再替换，中途中断不会留下半份清单。"""
        payload = {
            "version": STATE_VERSION,
            "lastSync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **extra,
            "notes": self.notes,
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(self.path)

    def modify_of(self, nid: str) -> int | None:
        rec = self.notes.get(nid)
        return rec[0] if rec else None

    def path_of(self, nid: str) -> str:
        rec = self.notes.get(nid)
        return (rec[1] if rec and len(rec) > 1 else "") or ""

    def record(self, nid: str, modify, relpath: str) -> None:
        self.notes[nid] = [int(modify or 0), relpath]

    def forget(self, nid: str) -> None:
        self.notes.pop(nid, None)


class SyncPlan:
    """一次同步的比对结果，四组互不相交的笔记 id。"""

    __slots__ = ("fresh", "updated", "unchanged", "deleted")

    def __init__(self, fresh: list[str], updated: list[str],
                 unchanged: list[str], deleted: list[str]):
        self.fresh, self.updated = fresh, updated
        self.unchanged, self.deleted = unchanged, deleted

    @property
    def refetch(self) -> set[str]:
        """需要重新拉正文的 id：新增的 + 云端改过的。"""
        return set(self.fresh) | set(self.updated)

    def summary(self) -> str:
        return (f"增量: 新增 {len(self.fresh)} 条, 有改动 {len(self.updated)} 条, "
                f"未变 {len(self.unchanged)} 条（复用缓存，不发正文请求）")


def plan_sync(entries: list[dict], state: SyncState, force: bool = False,
              detect_deleted: bool = True) -> SyncPlan:
    """把云端索引条目分成 新增 / 有改动 / 未变 / 云端已删除。

    只比 modifyDate：云端的比清单里记的更新才需要重拉正文。等值或更旧都视为
    未变 —— 云端时间戳倒退只可能是接口异常，重拉也换不来更可信的数据。
    全程内存字典查找，不读缓存、不比对内容。

    detect_deleted=False 用于 --limit 试跑：那时索引只截取了一部分，
    「清单里有而索引里没有」并不代表云端删了。
    """
    fresh: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    known = state.notes
    seen: set[str] = set()
    for e in entries:
        nid = e["id"]
        seen.add(nid)
        rec = known.get(nid)
        if rec is None:
            fresh.append(nid)
        elif force or int(e.get("modifyDate") or 0) > rec[0]:
            updated.append(nid)
        else:
            unchanged.append(nid)
    deleted = sorted(known.keys() - seen) if detect_deleted else []
    return SyncPlan(fresh, updated, unchanged, deleted)


def verify_local(out: pathlib.Path, state: SyncState, deep: bool = False) -> list[str]:
    """体检本地备份，返回问题列表（空列表表示一切正常）。

    浅查只做存在性检查（每条两次 stat）；deep=True 会额外解析每份缓存 JSON
    找出损坏的副本，代价是完整读一遍缓存。
    """
    problems: list[str] = []
    cache = out / ".cache"

    if not state.notes:
        problems.append("清单为空：还没同步过，或 .cache 里没有任何笔记 JSON")
        return problems

    no_md, no_cache, no_path = [], [], 0
    for nid, rec in state.notes.items():
        rel = rec[1] if len(rec) > 1 else ""
        if not rel:
            no_path += 1
        elif not (out / rel).exists():
            no_md.append(f"{nid} -> {rel}")
        if not (cache / f"{nid}.json").exists():
            no_cache.append(nid)

    if no_path:
        problems.append(f"{no_path} 条尚未记录输出路径（清单是重建来的，下次同步会补上）")
    if no_md:
        problems.append(f"{len(no_md)} 条的 Markdown 不存在，例如 {', '.join(no_md[:3])}")
    if no_cache:
        problems.append(f"{len(no_cache)} 条缺少缓存 JSON，例如 {', '.join(no_cache[:3])}"
                        f"（--offline 重建会丢这些笔记）")

    # 只在存放笔记的子目录里找遗留文件：备份目录根下的 README.md 之类是用户
    # 自己的文件，不是导出产物，不该报成问题（导出器的 --prune 同样不碰它们）。
    known_paths = {rec[1] for rec in state.notes.values() if len(rec) > 1 and rec[1]}
    note_dirs = {(out / rel).parent for rel in known_paths}
    orphans = [p for p in out.rglob("*.md")
               if p.parent in note_dirs and p.relative_to(out).as_posix() not in known_paths]
    if orphans:
        problems.append(f"{len(orphans)} 个 Markdown 在笔记目录内但不在清单里（改名或云端"
                        f"已删除的遗留，--prune 可清理），"
                        f"例如 {orphans[0].relative_to(out).as_posix()}")

    if deep:
        bad = []
        for nid in state.notes:
            f = cache / f"{nid}.json"
            if not f.exists():
                continue
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except Exception as ex:
                bad.append(f"{nid}: {ex}")
        if bad:
            problems.append(f"{len(bad)} 份缓存 JSON 解析失败，例如 {bad[0]}"
                            f"（导出器会把它们当新笔记重新拉）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="小米笔记备份的增量比对与校验（不联网）")
    ap.add_argument("-o", "--out", default="mi-notes", help="备份目录（默认 ./mi-notes）")
    ap.add_argument("--deep", action="store_true",
                    help="深度校验：解析全部缓存 JSON，找出损坏的副本")
    ap.add_argument("--rebuild-state", action="store_true",
                    help="强制从 .cache 重建清单（清单损坏或手工改过缓存后用）")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    cache = out / ".cache"
    if not cache.is_dir():
        print(f"找不到备份缓存目录: {cache}\n请先用 mi_note_export.py 导出一次。",
              file=sys.stderr)
        return 1

    if args.rebuild_state:
        state = SyncState(cache / STATE_FILE)
        n = state.rebuild(cache)
        state.save(notes=n, rebuilt=True)
        print(f"已从缓存重建清单: {n} 条 -> {state.path}")
        print("下次同步会重写一遍 Markdown 以补齐路径记录。")
        return 0

    state = SyncState.load(cache)
    print(f"备份目录: {out.resolve()}")
    print(f"清单: {len(state.notes)} 条" + (f", 上次同步 {state.last_sync}"
                                           if state.last_sync else ""))

    problems = verify_local(out, state, deep=args.deep)
    if not problems:
        print("校验通过，本地备份完好。")
        return 0
    print(f"发现 {len(problems)} 类问题:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
