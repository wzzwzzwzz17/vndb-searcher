"""入库内容的体检与剪裁。

两件事：

1. **看起来像不像游戏本体**。百度分享站上大量链接是诱饵（教学视频、音乐、
   文档），文件名还把平台标签（【PC】【安卓】【KR】）贴在 .mp4 上。转存完
   必须先列一遍目录，按字节占比判断：游戏可执行/镜像/压缩包占多数才算游戏，
   视频和文档占多数则判为诱饵，连同目录一起删掉重来。

2. **多平台包只留 PC**。正版多平台合集（10~30GB）在制作时会把
   `PC/`、`安卓直装/`、`iPhone/`、`KRKR/`、`Tyranor/` 放在同一层。超过
   `threshold_gb`（默认 4GB）就按最底层的文件名分类，删掉安卓/iOS/KRKR 那几
   棵子树。判不准的一律保留——宁可多占空间，不能删错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable

# 游戏本体常见的可执行/镜像/压缩包扩展名
GAME_EXTS = {
    ".exe", ".msi", ".iso", ".mdf", ".mds", ".bin", ".cue", ".img", ".nrg",
    ".rar", ".zip", ".7z", ".tar", ".gz", ".bz2", ".xz", ".lzh",
    ".xp3", ".pck", ".arc", ".pak", ".cpk", ".dat", ".sfb", ".fpk", ".int",
    ".vpk", ".wav", ".ogg", ".mp3", ".wmv",
}
# 明显不是游戏本体的东西
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".flv", ".rmvb", ".rm", ".webm", ".ts", ".m4v", ".mov"}
DOC_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".csv", ".chm"}
# 手机安装包：分享里只有这些说明不是 PC 版（实测 Muv-Luv 那条 2.6GB 就一个 .apk）
MOBILE_EXTS = {".apk", ".xapk", ".ipa", ".obb", ".aab"}

# 分卷压缩包的后缀不是 .7z 而是 .7z.001 / .part1.rar / .r00 / .z01
# 实测 Rance Ⅹ 的汉化版就是 Rance10_CN.7z.001~.004，早期版本当成"未知格式"误删了
_SPLIT_RE = re.compile(
    r"\.(?:7z|zip|rar|tar|gz|bz2|xz|zst)\.\d{3}$"  # xxx.7z.001
    r"|\.part\d+\.rar$"                            # xxx.part1.rar
    r"|\.r\d{2}$"                                  # xxx.r00
    r"|\.z\d{2}$"                                  # xxx.z01
    r"|\.\d{3}$",                                  # xxx.001（裸分卷）
    re.I,
)


def is_archive_segment(name: str) -> bool:
    """分卷压缩包的一段（.7z.001 这类）。"""
    return bool(_SPLIT_RE.search(str(name or "")))

_ANDROID_ASCII = re.compile(
    r"(?<![a-z0-9])(?:android|apk|xapk|krkr|krkrz|kr|tyranor|winlator|exagear|ons|kirikiroid|obb|mobile|droid)(?![a-z0-9])",
    re.I,
)
_IOS_ASCII = re.compile(r"(?<![a-z0-9])(?:ios|ipa|iphone|ipad)(?![a-z0-9])", re.I)
_PC_ASCII = re.compile(r"(?<![a-z0-9])(?:pc|windows|win|exe|msi|setup|iso)(?![a-z0-9])", re.I)
_ANDROID_CJK = ("安卓", "手机", "吉里吉里", "直装")
_IOS_CJK = ("苹果", "iphone", "ipad")
_PC_CJK = ("硬盘", "免安装", "绿色", "汉化", "汉化版", "pc版", "win版", "电脑")

# 诱饵分享的典型标题：拿课程/资料冒充游戏（实测「2026真实U3D脚本拆解实践班直播第19讲」这类）
BAIT_WORDS = (
    "直播", "考点", "精讲", "实践班", "课程", "考研", "高考", "四六级", "学习须知",
    "汇编", "拆解", "脚本班", "u3d", "unity教程", "编程实战", "编程", "零基础",
    "入门", "进阶", "训练营", "案例分析", "教程", "网课", "课件",
)

# 「第19讲」「第145讲」「第BIHD讲」这种，课程直播页的标志
_BAIT_LESSON = re.compile(r"第\s*[0-9a-zA-Z]{1,6}\s*讲")

# 试玩版/体验版不是要收藏的完整游戏
DEMO_WORDS = ("demo", "体験版", "体验版", "trial")


def looks_like_bait(name: str) -> bool:
    """标题一看就是课程/资料分享，不是游戏。"""
    text = str(name or "").lower()
    return any(word in text for word in BAIT_WORDS) or bool(_BAIT_LESSON.search(text))


def looks_like_demo(name: str) -> bool:
    """试玩版/体验版（不是完整游戏）。"""
    text = str(name or "").lower()
    return any(word in text for word in DEMO_WORDS)


def classify_name(name: str) -> str:
    """把名字分成 pc / android / ios / mixed / other。"""
    text = str(name or "").strip().lower()
    if not text:
        return "other"
    android = bool(_ANDROID_ASCII.search(text)) or any(w in text for w in _ANDROID_CJK)
    ios = bool(_IOS_ASCII.search(text)) or any(w in text for w in _IOS_CJK)
    pc = bool(_PC_ASCII.search(text)) or any(w in text for w in _PC_CJK)
    if android and ios:
        return "mixed"
    if android and pc:
        return "mixed"
    if ios and pc:
        return "mixed"
    if android:
        return "android"
    if ios:
        return "ios"
    if pc:
        return "pc"
    return "other"


@dataclass
class Entry:
    """网盘里的一个条目（相对入库目录的路径）。"""

    rel: str
    name: str
    isdir: bool
    size: int = 0

    @property
    def depth(self) -> int:
        return len(PurePosixPath(self.rel).parts)

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.name).suffix.lower()

    @property
    def size_gb(self) -> float:
        return self.size / 1e9

    @property
    def is_archive(self) -> bool:
        """压缩包本体或分卷的一段（.7z.001 / .r00 / .part1.rar）。"""
        return self.suffix in GAME_EXTS or is_archive_segment(self.name)


@dataclass
class Report:
    """一次体检的结论。"""

    ok: bool
    reason: str
    total_gb: float = 0.0
    game_gb: float = 0.0
    junk_gb: float = 0.0
    entries: list[Entry] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    drop: list[Entry] = field(default_factory=list)
    drop_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def drop_gb(self) -> float:
        return self.drop_bytes / 1e9

    def summary(self) -> str:
        head = "像游戏本体" if self.ok else "不像游戏本体"
        line = f"{head}：共 {self.total_gb:.2f}GB，游戏文件 {self.game_gb:.2f}GB，视频/文档 {self.junk_gb:.2f}GB"
        if self.drop:
            line += f"；建议删除 {len(self.drop)} 项 {self.drop_gb:.2f}GB"
        return line


def make_entries(items: Iterable[dict[str, Any]], prefix: str = "") -> list[Entry]:
    """把 `list_dir` 的原始 item 转成 Entry 列表。"""
    out: list[Entry] = []
    for item in items:
        if isinstance(item, Entry):
            out.append(item)
            continue
        name = str(item.get("server_filename") or item.get("name") or "").strip()
        if not name:
            continue
        rel = f"{prefix}/{name}" if prefix else name
        out.append(
            Entry(
                rel=rel,
                name=name,
                isdir=bool(int(item.get("isdir") or 0)),
                size=int(item.get("size") or 0),
            )
        )
    return out


def check_game_like(entries: Iterable[Entry]) -> Report:
    """按字节占比判断这批文件是不是游戏本体。"""
    files = [e for e in entries if not e.isdir]
    total = sum(e.size for e in files)
    game = sum(e.size for e in files if e.is_archive)
    junk = sum(e.size for e in files if e.suffix in VIDEO_EXTS or e.suffix in DOC_EXTS)
    rep = Report(ok=False, reason="", total_gb=total / 1e9, game_gb=game / 1e9, junk_gb=junk / 1e9,
                 entries=list(entries))
    if total <= 0:
        rep.reason = "分享是空的（没有文件）"
        return rep
    if game == 0 and junk == 0:
        mobile = sum(e.size for e in files if e.suffix in MOBILE_EXTS)
        if mobile and mobile == total:
            rep.reason = "只有安卓/iOS 安装包，不是 PC 版"
            return rep
    if game > 0 and game / total >= 0.3:
        rep.ok = True
        rep.reason = f"游戏文件占 {game / total:.0%}"
        return rep
    if game == 0 and junk == 0:
        rep.reason = "没认出任何游戏文件（可能是分卷/未知格式）"
        return rep
    if junk / total >= 0.5:
        rep.reason = f"视频/文档占 {junk / total:.0%}，没有游戏本体"
        return rep
    rep.reason = f"游戏文件只占 {game / total:.0%}，无法确认是游戏"
    return rep


def plan_prune(entries: Iterable[Entry], threshold_gb: float = 4.0) -> Report:
    """多平台包只留 PC：返回要删的子树。"""
    entries = list(entries)
    base = check_game_like(entries)
    rep = Report(ok=base.ok, reason=base.reason, total_gb=base.total_gb, game_gb=base.game_gb,
                 junk_gb=base.junk_gb, entries=entries)
    if not rep.ok:
        return rep
    if rep.total_gb <= threshold_gb:
        rep.reason = f"{rep.total_gb:.2f}GB 没超过 {threshold_gb:g}GB，不剪裁"
        return rep

    tops = [e for e in entries if e.depth == 1]
    by_rel = {e.rel: e for e in entries}

    def subtree(rel: str) -> list[Entry]:
        return [e for e in entries if e.rel == rel or e.rel.startswith(rel + "/")]

    def deeper_has_pc(rel: str) -> bool:
        return any(e.isdir and e.depth > 1 and e.rel.startswith(rel + "/") and classify_name(e.name) == "pc"
                   for e in entries)

    candidates: list[Entry] = []
    for top in tops:
        kind = classify_name(top.name)
        if not top.isdir:
            if kind in ("android", "ios") and top.suffix in {".apk", ".xapk", ".ipa", ".obb"}:
                candidates.append(top)
            continue
        if kind in ("android", "ios"):
            # 目录名说是安卓/iOS，但里面还有 PC 分类的子目录 -> 不整棵删，交给下一层
            if deeper_has_pc(top.rel):
                continue
            candidates.append(top)
        elif kind == "mixed":
            # 【PC+安卓】这种在同一层分目录的，往下找
            for sub in [e for e in entries if e.isdir and e.depth == 2 and e.rel.startswith(top.name + "/")]:
                sub_kind = classify_name(sub.name)
                if sub_kind in ("android", "ios") and not deeper_has_pc(sub.rel):
                    candidates.append(sub)

    # 去重：被祖先覆盖的子树只删祖先
    picked: list[Entry] = []
    for cand in sorted(candidates, key=lambda e: (e.depth, e.rel)):
        if any(c.rel == cand.rel or cand.rel.startswith(c.rel + "/") for c in picked):
            continue
        picked.append(cand)

    rep.drop = picked
    rep.drop_bytes = sum(e.size for e in entries if any(e.rel == c.rel or e.rel.startswith(c.rel + "/") for c in picked))
    pc_left = any(
        classify_name(e.name) in ("pc", "other") and e.rel not in {c.rel for c in picked}
        and not any(e.rel.startswith(c.rel + "/") for c in picked)
        for e in entries
    )
    if picked and not pc_left:
        rep.drop = []
        rep.drop_bytes = 0
        rep.warnings.append("除了安卓/iOS 之外没找到 PC 端，先不动，人工看一眼")
    if picked:
        rep.keep = [
            e.rel for e in tops
            if e.rel not in {c.rel for c in picked} and not any(e.rel.startswith(c.rel + "/") for c in picked)
        ]
    return rep


def walk_cloud(client: Any, path: str, max_depth: int = 6) -> list[Entry]:
    """把网盘目录递归列成扁平的 Entry 列表。"""
    out: list[Entry] = []

    def rec(cur: str, depth: int) -> None:
        if depth > max_depth:
            return
        for entry in make_entries(client.list_dir(cur), prefix=cur[len(path):].strip("/")):
            out.append(entry)
            if entry.isdir:
                rec(f"{cur}/{entry.name}", depth + 1)

    rec(path, 1)
    return out


def cleanup_junk(client: Any, path: str, threshold_gb: float = 4.0) -> Report:
    """体检一个已入库目录，不删任何东西（只看）。"""
    return plan_prune(walk_cloud(client, path), threshold_gb=threshold_gb)


def apply_report(client: Any, path: str, report: Report) -> int:
    """按报告删掉该删的子树，返回删除项数。"""
    if not report.drop:
        return 0
    targets = [f"{path}/{e.rel}" for e in report.drop]
    client.delete(targets)
    return len(targets)
