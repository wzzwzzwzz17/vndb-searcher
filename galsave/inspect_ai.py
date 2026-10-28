# -*- coding: utf-8 -*-
"""用 DeepSeek 快速看网盘目录最底层：辨认伪装包里的游戏本体，并标出 PC 端。

外层文件名经常伪装成课程/工具；真正的 iso/rar/exe 往往在最深层。
启发式误判时以本模块为准（仍只输出可执行的 keep/drop 路径）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .logger import get_logger
from .prune import Entry, classify_name

SYSTEM = """你是 Galgame 百度网盘目录质检助手。根据「最底层文件名」快速判断。
硬规则（必须遵守）：
1. 所有 .mp4/.mkv/.avi 等视频 = 伪装，必须进 drop，绝不当游戏本体。
2. 体积明显 >1GB、文件名带 PC/电脑/【PC】，却不是 .iso/.rar/.7z/.exe/.mdf 等 = 伪装，进 drop。
3. 真游戏压缩包/镜像即使很大、带 PC 字样，也是本体，保留。
4. is_game=true 仅当存在真实游戏包；只剩伪装视频则 is_game=false。
5. drop 还要包含安卓/iOS/KRKR/Tyranor/工具目录。
6. 只输出 JSON：
{"is_game":true,"drop":["xxx.mp4","安卓"],"keep_hint":"PC/game.iso","reason":"..."}
"""


@dataclass
class AiInspect:
    is_game: bool = True
    drop: list[str] = field(default_factory=list)
    keep_hint: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def deepest_summary(entries: Iterable[Entry], limit: int = 60) -> str:
    """压缩成给模型的目录摘要：按深度降序列文件。"""
    items = [e for e in entries if not e.isdir]
    if not items:
        items = list(entries)
    items.sort(key=lambda e: (-e.depth, -e.size, e.rel))
    lines: list[str] = []
    for e in items[:limit]:
        kind = classify_name(e.name)
        gb = e.size / 1e9
        lines.append(f"d{e.depth}\t{kind}\t{gb:.2f}GB\t{e.rel}")
    dirs = [e for e in entries if e.isdir]
    dirs.sort(key=lambda e: (e.depth, e.rel))
    for e in dirs[:40]:
        lines.append(f"DIR d{e.depth}\t{classify_name(e.name)}\t{e.rel}")
    return "\n".join(lines) if lines else "(空目录)"


def _parse(text: str) -> AiInspect:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            text = brace.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not object")
    drop = data.get("drop") or []
    if isinstance(drop, str):
        drop = [drop]
    drop = [str(x).strip().lstrip("/") for x in drop if str(x).strip()]
    return AiInspect(
        is_game=bool(data.get("is_game", True)),
        drop=drop,
        keep_hint=str(data.get("keep_hint") or "")[:200],
        reason=str(data.get("reason") or "")[:300],
        raw=data,
    )


def judge_tree(cfg: dict[str, Any], title: str, entries: list[Entry]) -> AiInspect | None:
    """调 DeepSeek；失败返回 None（调用方退回启发式）。"""
    prune_cfg = cfg.get("prune") or {}
    if not bool(prune_cfg.get("ai_inspect", True)):
        return None
    log = get_logger()
    summary = deepest_summary(entries)
    user = (
        f"作品名：{title or '（未知）'}\n"
        f"条目数：{len(list(entries))}\n"
        f"目录摘要（depth/平台猜测/体积/相对路径）：\n{summary}\n"
        "请判断是否游戏本体，以及要删的非 PC/伪装路径。"
    )
    try:
        from .deepseek_ops import _chat

        content = _chat(cfg, SYSTEM, user)
        result = _parse(content)
        log.info(
            "AI目录质检：is_game=%s drop=%s — %s",
            result.is_game,
            result.drop or "[]",
            result.reason or result.keep_hint,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - AI 失败不阻断入库
        log.warning("AI目录质检失败，改用本地规则：%s", exc)
        return None


def merge_ai_drops(entries: list[Entry], ai: AiInspect) -> list[Entry]:
    """把 AI 给的相对路径落到实际 Entry（只删确实存在的）。"""
    by_rel = {e.rel: e for e in entries}
    names = {e.name: e for e in entries if e.depth == 1}
    picked: list[Entry] = []
    for rel in ai.drop:
        rel = rel.strip().strip("/")
        if rel in by_rel:
            picked.append(by_rel[rel])
            continue
        # 允许只给顶层目录名
        if rel in names:
            picked.append(names[rel])
            continue
        # 前缀匹配最浅的一棵
        hits = [e for e in entries if e.rel == rel or e.rel.startswith(rel + "/")]
        if hits:
            hits.sort(key=lambda e: e.depth)
            picked.append(hits[0])
    # 去重：保留较浅祖先
    out: list[Entry] = []
    for cand in sorted(picked, key=lambda e: (e.depth, e.rel)):
        if any(c.rel == cand.rel or cand.rel.startswith(c.rel + "/") for c in out):
            continue
        out.append(cand)
    return out
