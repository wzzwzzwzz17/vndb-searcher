# -*- coding: utf-8 -*-
"""把已经入库但目录结构不合规的东西，整理成「/gal_save/v<编号> <日文原名>」。

早期版本按来源分了 `_offline` / `_share` / `_encrypted` 三层目录，名字要么是爬虫标题、
要么夹着「[作品名]」后缀。用户要求：所有作品都放同一个根目录下，一部件一个目录，
目录名统一是 `v<vndb 编号> <VNDB 日文原名>`。

用法：`python -m galsave reorg`（只打印计划）、`python -m galsave reorg --apply`（真动云端）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from baidu_cloud.client import posix_name, posix_parent
from baidu_cloud import BaiduError

from .logger import get_logger

SKIP = "skip"
MOVE_DIR = "move-dir"
MOVE_FILE = "move-file"


@dataclass
class PlanItem:
    vndb_id: str
    title: str
    kind: str
    source: str
    target: str
    note: str = ""

    def __str__(self) -> str:
        if self.kind == SKIP:
            return f"[跳过] {self.vndb_id} {self.source}  {self.note}"
        return f"[{self.kind}] {self.source}  →  {self.target}"


def _fresh_names(vndb: Any, rows: list[dict]) -> dict[str, str]:
    """用 VNDB 重新取一遍日文原名：老记录里存的是罗马字标题。"""
    names: dict[str, str] = {}
    if vndb is None:
        return names
    log = get_logger()
    for row in rows:
        vndb_id = str(row.get("vndb_id") or "")
        try:
            vn = vndb.get_vn(vndb_id)
        except Exception as exc:  # noqa: BLE001 - 网络问题就退回旧标题
            log.warning("取 %s 的 VNDB 名字失败: %s", vndb_id, exc)
            continue
        if vn is not None and vn.preferred_name:
            names[vndb_id] = vn.preferred_name
    return names


def plan_reorg(saver: Any, db: Any, client: Any, vndb: Any = None) -> list[PlanItem]:
    """算出每一条 done 记录应该待在哪，不修改任何东西。"""
    log = get_logger()
    items: list[PlanItem] = []
    rows = db.all_done()
    fresh = _fresh_names(vndb, rows)
    for row in rows:
        vndb_id = str(row.get("vndb_id") or "")
        title = fresh.get(vndb_id) or str(row.get("title") or "")
        path = str(row.get("cloud_path") or "")
        target = saver.folder_for(vndb_id, title)
        if not path:
            items.append(PlanItem(vndb_id, title, SKIP, path, target, "没有云端路径"))
            continue
        if path == target or path.startswith(target.rstrip("/") + "/"):
            items.append(PlanItem(vndb_id, title, SKIP, path, target, "已经合规"))
            continue
        try:
            entry = client.entry(path)
        except BaiduError as exc:
            items.append(PlanItem(vndb_id, title, SKIP, path, target, f"查不到: {exc}"))
            continue
        if entry is None:
            items.append(PlanItem(vndb_id, title, SKIP, path, target, "云端已不存在"))
            continue
        if int(entry.get("isdir") or 0) == 1:
            items.append(PlanItem(vndb_id, title, MOVE_DIR, path, target, "整目录改名/挪位"))
        else:
            final = f"{target}/{posix_name(path)}"
            items.append(PlanItem(vndb_id, title, MOVE_FILE, path, final, "单文件挪进作品目录"))
    log.debug("reorg 计划 %d 条", len(items))
    return items


def apply_reorg(saver: Any, db: Any, client: Any, items: list[PlanItem]) -> int:
    """执行计划，返回成功条数。每成功一条就同步更新数据库里的 cloud_path。"""
    log = get_logger()
    done = 0
    for item in items:
        if item.kind == SKIP:
            continue
        if client.entry(item.target) is not None and item.kind == MOVE_DIR:
            log.warning("目标目录已存在，跳过 %s", item.target)
            continue
        try:
            client.ensure_dir(posix_parent(item.target))
            client.move(item.source, posix_parent(item.target), posix_name(item.target))
            new_path = item.target
        except BaiduError as exc:
            log.warning("移动失败 %s → %s: %s", item.source, item.target, exc)
            continue
        db.mark_done(
            vndb_id=item.vndb_id,
            rating=_rating_of(db, item.vndb_id),
            title=item.title,
            source_type=_source_of(db, item.vndb_id),
            site=_site_of(db, item.vndb_id),
            resource_link=_link_of(db, item.vndb_id),
            cloud_path=new_path,
            size_gb=_size_of(db, item.vndb_id),
        )
        log.info("已整理 %s → %s", item.source, new_path)
        done += 1
    return done


def _row(db: Any, vndb_id: str) -> dict:
    return db.get_done(vndb_id) or {}


def _rating_of(db: Any, vndb_id: str) -> Any:
    return _row(db, vndb_id).get("rating")


def _source_of(db: Any, vndb_id: str) -> str:
    return str(_row(db, vndb_id).get("source_type") or "")


def _site_of(db: Any, vndb_id: str) -> str:
    return str(_row(db, vndb_id).get("site") or "")


def _link_of(db: Any, vndb_id: str) -> str:
    return str(_row(db, vndb_id).get("resource_link") or "")


def _size_of(db: Any, vndb_id: str) -> float:
    return float(_row(db, vndb_id).get("size_gb") or 0.0)
