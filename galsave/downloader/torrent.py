# -*- coding: utf-8 -*-
"""磁力路线：百度网盘已不收磁力离线（实测 36001），改成本机 aria2 下载再加密上传。

本机必须装 aria2（`winget install aria2.aria2` 或官网 7z 包里的 `aria2c.exe`）。
找不到可执行文件时不抛异常，而是返回 `available() == (False, 原因)`，
让调度器把这条磁力登记进 `magnet_pending` 清单，之后人工处理。

下载完的产物可能是单个文件（iso/rar），也可能是一整个目录（分卷、镜像 + 音轨）。
目录会被打包成一个 zip，保证「一个作品 = 一个文件」再进加密和上传。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

from ..logger import get_logger

GB_BYTES = 1000 ** 3
MIN_FREE_GB = 8.0
DEFAULT_TIMEOUT_SECONDS = 6 * 3600


class Aria2Error(RuntimeError):
    """aria2 没装、下载失败或产物不完整。"""


class Aria2Downloader:
    """用本机 aria2c 把磁力下到临时目录。"""

    def __init__(self, cfg: dict[str, Any], tmp_dir: Path | None = None, binary: str = ""):
        self.cfg = cfg
        self.log = get_logger()
        self.bt = (cfg.get("baidu") or {})
        self.tmp_dir = Path(tmp_dir) if tmp_dir else self._default_tmp()
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.binary = binary or str(self.bt.get("aria2_path") or "") or self._which()
        self.seed_time = int(self.bt.get("aria2_seed_time") or 0)
        self.timeout = int(self.bt.get("aria2_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
        self.extra_args = [str(x) for x in (self.bt.get("aria2_args") or [])]

    @staticmethod
    def _default_tmp() -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "tmp"

    @staticmethod
    def _which() -> str:
        for name in ("aria2c", "aria2c.exe"):
            found = shutil.which(name)
            if found:
                return found
        return ""

    # ---------- 可用性 ----------
    def available(self) -> tuple[bool, str]:
        """返回 (能不能用, 原因)。没装 aria2 时只提醒，不算错误。"""
        if not self.binary:
            return False, "没找到 aria2c，装一个（winget install aria2.aria2）或用 baidu.aria2_path 指定路径"
        try:
            proc = subprocess.run([self.binary, "--version"], capture_output=True, text=True,
                                  timeout=20, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"调不起 aria2c: {exc}"
        if proc.returncode != 0:
            return False, f"aria2c --version 返回 {proc.returncode}"
        first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        return True, first.strip()

    # ---------- 下载 ----------
    def download(self, magnet: str, name: str, progress: Any = None) -> Path:
        """下磁力，返回可直接加密上传的单个文件路径（目录会打包成 zip）。"""
        if not str(magnet or "").startswith("magnet:"):
            raise Aria2Error(f"不是磁力链接: {str(magnet)[:40]}")
        ok, why = self.available()
        if not ok:
            raise Aria2Error(why)

        tag = _tag(name)
        work_dir = self.tmp_dir / tag
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        self._check_space(work_dir)

        cmd = [
            self.binary,
            f"--dir={work_dir}",
            "--seed-time=0",
            "--bt-stop-timeout=300",
            "--max-connection-per-server=8",
            "--summary-interval=0",
            "--console-log-level=warn",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            *self.extra_args,
            str(magnet),
        ]
        self.log.info("aria2 开始下载 %s", tag)
        started = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise Aria2Error(f"aria2 超过 {self.timeout // 3600} 小时没下完") from exc
        except OSError as exc:
            raise Aria2Error(f"调不起 aria2c: {exc}") from exc

        files = [p for p in work_dir.rglob("*") if p.is_file() and not p.name.endswith(".aria2")]
        if not files:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            raise Aria2Error("aria2 没下到任何文件" + (f"：{' / '.join(tail)}" if tail else ""))
        if proc.returncode != 0:
            self.log.warning("aria2 退出码 %s，但已下到 %d 个文件，继续处理", proc.returncode, len(files))

        took = time.time() - started
        total_gb = sum(p.stat().st_size for p in files) / GB_BYTES
        self.log.info("aria2 下载完成：%d 个文件 / %.2fGB / %.1f 分钟", len(files), total_gb, took / 60)
        return self._package(work_dir, files, name)

    # ---------- 产物整理 ----------
    def _package(self, work_dir: Path, files: list[Path], name: str) -> Path:
        """单个文件直接用；多个文件打成一个 zip。"""
        if len(files) == 1 and files[0].parent == work_dir:
            return files[0]
        target = work_dir / f"{_tag(name)}.zip"
        self.log.info("打包 %d 个文件 → %s", len(files), target.name)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in files:
                arc = path.relative_to(work_dir).as_posix()
                if arc == target.name:
                    continue
                zf.write(path, arc)
        self._remove_inputs(work_dir, files, target)
        return target

    @staticmethod
    def _remove_inputs(work_dir: Path, files: list[Path], keep: Path) -> None:
        """打包完把原始文件删掉，别把磁盘塞满。"""
        for path in files:
            if path == keep:
                continue
            try:
                path.unlink()
            except OSError:
                pass
        for child in sorted(work_dir.iterdir(), key=lambda p: len(p.parts), reverse=True):
            if child == keep:
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass

    def _check_space(self, target: Path) -> None:
        free_gb = shutil.disk_usage(target).free / GB_BYTES
        if free_gb < MIN_FREE_GB:
            raise Aria2Error(f"磁盘剩余 {free_gb:.1f}GB，低于 {MIN_FREE_GB}GB 安全线，先清理")


def _tag(name: str) -> str:
    import re

    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip(" .")
    return (text[:60] or "magnet")


def cleanup_dir(path: str | Path) -> None:
    """删掉 aria2 的临时目录（上传完之后调用）。"""
    target = Path(path)
    parent = target.parent
    try:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        if parent.exists() and not any(parent.iterdir()):
            os.rmdir(parent)
    except OSError:
        pass
