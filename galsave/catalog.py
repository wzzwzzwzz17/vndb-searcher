# -*- coding: utf-8 -*-
"""已入库作品台账：工作区根目录文档，和 done 表一致，后续抓取靠 vndb_id 去重。"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

HEADER = """# 已收录作品

> 由 `python -m galsave run` 自动维护，与本地数据库 `done` 表一一对应。
> **主键是 VNDB 编号**（如 v2002）：已在表里的作品，下次抓取会直接跳过，不会重复入库。
> 若网盘里已有目录但库里没有，执行 `python -m galsave adopt` 从云端回填记录。

| VNDB | 作品 | 评分 | 来源 | 体积(GB) | 网盘路径 | 入库时间 |
| --- | --- | --- | --- | --- | --- | --- |
"""

_FOLDER_RE = re.compile(r"^v(\d+)\s*(.*)$", re.I)


def doc_path(cfg: dict[str, Any]) -> Path:
    from .config import REPO_ROOT, cget, cpath

    raw = str(cget(cfg, "paths.catalog_doc", "") or "").strip()
    if not raw:
        return REPO_ROOT / "已收录作品.md"
    return cpath(cfg, "paths.catalog_doc").resolve()


def _cell(text: Any, empty: str = "—") -> str:
    value = str(text or "").replace("|", "／").replace("\n", " ").strip()
    return value if value else empty


def _row(row: dict[str, Any]) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(row.get("finish_ts") or time.time())))
    rating = row.get("rating")
    rating_s = f"{float(rating):.2f}" if rating is not None and rating != "" else "—"
    return (
        f"| {_cell(row.get('vndb_id'))} "
        f"| {_cell(row.get('title'))} "
        f"| {rating_s} "
        f"| {_cell(row.get('source_type'))}/{_cell(row.get('site'), '')} "
        f"| {float(row.get('size_gb') or 0):.2f} "
        f"| {_cell(row.get('cloud_path'))} "
        f"| {when} |"
    )


def write_catalog(cfg: dict[str, Any], rows: list[dict[str, Any]]) -> Path:
    path = doc_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 按评分降序，方便人眼扫
    ordered = sorted(
        rows,
        key=lambda r: (-float(r.get("rating") or 0), str(r.get("vndb_id") or "")),
    )
    lines = [HEADER.rstrip("\n")]
    if not ordered:
        lines.append("\n（暂无：还没有入库记录。抓取成功或执行 adopt 后会出现在这里。）")
    else:
        for row in ordered:
            lines.append(_row(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def sync(cfg: dict[str, Any], db: Any) -> Path:
    return write_catalog(cfg, db.all_done())


def parse_folder_name(name: str) -> tuple[str, str] | None:
    """`v2002 STEINS;GATE` → (`v2002`, `STEINS;GATE`)。对不上规范命名返回 None。"""
    match = _FOLDER_RE.match(str(name or "").strip())
    if not match:
        return None
    return f"v{match.group(1)}", (match.group(2) or "").strip()
