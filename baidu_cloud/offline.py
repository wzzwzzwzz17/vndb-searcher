# -*- coding: utf-8 -*-
"""磁力离线下载（A 路线）：把 magnet 丢给百度网盘离线，云端下完再统计体积。

注意：离线下载是会员功能，非 SVIP 账号会返回 errno 31064，这里会明确报出来，
让主程序把资源转给 B/C 路线，而不是当成「网络错误」重试到死。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .client import PAN, BaiduError, BaiduClient, join_posix
from .share import safe_name

CLOUD_DL = f"{PAN}/rest/2.0/services/cloud_dl"

# 离线任务状态（cloud_dl 返回的 status 字段）
STATUS_TEXT = {
    0: "等待中",
    1: "下载中",
    2: "已完成",
    3: "失败",
    4: "已取消",
}


class OfflineDownloader:
    def __init__(self, client: BaiduClient, app_id: str = "250528"):
        self.client = client
        self.app_id = str(app_id)

    # ---------- 建任务 ----------
    def add(self, source_url: str, save_dir: str, timeout: int = 60) -> str:
        """添加离线任务，返回 task_id。magnet 用 type=4。"""
        source = str(source_url or "").strip()
        if not source:
            raise BaiduError(2, "离线任务的链接为空", "百度离线")
        save_path = self.client.ensure_dir(save_dir)
        link_type = 4 if source.startswith("magnet:") else 1

        params = {
            "method": "add_task",
            "app_id": self.app_id,
            "bdstoken": self.client.bdstoken,
            "save_path": save_path,
            "source_url": source,
            "type": str(link_type),
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
        }
        resp = self.client.session.post(CLOUD_DL, params=params, data={"source_url": source},
                                        timeout=max(self.client.timeout, timeout))
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "离线接口返回不是 JSON", "百度离线") from exc
        errno = data.get("errno")
        if errno not in (0, "0", None):
            raise BaiduError(errno, str(data.get("show_msg") or data.get("error_msg") or ""), "百度离线")
        task_id = str(data.get("task_id") or data.get("taskid") or "")
        if not task_id:
            raise BaiduError(-1, "离线接口没返回 task_id", "百度离线")
        return task_id

    def add_into(self, magnet: str, root_dir: str, title: str) -> tuple[str, str]:
        """建任务并返回 (task_id, 保存目录)。"""
        save_dir = join_posix(root_dir, safe_name(title))
        return self.add(magnet, save_dir), save_dir

    # ---------- 查询 ----------
    def query(self, task_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not task_ids:
            return {}
        ids = [str(t) for t in task_ids]
        params = {
            "method": "query_task",
            "app_id": self.app_id,
            "bdstoken": self.client.bdstoken,
            "task_ids": json.dumps(ids),
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
        }
        resp = self.client.session.get(CLOUD_DL, params=params, timeout=self.client.timeout)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "查询离线任务返回不是 JSON", "百度离线") from exc
        if data.get("errno") not in (0, "0", None):
            raise BaiduError(data.get("errno"), str(data.get("show_msg") or ""), "百度离线")
        info = data.get("task_info") or {}
        return {str(k): v for k, v in info.items() if isinstance(v, dict)}

    def status(self, task_id: str) -> dict[str, Any]:
        info = self.query([task_id]).get(str(task_id))
        if not info:
            return {"status": -1, "status_text": "任务不存在", "file_size": 0, "save_path": ""}
        status = int(info.get("status") or 0)
        return {
            "status": status,
            "status_text": STATUS_TEXT.get(status, f"未知({status})"),
            "file_size": int(info.get("file_size") or 0),
            "save_path": str(info.get("save_path") or ""),
            "task_errno": info.get("task_errno"),
            "source_url": str(info.get("source_url") or ""),
            "raw": info,
        }

    def wait(self, task_id: str, poll_seconds: float = 20.0, max_hours: float = 6.0,
             on_tick: Any = None) -> dict[str, Any]:
        """轮询到任务结束。返回最后一次 status；超时返回 status=-2。"""
        deadline = time.time() + max_hours * 3600
        while time.time() < deadline:
            info = self.status(task_id)
            if on_tick:
                on_tick(info)
            if info["status"] in (2, 3, 4):
                return info
            time.sleep(max(5.0, poll_seconds))
        return {"status": -2, "status_text": "等待超时", "file_size": 0, "save_path": "", "task_errno": -1}

    # ---------- 清理 ----------
    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        params = {
            "method": "list_task",
            "app_id": self.app_id,
            "bdstoken": self.client.bdstoken,
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
            "page": "1",
        }
        resp = self.client.session.get(CLOUD_DL, params=params, timeout=self.client.timeout)
        try:
            data = resp.json()
        except ValueError:
            return []
        tasks = (data.get("task_info") or [])
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        return [t for t in tasks if isinstance(t, dict)][:limit]

    def delete(self, task_ids: list[str]) -> bool:
        if not task_ids:
            return True
        params = {
            "method": "delete_task",
            "app_id": self.app_id,
            "bdstoken": self.client.bdstoken,
            "task_ids": json.dumps([str(t) for t in task_ids]),
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
        }
        resp = self.client.session.post(CLOUD_DL, params=params, timeout=self.client.timeout)
        try:
            return resp.json().get("errno") in (0, "0", None)
        except ValueError:
            return False
