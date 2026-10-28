# -*- coding: utf-8 -*-
"""运行心跳：断网/断进程后用文件判断流水线是否还活着，方便 guard 续跑。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def heartbeat_path(cfg: dict[str, Any]) -> Path:
    from .config import cpath

    return cpath(cfg, "paths.heartbeat")


def lock_path(cfg: dict[str, Any]) -> Path:
    from .config import cpath

    return cpath(cfg, "paths.run_lock")


def pulse(cfg: dict[str, Any], **fields: Any) -> None:
    """写入/刷新心跳。调度器每处理一部作品、下载进度时都应调用。"""
    path = heartbeat_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "pid": os.getpid(),
        "phase": fields.get("phase") or "running",
    }
    payload.update({k: v for k, v in fields.items() if v is not None})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_heartbeat(cfg: dict[str, Any]) -> dict[str, Any] | None:
    path = heartbeat_path(cfg)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def age_seconds(cfg: dict[str, Any]) -> float | None:
    hb = read_heartbeat(cfg)
    if not hb or "ts" not in hb:
        return None
    try:
        return max(0.0, time.time() - float(hb["ts"]))
    except (TypeError, ValueError):
        return None


def is_stale(cfg: dict[str, Any], stale_seconds: float | None = None) -> bool:
    age = age_seconds(cfg)
    if age is None:
        return True
    limit = stale_seconds
    if limit is None:
        limit = float((cfg.get("guardian") or {}).get("stale_seconds") or 180)
    return age > float(limit)


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except AttributeError:
        # Windows 上也有 os.kill；若不可用再试 OpenProcess 式探测
        return False
    return True


def acquire_lock(cfg: dict[str, Any]) -> bool:
    """简单进程锁：已有存活 PID 则返回 False。"""
    path = lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = int(path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            old = 0
        if old and pid_alive(old) and old != os.getpid():
            return False
    path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock(cfg: dict[str, Any]) -> None:
    path = lock_path(cfg)
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass
