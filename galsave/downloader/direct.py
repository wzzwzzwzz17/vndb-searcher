# -*- coding: utf-8 -*-
"""C 路线第一跳：直链下载到本地临时目录，支持断点续传。

占用的是校园网流量，所以调度器会给它单独记批次配额（默认 500GB/批）。
下载完后立刻加密 + 上传，然后删掉本地明文，尽量不长期占磁盘。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable

import requests

from ..logger import get_logger

CHUNK = 1024 * 1024
MIN_FREE_GB = 8.0


class DownloadError(RuntimeError):
    """下载失败（链接过期、磁盘不够、校验不符）。"""


class DirectDownloader:
    def __init__(self, cfg: dict, tmp_dir: Path | None = None):
        self.cfg = cfg
        self.log = get_logger()
        self.timeout = float(cfg.get("crawler", {}).get("timeout") or 30)
        self.tmp_dir = Path(tmp_dir) if tmp_dir else self._default_tmp()
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            )
        })

    @staticmethod
    def _default_tmp() -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "tmp"

    def download(
        self,
        url: str,
        filename: str,
        referer: str = "",
        progress: Callable[[int, int], None] | None = None,
        retries: int = 3,
    ) -> Path:
        """下到 tmp_dir/filename，返回本地路径。已存在同名文件则续传。"""
        if not url:
            raise DownloadError("直链为空")
        target = self.tmp_dir / safe_filename(filename)
        part = target.with_suffix(target.suffix + ".part")

        self._check_space(target)

        headers = {"Referer": referer} if referer else {}
        last_error: Exception | None = None
        for attempt in range(1, max(1, retries) + 1):
            existing = part.stat().st_size if part.exists() else 0
            start = existing
            # 先问服务器支不支持续传
            head = self.session.head(url, timeout=self.timeout, headers=headers, allow_redirects=True)
            total = int(head.headers.get("Content-Length") or 0)
            accepts_range = head.headers.get("Accept-Ranges", "").lower() == "bytes"
            if existing and total and existing >= total:
                part.replace(target)
                return target
            if existing and not accepts_range:
                part.unlink(missing_ok=True)
                existing = 0
                start = 0
            if existing:
                headers["Range"] = f"bytes={existing}-"

            try:
                with self.session.get(url, headers=headers, stream=True, timeout=self.timeout) as resp:
                    if resp.status_code not in (200, 206):
                        raise DownloadError(f"HTTP {resp.status_code}")
                    if resp.status_code == 200 and existing:
                        # 服务器忽略了 Range，只能重下
                        start = 0
                    mode = "ab" if start else "wb"
                    with open(part, mode) as handle:
                        for chunk in resp.iter_content(CHUNK):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            start += len(chunk)
                            if progress:
                                progress(start, total or start)
                expected = total or start
                if expected and start < expected:
                    raise DownloadError(f"下载不完整: {start}/{expected}")
                part.replace(target)
                return target
            except (requests.RequestException, DownloadError) as exc:
                last_error = exc
                self.log.warning("直链下载第 %d 次失败: %s", attempt, exc)
                time.sleep(min(20, 3 * attempt))

        raise DownloadError(f"直链下载失败: {last_error}")

    def _check_space(self, target: Path) -> None:
        free_gb = shutil.disk_usage(target.parent).free / (1024 ** 3)
        if free_gb < MIN_FREE_GB:
            raise DownloadError(f"磁盘剩余 {free_gb:.1f}GB，低于 {MIN_FREE_GB}GB 安全线，先清理")

    def cleanup(self, path: str | Path) -> None:
        """删掉本地明文（加密上传成功后调用）。"""
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            self.log.warning("删除本地文件失败 %s: %s", path, exc)


def safe_filename(name: str, max_len: int = 100) -> str:
    import re

    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip(" .")
    return (text[:max_len] or "download.bin")
