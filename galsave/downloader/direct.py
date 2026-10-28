# -*- coding: utf-8 -*-
"""C 路线第一跳：直链下载到本地临时目录，支持断点续传。

占用的是校园网流量，所以调度器会给它单独记批次配额（默认 500GB/批）。
下载完后立刻加密 + 上传，然后删掉本地明文，尽量不长期占磁盘。
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

import requests

from ..logger import get_logger

CHUNK = 1024 * 1024
MIN_FREE_GB = 8.0
GB_BYTES = 1000 ** 3  # 与网盘/方案文档一致，按十进制 GB 算
# aria2 分段连接数：单连接实测只有 1.5MB/s，分段能快好几倍
ARIA2_CONNECTIONS = 8


class DownloadError(RuntimeError):
    """下载失败（链接过期、磁盘不够、校验不符）。"""


class DirectDownloader:
    def __init__(self, cfg: dict, tmp_dir: Path | None = None):
        self.cfg = cfg
        self.log = get_logger()
        self.timeout = float(cfg.get("crawler", {}).get("timeout") or 30)
        self.use_aria2 = bool(cfg.get("crawler", {}).get("aria2_direct", False))
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

    # ---------- aria2 分段下载（快，且自带断点续传） ----------
    def _aria2_binary(self) -> str:
        from .torrent import Aria2Downloader

        resolved = Aria2Downloader(self.cfg).binary
        return resolved if resolved and Path(resolved).exists() else ""

    def _download_aria2(self, url: str, target: Path, referer: str,
                        retries: int, progress: Callable[[int, int], None] | None) -> bool:
        """用 aria2 分段下到 target，成功返回 True。

        单连接实测 1.5MB/s（400MB 要 4 分钟），分段 8 路能快数倍；aria2 自己维护
        `.aria2` 控制文件做断点续传，和 requests 那套 `.part` 互不干扰。
        """
        binary = self._aria2_binary()
        if not binary:
            return False
        cmd = [
            binary,
            f"--dir={self.tmp_dir}",
            f"--out={target.name}",
            f"--max-connection-per-server={ARIA2_CONNECTIONS}",
            f"--split={ARIA2_CONNECTIONS}",
            "--min-split-size=1M",
            "--continue=true",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--file-allocation=none",
            "--summary-interval=0",
            "--console-log-level=warn",
            "--retry-wait=5",
            "--max-tries=%d" % max(1, retries),
            "--connect-timeout=%d" % int(self.timeout),
        ]
        if referer:
            cmd.append(f"--referer={referer}")
        cmd.append(url)

        self.log.info("用 aria2 分段下载（%d 连接）: %s", ARIA2_CONNECTIONS, target.name)
        started = time.time()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.log.warning("aria2 起不来（%s），退回单连接下载", exc)
            return False
        while proc.poll() is None:
            time.sleep(3)
            if progress and target.exists():
                progress(target.stat().st_size, 0)
        if proc.returncode != 0:
            self.log.warning("aria2 退出码 %s（文件 %s 字节）", proc.returncode,
                             target.stat().st_size if target.exists() else 0)
        if not target.exists() or target.stat().st_size <= 0:
            return False
        self.log.info("aria2 下载完成：%s（%.2fGB，%.0fs）", target.name,
                      target.stat().st_size / GB_BYTES, time.time() - started)
        return True

    def probe(self, url: str, referer: str = "") -> tuple[int, bool]:
        """只看响应头，返回 (字节数, 是否支持续传)；拿不到就返回 (0, False)。

        用来在真的开下之前判断「这批配额够不够」，避免先把校园流量花掉再拒绝。
        """
        if not url:
            return 0, False
        headers = {"Referer": referer} if referer else {}
        try:
            head = self.session.head(url, timeout=self.timeout, headers=headers, allow_redirects=True)
            total = int(head.headers.get("Content-Length") or 0)
            accepts = head.headers.get("Accept-Ranges", "").lower() == "bytes"
            return total, accepts
        except (requests.RequestException, ValueError) as exc:
            self.log.debug("探测直链体积失败 %s: %s", url[:80], exc)
            return 0, False

    def download(
        self,
        url: str,
        filename: str,
        referer: str = "",
        progress: Callable[[int, int], None] | None = None,
        retries: int = 8,
    ) -> Path:
        """下到 tmp_dir/filename，返回本地路径。

        实测 zd/galgamedownload 这类 CDN 传大文件时经常中途断流
        （`Connection broken: IncompleteRead`），所以每一轮都从 `.part` 已有字节
        用 Range 续传；服务器忽略 Range（回 200）时才从头重来。断了不算失败，
        已下载的字节不作废：只要还有进展就继续重试，连续两轮零进展才放弃。
        """
        if not url:
            raise DownloadError("直链为空")
        target = self.tmp_dir / safe_filename(filename)
        part = target.with_suffix(target.suffix + ".part")

        self._check_space(target)

        # 上次已经完整下好的文件（例如上传失败留下的）直接复用，别再花一遍流量
        if target.exists() and not part.exists():
            expected_size, _accepts = self.probe(url, referer=referer)
            got = target.stat().st_size
            if expected_size and got == expected_size:
                self.log.info("复用上次下好的文件：%s（%.2fGB）", target.name, got / 1e9)
                return target
            if expected_size and 0 < got < expected_size:
                # 半成品（比如 aria2 下到一半停了）：改名为 .part，接着续传，别从头再来
                target.replace(part)
                self.log.info("发现半成品 %s（%.2f/%.2fGB），改名 .part 继续续传",
                              target.name, got / 1e9, expected_size / 1e9)

        # 有 aria2 就先用它分段下（快）；它自己会接着上次的 .aria2 控制文件续传，
        # 而 requests 那套 .part 半成品只能由 requests 自己续，两者不混用。
        control = target.with_suffix(target.suffix + ".aria2")
        if self.use_aria2 and not part.exists() and not control.exists():
            expected, _accepts = self.probe(url, referer=referer)
            if self._download_aria2(url, target, referer, retries, progress):
                got = target.stat().st_size
                if not expected or got == expected:
                    return target
                self.log.warning("aria2 下到 %d 字节，HEAD 说 %d，删掉重来核对", got, expected)
                target.unlink(missing_ok=True)

        base_headers = {"Referer": referer} if referer else {}
        last_error: Exception | None = None
        total = 0
        attempt = 0
        stalled = 0
        while attempt < max(1, retries):
            attempt += 1
            before = part.stat().st_size if part.exists() else 0
            headers = dict(base_headers)
            if before:
                headers["Range"] = f"bytes={before}-"
            try:
                with self.session.get(url, headers=headers, stream=True, timeout=self.timeout) as resp:
                    if resp.status_code == 416 and before:
                        # Range 越界：要么已经下完，要么 .part 是坏的
                        if total and before >= total:
                            part.replace(target)
                            return target
                        part.unlink(missing_ok=True)
                        raise DownloadError("续传位置越界，重新下载")
                    if resp.status_code not in (200, 206):
                        raise DownloadError(f"HTTP {resp.status_code}")
                    if resp.status_code == 206:
                        start = before
                        content_range = resp.headers.get("Content-Range") or ""
                        if "/" in content_range:
                            try:
                                total = int(content_range.rsplit("/", 1)[-1])
                            except ValueError:
                                pass
                        if not total:
                            total = start + int(resp.headers.get("Content-Length") or 0)
                    else:
                        start = 0
                        total = int(resp.headers.get("Content-Length") or 0)
                        if before:
                            self.log.debug("服务器不支持续传，从头下载")
                    with open(part, "ab" if start else "wb") as handle:
                        for chunk in resp.iter_content(CHUNK):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            start += len(chunk)
                            if progress:
                                progress(start, total or start)
                got = part.stat().st_size if part.exists() else 0
                if total and got < total:
                    raise DownloadError(f"下载不完整: {got}/{total}")
                part.replace(target)
                return target
            except (requests.RequestException, DownloadError, OSError) as exc:
                last_error = exc
                kept = part.stat().st_size if part.exists() else 0
                stalled = 0 if kept > before else stalled + 1
                self.log.warning(
                    "直链下载第 %d 次中断（已存 %.0fMB，下次续传）: %s",
                    attempt, kept / 1e6, exc,
                )
                if stalled >= 2:
                    self.log.error("连续两轮没有任何新数据，先放弃（.part 保留，下次接着下）")
                    break
                time.sleep(min(20, 3 * attempt))

        raise DownloadError(f"直链下载失败（.part 已保留，下次可续传）: {last_error}")

    def _check_space(self, target: Path) -> None:
        free_gb = shutil.disk_usage(target.parent).free / GB_BYTES
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
