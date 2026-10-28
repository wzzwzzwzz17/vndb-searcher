# -*- coding: utf-8 -*-
"""转存成功后的解压码台账：工作区根目录一份 Markdown，游戏名一一对应。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

HEADER = """# 解压码一览

> 由 `python -m galsave run` 在**鲲转存成功**时自动维护。
> 解压码优先取鲲 downloads 接口的 `archive_password`，没有再从资源详情正文里抠。
> 百度分享提取码（`?pwd=`）单独一列，方便打开网盘分享时对照。

| VNDB | 作品 | 解压码 | 分享提取码 | 来源 | 记录时间 |
| --- | --- | --- | --- | --- | --- |
"""


def doc_path(cfg: dict[str, Any]) -> Path:
    from .config import REPO_ROOT, cget, cpath

    raw = str(cget(cfg, "paths.password_doc", "") or "").strip()
    if not raw:
        return REPO_ROOT / "解压码.md"
    return cpath(cfg, "paths.password_doc").resolve()


def _cell(text: str, empty: str = "—") -> str:
    value = str(text or "").replace("|", "／").replace("\n", " ").strip()
    return value if value else empty


def _row(row: dict[str, Any]) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(row.get("ts") or time.time())))
    return (
        f"| {_cell(row.get('vndb_id'))} "
        f"| {_cell(row.get('title'))} "
        f"| {_cell(row.get('archive_password'), '（未提供）')} "
        f"| {_cell(row.get('share_pwd'))} "
        f"| {_cell(row.get('site'), 'kungal')} "
        f"| {when} |"
    )


def write_passwords(cfg: dict[str, Any], rows: list[dict[str, Any]]) -> Path:
    path = doc_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEADER.rstrip("\n")]
    if not rows:
        lines.append("\n（暂无：还没有通过转存入库并登记解压码的作品。）")
    else:
        for row in rows:
            lines.append(_row(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def sync(cfg: dict[str, Any], db: Any) -> Path:
    return write_passwords(cfg, db.passwords())
