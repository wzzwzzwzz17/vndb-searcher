"""把「三个渠道都没捞到」的作品写进工作区根目录的一个文档。

用户要求：鲲转存优先，其次真红直链，最后 ggbase 磁力；三个都不行时，
不能只是日志里一闪而过——要落到人能看到的地方，方便以后手动补。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

TITLE = "# 三个渠道都没捞到的作品"
HEADER = """# 三个渠道都没捞到的作品

> 由 `python -m galsave run` 自动维护：鲲转存 / 真红直链 / ggbase 磁力都失败、
> 或者压根没有候选的作品会记在这里。之后可以拿名字手动去别处找。
> 重新捞成功的话这一行会自动消失。

| VNDB | 作品 | 评分 | 记录时间 | 原因 |
| --- | --- | --- | --- | --- |
"""


def doc_path(cfg: dict[str, Any]) -> Path:
    """文档位置：默认工作区根目录（config 里的 `paths.missing_doc`）。"""
    from .config import REPO_ROOT, cget, cpath

    raw = str(cget(cfg, "paths.missing_doc", "") or "").strip()
    if not raw:
        # 测试或自定义配置里没写这一项时，落到仓库根目录的同名文件
        return REPO_ROOT / "未收录作品.md"
    return cpath(cfg, "paths.missing_doc").resolve()


def _row(vndb_id: str, title: str, rating: float, reason: str, ts: float) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    safe_title = str(title or "").replace("|", "／").strip()
    safe_reason = str(reason or "").replace("|", "／").replace("\n", " ").strip()[:400]
    return f"| {vndb_id} | {safe_title} | {float(rating or 0):.2f} | {when} | {safe_reason} |"


def write_missing(cfg: dict[str, Any], rows: list[dict[str, Any]]) -> Path:
    """整篇重写（幂等）：文档永远和数据库里的 missing 表一致。"""
    path = doc_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEADER.rstrip("\n")]
    if not rows:
        lines.append("\n（暂无：三个渠道都还没有失手的作品。）")
    else:
        for row in rows:
            lines.append(
                _row(
                    str(row.get("vndb_id") or ""),
                    str(row.get("title") or ""),
                    float(row.get("rating") or 0),
                    str(row.get("reason") or ""),
                    float(row.get("ts") or time.time()),
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def sync(cfg: dict[str, Any], db: Any) -> Path:
    """run 结束时调一次：把 missing 表导出成文档。"""
    return write_missing(cfg, db.missing())
