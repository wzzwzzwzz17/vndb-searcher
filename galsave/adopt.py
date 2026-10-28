# -*- coding: utf-8 -*-
"""从网盘 /gal_save 回填 done 表：云端已有、库里没有的目录补上记录，避免重复抓。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from baidu_cloud.size import measure

from .catalog import parse_folder_name, sync as catalog_export
from .logger import get_logger


@dataclass
class AdoptItem:
    vndb_id: str
    title: str
    cloud_path: str
    size_gb: float
    action: str  # adopt / skip / bad
    note: str = ""


def scan_cloud(client: Any, root_dir: str) -> list[tuple[str, str, str]]:
    """列出根目录下形如 `v编号 名字` 的子目录，返回 (vndb_id, title, path)。"""
    items = client.list_dir(root_dir or "/gal_save")
    found: list[tuple[str, str, str]] = []
    for item in items:
        if int(item.get("isdir") or 0) != 1:
            continue
        name = str(item.get("server_filename") or "")
        path = str(item.get("path") or f"{root_dir.rstrip('/')}/{name}")
        parsed = parse_folder_name(name)
        if not parsed:
            continue
        vndb_id, title = parsed
        found.append((vndb_id, title or name, path))
    return found


def plan_adopt(client: Any, db: Any, root_dir: str) -> list[AdoptItem]:
    log = get_logger()
    done = db.done_ids()
    plan: list[AdoptItem] = []
    try:
        folders = scan_cloud(client, root_dir)
    except Exception as exc:  # noqa: BLE001
        log.error("列网盘目录失败: %s", exc)
        return plan
    for vndb_id, title, path in folders:
        if vndb_id in done:
            plan.append(AdoptItem(vndb_id, title, path, 0.0, "skip", "库里已有记录"))
            continue
        try:
            size_gb = float(measure(client, path) or 0)
        except Exception as exc:  # noqa: BLE001
            plan.append(AdoptItem(vndb_id, title, path, 0.0, "bad", f"量体积失败: {exc}"))
            continue
        if size_gb <= 0:
            plan.append(AdoptItem(vndb_id, title, path, 0.0, "bad", "目录为空或量不到体积"))
            continue
        plan.append(AdoptItem(vndb_id, title, path, size_gb, "adopt", "将写入 done，以后跳过"))
    return plan


def apply_adopt(db: Any, cfg: dict[str, Any], items: list[AdoptItem]) -> int:
    """把 action=adopt 的写入 done，并刷新已收录文档。返回新登记条数。"""
    n = 0
    for item in items:
        if item.action != "adopt":
            continue
        db.mark_done(
            vndb_id=item.vndb_id,
            rating=None,
            title=item.title,
            source_type="adopt",
            site="cloud",
            resource_link="",
            cloud_path=item.cloud_path,
            size_gb=item.size_gb,
        )
        n += 1
    if n:
        db.recompute_total()
    try:
        catalog_export(cfg, db)
    except OSError:
        pass
    return n
