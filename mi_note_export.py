#!/usr/bin/env python3
"""小米云服务笔记导出器 —— 只读导出为 Markdown。

安全约束：全程仅发起 GET 请求，代码中不存在任何写入/删除类接口调用，
因此不可能修改或删除云端笔记。

用法:
    py -3 mi_note_export.py                    # 使用同目录 cookie.txt，导出到 ./mi-notes
    py -3 mi_note_export.py --limit 5          # 先小批量试跑
    py -3 mi_note_export.py -o D:/backup -r 3  # 指定输出目录并降低请求速率

重复运行即增量同步：只拉新增的和云端 modifyDate 变新的笔记，其余复用 .cache。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from mi_note_verify import SyncState, plan_sync

BASE = "https://i.mi.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
# 正文中的附件占位: ![](https://com.miui.notes/image/<digest>?size=full)
# digest 实测有三种形态：40 位 hex、img_<毫秒>_<序号>.jpg、hex.mp3，故不能只认 hex
IMG_RE = re.compile(r"https://com\.miui\.notes/[\w.-]+/([^?)\s\"']+)(\?[^)\s\"']*)?")
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
            "image/webp": ".webp", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            "audio/amr": ".amr", "video/mp4": ".mp4"}
BUILTIN_FOLDERS = {"0": "未分类"}
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class Client:
    """带限速与重试的只读 HTTP 客户端。"""

    def __init__(self, cookie: str, rate: float, retries: int = 3):
        self.s = requests.Session()
        self.s.headers.update({
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cookie": cookie,
            "referer": f"{BASE}/note",
            "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": UA,
        })
        self._gap = 1.0 / rate if rate > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()
        self.retries = retries

    def _throttle(self) -> None:
        """全局令牌间隔，多线程下也保证整体请求速率不超过 rate。"""
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                time.sleep(wait)
                now = self._next
            self._next = now + self._gap

    def get(self, path: str, **params) -> requests.Response:
        last = None
        for attempt in range(self.retries):
            self._throttle()
            params["ts"] = int(time.time() * 1000)
            try:
                r = self.s.get(BASE + path, params=params, timeout=30)
                # 429/5xx 视为限流或临时故障，退避重试
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r
            except Exception as e:  # noqa: BLE001 - 网络层异常统一退避重试
                last = e
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt * 1.5)
        raise RuntimeError(f"GET {path} 失败: {last}")

    def json(self, path: str, **params) -> dict:
        d = self.get(path, **params).json()
        if d.get("result") != "ok" or d.get("code") != 0:
            raise RuntimeError(f"接口返回异常 {path}: {d.get('code')} {d.get('description')}")
        return d["data"]


def _index_pass(c: Client, entries: dict[str, dict],
                folders: dict[str, str]) -> tuple[int, int]:
    """按 syncTag 翻完一遍全部分页，并入 entries，返回（本遍新出现条数, 页数）。

    folders 并非只在首页返回，需逐页收集；中间页可能返回不足 limit 条，
    必须以 lastPage 而非返回条数作为终止条件。
    """
    new, tag, page = 0, None, 0
    while True:
        params = {"limit": 200}
        if tag:
            params["syncTag"] = tag
        d = c.json("/note/v2/full/page", **params)
        for e in d.get("entries") or []:
            old = entries.get(e["id"])
            if old is None:
                new += 1
            # 同一条可能在不同遍里带不同 modifyDate，保留更新的那份
            if old is None or int(e.get("modifyDate") or 0) >= int(old.get("modifyDate") or 0):
                entries[e["id"]] = e
        for f in d.get("folders") or []:
            folders[str(f["id"])] = f.get("subject") or f"folder_{f['id']}"
        page += 1
        tag = d.get("syncTag")
        if d.get("lastPage") or not tag:
            break
        if page > 2000:  # 防御性上限，避免 syncTag 异常导致死循环
            print("  ! 页数超过上限，提前停止", file=sys.stderr)
            break
    return new, page


def fetch_index(c: Client, passes: int = 3) -> tuple[list[dict], dict[str, str]]:
    """拉取全部笔记条目与文件夹，重复翻页直到条目集合不再增长。

    为什么要翻多遍：实测同一账号连续全量翻页，返回的条目数并不稳定
    （同一天内测到 792 / 870 / 915 / 918 条，逐次变多），单遍翻页会静默漏掉
    笔记 —— 本仓库最初那份 792 条的备份就因此漏了 100 多条真实笔记。
    备份宁可多花几个请求，也不能漏，所以这里取多遍的并集，直到某一遍
    不再出现新 id 为止。
    """
    entries: dict[str, dict] = {}
    folders = dict(BUILTIN_FOLDERS)
    for i in range(1, max(1, passes) + 1):
        new, pages = _index_pass(c, entries, folders)
        print(f"  第 {i} 遍翻页: {pages} 页, 新出现 {new} 条 "
              f"(累计 {len(entries)} 条, {len(folders)} 个文件夹)")
        if new == 0:
            break
    else:
        print(f"  ! 翻了 {passes} 遍仍在发现新笔记，索引尚未收敛；"
              f"本次结果可能仍不完整，建议稍后再跑一次", file=sys.stderr)
    return list(entries.values()), folders


def fetch_note(c: Client, note_id: str, cache: pathlib.Path,
               force: bool = False) -> dict:
    """拉取单条笔记详情，带本地缓存以支持断点续传。

    force=True 时忽略缓存重新拉取；由增量比对结果决定，见 mi_note_verify.plan_sync()。
    """
    f = cache / f"{note_id}.json"
    if f.exists() and not force:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass  # 缓存损坏则重新拉取
    entry = c.json(f"/note/v2/note/{note_id}/")["entry"]
    f.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    return entry


def fetch_asset(c: Client, file_id: str, mime: str, assets: pathlib.Path,
                digest: str, offline: bool = False) -> str | None:
    """下载附件到 assets/，返回文件名；已存在则跳过。

    涂鸦类附件的 digest 形如 doodle_tmp_<ts>/page_0.mpf，带路径分隔符，必须净化。
    部分 digest 自带扩展名（img_xxx.jpg、hex.mp3），此时不再重复追加。
    """
    ext = MIME_EXT.get(mime, ".bin")
    stem = ILLEGAL.sub("_", digest)
    name = stem if stem.lower().endswith(ext) else stem + ext
    dst = assets / name
    if dst.exists() and dst.stat().st_size > 0:
        return name
    if offline:
        return None
    r = c.get("/file/full", type="note_img", fileid=file_id)
    if not r.content:
        return None
    dst.write_bytes(r.content)
    return name


def safe_name(text: str, fallback: str, maxlen: int = 60) -> str:
    text = ILLEGAL.sub("_", (text or "").strip()).strip(". ")
    text = re.sub(r"\s+", " ", text)
    return text[:maxlen] or fallback


def note_title(e: dict) -> str:
    """取笔记标题：extraInfo.title 优先，其次 subject，最后退到摘要首行。"""
    try:
        extra = json.loads(e.get("extraInfo") or "{}")
    except Exception:
        extra = {}
    return (extra.get("title") or e.get("subject")
            or (e.get("snippet") or "")[:30] or "").strip()


def plan_paths(entries: list[dict], folders: dict[str, str],
               out: pathlib.Path) -> dict[str, pathlib.Path]:
    """预先规划每条笔记的输出路径。

    文件名默认就用 <标题>.md，只有当同一目录下标题撞车时，才给这一组
    全部退回 <日期>-<标题>.md；若同日同标题仍冲突再追加序号。
    必须在并发写入之前一次算完，因为「是否冲突」是全局信息。
    """
    def folder_dir(e: dict) -> pathlib.Path:
        folder = folders.get(str(e.get("folderId", 0)), f"folder_{e.get('folderId')}")
        return out / safe_name(folder, "未分类", 40)

    def date_of(e: dict) -> str:
        try:
            return datetime.fromtimestamp(
                int(e.get("createDate") or 0) / 1000).strftime("%Y%m%d")
        except Exception:
            return ""

    groups: dict[tuple[str, str], list[tuple[dict, pathlib.Path, str]]] = {}
    for e in entries:
        d = folder_dir(e)
        t = safe_name(note_title(e), e["id"])
        groups.setdefault((d.as_posix().lower(), t.lower()), []).append((e, d, t))

    plan: dict[str, pathlib.Path] = {}
    taken: set[str] = set()
    for items in groups.values():
        collide = len(items) > 1
        for e, d, t in items:
            stem = t
            if collide:
                dt = date_of(e)
                stem = f"{dt}-{t}" if dt else t
            cand, n = stem, 1
            while (d / f"{cand}.md").as_posix().lower() in taken:
                n += 1
                cand = f"{stem}-{n}"
            taken.add((d / f"{cand}.md").as_posix().lower())
            plan[e["id"]] = d / f"{cand}.md"
    return plan


def to_markdown(entry: dict, folders: dict[str, str], img_map: dict[str, str]) -> str:
    """生成带 YAML frontmatter 的 Markdown。"""
    title = note_title(entry)
    content = entry.get("content") or entry.get("snippet") or ""

    # 正文中的云端附件地址替换为本地相对路径（连同 ?size=full 查询串一起替换掉）
    def repl(m: re.Match) -> str:
        ref = m.group(1).lower()
        local = img_map.get(ref) or img_map.get(ref.rsplit(".", 1)[0])
        return f"../assets/{local}" if local else m.group(0)

    content = IMG_RE.sub(repl, content)

    def ts(v) -> str:
        try:
            return datetime.fromtimestamp(int(v) / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    fm = {
        "title": title,
        "id": entry.get("id", ""),
        "folder": folders.get(str(entry.get("folderId", 0)), f"folder_{entry.get('folderId')}"),
        "created": ts(entry.get("createDate")),
        "modified": ts(entry.get("modifyDate")),
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f'{k}: "{str(v).replace(chr(34), chr(39))}"')
    lines += ["---", ""]
    if title and not content.lstrip().startswith("# "):
        lines += [f"# {title}", ""]
    lines.append(content)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    here = pathlib.Path(__file__).parent
    ap = argparse.ArgumentParser(description="小米云服务笔记导出（只读）")
    ap.add_argument("-c", "--cookie-file", default=str(here / "cookie.txt"),
                    help="存放 i.mi.com Cookie 的文件（默认脚本同目录 cookie.txt）")
    ap.add_argument("-o", "--out", default="mi-notes", help="输出目录（默认 ./mi-notes）")
    ap.add_argument("-r", "--rate", type=float, default=5.0,
                    help="全局请求速率上限 req/s（默认 5，越小越不易触发风控）")
    ap.add_argument("-w", "--workers", type=int, default=4, help="并发线程数（默认 4）")
    ap.add_argument("--limit", type=int, default=0, help="只导出最近 N 条（试跑用）")
    ap.add_argument("--no-assets", action="store_true", help="不下载图片等附件")
    ap.add_argument("--full", action="store_true",
                    help="忽略缓存重新拉取全部正文（默认只拉新增和云端有改动的）")
    ap.add_argument("--offline", action="store_true",
                    help="不联网，仅用 .cache 已有数据重新生成 Markdown（Cookie 过期时可用）")
    ap.add_argument("--prune", action="store_true",
                    help="删除输出目录中不属于本次计划的 .md（改名后清理旧文件用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只拉索引比对并报告增量，不下载正文、不写任何文件")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    assets = out / "assets"
    cache = out / ".cache"
    for p in (out, assets, cache):
        p.mkdir(parents=True, exist_ok=True)
    folders_file = cache / "_folders.json"

    cookie = ""
    if not args.offline:
        cookie_path = pathlib.Path(args.cookie_file)
        if not cookie_path.exists():
            print(f"找不到 Cookie 文件: {cookie_path}\n"
                  f"请参考 README.md 从浏览器复制 Cookie 并保存到该文件。", file=sys.stderr)
            return 1
        cookie = cookie_path.read_text(encoding="utf-8").strip()

    c = Client(cookie, rate=args.rate)

    state = SyncState.load(cache)
    if state.last_sync:
        print(f"上次同步: {state.last_sync}（清单 {len(state.notes)} 条）")

    if args.offline:
        print("[1/3] 离线模式：从本地缓存重建 ...")
        folders = (json.loads(folders_file.read_text(encoding="utf-8"))
                   if folders_file.exists() else dict(BUILTIN_FOLDERS))
        entries = []
        for f in cache.glob("*.json"):
            if f.name.startswith("_"):
                continue
            try:
                entries.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as ex:
                print(f"  ! 缓存损坏跳过 {f.name}: {ex}", file=sys.stderr)
    else:
        print("[1/3] 拉取笔记索引 ...")
        entries, folders = fetch_index(c)
        if not args.dry_run:
            folders_file.write_text(json.dumps(folders, ensure_ascii=False),
                                    encoding="utf-8")
    entries.sort(key=lambda e: int(e.get("createDate") or 0))
    if args.limit:
        entries = entries[-args.limit:]
    print(f"  共 {len(entries)} 条笔记, {len(folders)} 个文件夹: "
          f"{', '.join(folders.values())}")

    # 增量比对：只看索引里的 modifyDate 与清单记录，不读缓存正文
    sync = None
    refetch: set[str] = set()
    if not args.offline:
        sync = plan_sync(entries, state, force=args.full,
                         detect_deleted=not args.limit)
        refetch = sync.refetch
        print("  " + sync.summary())
        if args.limit:
            # --limit 只截取了最近 N 条，此时「清单里有而索引里没有」不等于云端删了
            print("  ! --limit 模式下跳过云端删除检测")
        elif sync.deleted:
            tail = "，--prune 会一并清理" if not args.prune else ""
            print(f"  云端已删除 {len(sync.deleted)} 条，本地仍有备份{tail}")

    plan = plan_paths(entries, folders, out)
    dated = sum(1 for e in entries
                if plan[e["id"]].stem != safe_name(note_title(e), e["id"]))
    print(f"  文件名: {len(entries) - dated} 条用 <标题>.md, "
          f"{dated} 条因重名用 <日期>-<标题>.md")

    # 内容和输出路径都没变、文件也还在的笔记，连缓存 JSON 都不必读，整条跳过。
    # 10 万条规模下这一步把「每次全量重写 Markdown」降为只写真正变化的那几条。
    todo = entries
    if sync:
        skip = set()
        for nid in sync.unchanged:
            rel = state.path_of(nid)
            if rel and rel == plan[nid].relative_to(out).as_posix() and plan[nid].exists():
                skip.add(nid)
        if skip:
            print(f"  跳过 {len(skip)} 条（内容与路径均未变，不读缓存也不重写）")
        todo = [e for e in entries if e["id"] not in skip]

    if args.dry_run:
        print("[2/3] --dry-run: 不下载、不写文件")
        for label, ids in (("新增", sync.fresh if sync else []),
                           ("有改动", sync.updated if sync else []),
                           ("云端已删除", sync.deleted if sync else [])):
            for nid in ids[:10]:
                p = plan.get(nid)
                print(f"  {label}: {nid} {p.relative_to(out).as_posix() if p else state.path_of(nid)}")
            if len(ids) > 10:
                print(f"  {label}: ... 另有 {len(ids) - 10} 条")
        print(f"[3/3] 本次将处理 {len(todo)} 条（其中需联网拉正文 {len(refetch)} 条）")
        return 0

    print(f"[2/3] 拉取正文并写出 Markdown (速率 {args.rate} req/s, {args.workers} 线程) ...")
    done, failed, assets_n = [0], [], [0]
    lock = threading.Lock()

    def work(e: dict) -> None:
        nid = e["id"]
        try:
            entry = fetch_note(c, nid, cache, force=nid in refetch)
            try:
                extra = json.loads(entry.get("extraInfo") or "{}")
            except Exception:
                extra = {}

            img_map: dict[str, str] = {}
            if not args.no_assets:
                for a in extra.get("attachments") or []:
                    digest, fid = a.get("digest"), a.get("fileId")
                    if not (digest and fid):
                        continue
                    try:
                        name = fetch_asset(c, fid, a.get("mimeType", ""), assets, digest,
                                           offline=args.offline)
                        if name:
                            img_map[digest.lower()] = name
                            with lock:
                                assets_n[0] += 1
                    except Exception as ex:
                        with lock:
                            failed.append((nid, f"附件 {digest[:8]}: {ex}"))

            dst = plan[nid]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(to_markdown(entry, folders, img_map), encoding="utf-8")

            with lock:
                # 只有整条成功才登记进清单；失败的保留旧记录（或没有记录），
                # 这样下次同步会自然重试，不会被误判为「已同步」
                state.record(nid, entry.get("modifyDate"),
                             dst.relative_to(out).as_posix())
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(todo):
                    print(f"  {done[0]}/{len(todo)} 条, 附件 {assets_n[0]} 个")
        except Exception as ex:
            with lock:
                failed.append((nid, str(ex)))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    prune_cap = max(20, len(state.notes) // 20)
    if args.prune and args.limit:
        print("  ! --limit 与 --prune 同用会误删其余笔记，已跳过清理", file=sys.stderr)
    elif args.prune and sync and len(sync.deleted) > prune_cap:
        # 索引会漏返回（见 fetch_index），把「本次没看到」当成「云端已删除」有风险。
        # 判定删除的数量异常大时宁可留垃圾，也不能删掉真笔记。
        print(f"  ! 本次判定云端已删除 {len(sync.deleted)} 条，超过安全阈值 {prune_cap} 条，"
              f"已跳过清理。请再跑一次确认不是索引漏返回。", file=sys.stderr)
    elif args.prune:
        # 只在真正存放笔记的子目录里清理。备份目录根下的 README.md 等自有文件
        # 不属于任何笔记，绝不能被当成陈旧笔记删掉。
        planned = {p.resolve() for p in plan.values()}
        note_dirs = {p.parent.resolve() for p in plan.values()}
        stale = {p.resolve() for p in out.rglob("*.md")
                 if p.parent.resolve() in note_dirs and p.resolve() not in planned}
        # 云端已删除的笔记，其文件夹可能已被清空而不在 note_dirs 里，靠清单补上
        for nid in (sync.deleted if sync else []):
            rel = state.path_of(nid)
            if rel and (out / rel).exists():
                stale.add((out / rel).resolve())
        for p in stale:
            p.unlink()
        print(f"  清理不在本次计划内的旧 Markdown {len(stale)} 个")
        # 缓存副本和清单记录也要删，否则 --offline 重建会让已删除的笔记复活
        for nid in (sync.deleted if sync else []):
            (cache / f"{nid}.json").unlink(missing_ok=True)
            state.forget(nid)
        if sync and sync.deleted:
            print(f"  清理云端已删除笔记的缓存 {len(sync.deleted)} 个")

    state.save(notes=len(state.notes), offline=args.offline,
               new=len(sync.fresh) if sync else 0,
               updated=len(sync.updated) if sync else 0,
               failed=len(failed))

    print("[3/3] 完成")
    skipped = len(entries) - len(todo)
    print(f"  成功 {done[0]} / {len(todo)} 条"
          + (f"（另有 {skipped} 条未变已跳过）" if skipped else "")
          + f", 附件 {assets_n[0]} 个 -> {out.resolve()}")
    if failed:
        print(f"  失败 {len(failed)} 项（重跑本脚本会自动续传）:", file=sys.stderr)
        for nid, err in failed[:20]:
            print(f"    {nid}: {err}", file=sys.stderr)
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
