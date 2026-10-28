# -*- coding: utf-8 -*-
"""体积统计：入库按「网盘里实际占多大」算，不按站点标称大小。"""

from __future__ import annotations

import re
from typing import Any

from .client import BaiduClient, BaiduError

_UNITS = (
    (re.compile(r"([\d.]+)\s*T(?:B)?", re.I), 1000.0),
    (re.compile(r"([\d.]+)\s*G(?:B)?", re.I), 1.0),
    (re.compile(r"([\d.]+)\s*M(?:B)?", re.I), 0.001),
    (re.compile(r"([\d.]+)\s*K(?:B)?", re.I), 0.000001),
)


def parse_human_gb(text: str) -> float:
    """`10.5G` / `872.11 MB` / `1.2 TB` → GB。"""
    raw = str(text or "")
    for pattern, factor in _UNITS:
        match = pattern.search(raw)
        if match:
            try:
                return round(float(match.group(1)) * factor, 3)
            except ValueError:
                return 0.0
    return 0.0


def fmt_gb(gb: float) -> str:
    gb = float(gb or 0)
    if gb >= 1000:
        return f"{gb / 1000:.2f}TB"
    if gb >= 1:
        return f"{gb:.2f}GB"
    return f"{gb * 1000:.0f}MB"


def fmt_bytes(nbytes: int) -> str:
    return fmt_gb(int(nbytes or 0) / (1024 ** 3))


def measure(client: BaiduClient, remote_path: str, recursive: bool = True) -> float:
    """量网盘路径的体积（GB）。文件直接返大小，目录递归求和。"""
    if not remote_path:
        return 0.0
    parent, name = _split(remote_path)
    try:
        items = client.list_dir(parent)
    except BaiduError:
        return 0.0
    for item in items:
        if str(item.get("server_filename") or "") != name:
            continue
        if int(item.get("isdir") or 0):
            return client.dir_size_gb(remote_path) if recursive else 0.0
        return round(int(item.get("size") or 0) / (1024 ** 3), 3)
    return 0.0


def exists(client: BaiduClient, remote_path: str) -> bool:
    parent, name = _split(remote_path)
    try:
        items = client.list_dir(parent)
    except BaiduError:
        return False
    return any(str(item.get("server_filename") or "") == name for item in items)


def _split(remote_path: str) -> tuple[str, str]:
    path = "/" + str(remote_path or "").strip("/")
    parent, _, name = path.rpartition("/")
    return parent or "/", name


def ensure_space(client: BaiduClient, need_gb: float) -> tuple[bool, dict[str, Any]]:
    """剩余容量够不够。返回 (是否够, 容量信息)。"""
    try:
        quota = client.quota()
    except BaiduError:
        return True, {}
    return float(quota.get("free_gb") or 0) >= float(need_gb or 0), quota
