# -*- coding: utf-8 -*-
"""实时状态窗：读 SQLite + 尾随 galsave.log，不介入抓取本身。

用法：python -m galsave watch
"""

from __future__ import annotations

import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from .config import cpath, load_config
from .db import TaskDB

REFRESH_MS = 800
LOG_TAIL = 18

RE_BUCKET = re.compile(r"分数档\s+([\d.]+)\s+开始")
RE_VN = re.compile(r"▶\s+(v\d+)\s+《(.+?)》\s+评分\s+([\d.]+)")
RE_TRY = re.compile(r"尝试\s+\[(\w+)/(\w+)\]\s+(.+?)(?:\s+\([\d.]+GB\))?\s+(https?\S+|magnet:\S+)?")
RE_DL = re.compile(r"aria2 已下\s+([\d.]+)GB（([\d.]+)MB/s，(\d+)\s*秒）")
RE_DL_DONE = re.compile(r"aria2 下载完成：(.+?)（([\d.]+)GB，(\d+)s）")
RE_UPLOAD = re.compile(r"文件\s+([\d.]+)GB，分片")
RE_OK = re.compile(r"✓\s+入库\s+(.+?)（([\d.]+)GB")
RE_MISS = re.compile(r"记入未收录清单")
RE_END = re.compile(r"结束：本次成功\s+(\d+).*用时\s+([\d.]+)\s*分钟")


def _tail_lines(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) > 512_000:
        data = data[-512_000:]
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def parse_log(lines: list[str]) -> dict[str, Any]:
    st: dict[str, Any] = {
        "bucket": "-",
        "vn": "-",
        "rating": "-",
        "phase": "空闲",
        "channel": "-",
        "speed": "-",
        "progress": "-",
        "last_ok": "-",
        "last_err": "-",
        "running": False,
    }
    for line in lines:
        msg = line[20:] if len(line) > 20 else line  # strip timestamp roughly
        if m := RE_BUCKET.search(line):
            st["bucket"] = m.group(1)
            st["running"] = True
        if m := RE_VN.search(line):
            st["vn"] = f"{m.group(1)}  {m.group(2)}"
            st["rating"] = m.group(3)
            st["phase"] = "检索中"
            st["channel"] = "-"
            st["speed"] = "-"
            st["progress"] = "-"
            st["running"] = True
        if m := RE_TRY.search(line):
            st["channel"] = f"{m.group(1)}/{m.group(2)}"
            title = m.group(3).strip()
            if len(title) > 48:
                title = title[:46] + "…"
            st["phase"] = f"尝试 {st['channel']} · {title}"
            st["running"] = True
        if "跳过已知是诱饵" in line or "跳过诱饵" in line:
            st["phase"] = "转存跳过（诱饵）"
            st["last_err"] = line.split("INFO", 1)[-1].strip()[-80:]
        if "百度网盘转存" in line and ("✗" in line or "WARNING" in line or "ERROR" in line):
            st["phase"] = "转存失败"
            st["last_err"] = line.split(" ", 3)[-1][-100:] if len(line) > 40 else line
        if m := RE_DL.search(line):
            st["phase"] = "下载中"
            st["speed"] = f"{m.group(2)} MB/s"
            st["progress"] = f"{m.group(1)} GB · {m.group(3)}s"
            st["running"] = True
        if m := RE_DL_DONE.search(line):
            st["phase"] = "下载完成，准备上传"
            st["progress"] = f"{m.group(2)} GB · {m.group(3)}s"
            st["speed"] = "-"
        if m := RE_UPLOAD.search(line):
            st["phase"] = f"上传中（包 {m.group(1)} GB）"
            st["running"] = True
        if "上传中断" in line or "上传失败" in line:
            st["phase"] = "上传重试中"
            st["last_err"] = "分片连接被重置（会自动重试）"
        if m := RE_OK.search(line):
            path = m.group(1)
            if len(path) > 56:
                path = "…" + path[-54:]
            st["last_ok"] = f"{path}  ({m.group(2)} GB)"
            st["phase"] = "刚入库"
            st["speed"] = "-"
        if RE_MISS.search(line):
            st["phase"] = "三渠道失败 → 未收录"
        if m := RE_END.search(line):
            st["phase"] = f"本轮结束（成功 {m.group(1)}，{m.group(2)} 分钟）"
            st["running"] = False
        if "启动：已入库" in line:
            st["running"] = True
            st["phase"] = "已启动"
    # freshness: last log line age
    if lines:
        try:
            ts = time.strptime(lines[-1][:19], "%Y-%m-%d %H:%M:%S")
            age = time.time() - time.mktime(ts)
            st["log_age"] = age
            if age > 120 and st["phase"] not in ("空闲",) and "结束" not in st["phase"]:
                st["phase"] = st["phase"] + "（日志已停更新）"
                st["running"] = False
            elif age <= 90:
                st["running"] = True
        except (ValueError, OverflowError, OSError):
            st["log_age"] = None
    return st


class WatchApp:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.db_path = cpath(cfg, "paths.db")
        self.log_path = cpath(cfg, "paths.log_dir") / "galsave.log"
        self.max_gb = float(cfg.get("max_total_gb") or 4000)
        self.c_quota = float(cfg.get("c_quota_per_batch_gb") or 500)

        self.root = tk.Tk()
        self.root.title("galsave 实时状态")
        self.root.geometry("720x520")
        self.root.minsize(560, 400)
        self.root.configure(bg="#1a1d23")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#1a1d23")
        style.configure("TLabel", background="#1a1d23", foreground="#e8eaed", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 14), foreground="#f0f3f7")
        style.configure("Muted.TLabel", foreground="#9aa0a6", font=("Segoe UI", 9))
        style.configure("Value.TLabel", font=("Consolas", 11), foreground="#8ab4f8")
        style.configure("Ok.TLabel", foreground="#81c995", font=("Consolas", 10))
        style.configure("Warn.TLabel", foreground="#fdd663", font=("Consolas", 10))
        style.configure("Err.TLabel", foreground="#f28b82", font=("Consolas", 10))
        style.configure("green.Horizontal.TProgressbar", troughcolor="#2d3139", background="#81c995")
        style.configure("blue.Horizontal.TProgressbar", troughcolor="#2d3139", background="#8ab4f8")
        style.configure("amber.Horizontal.TProgressbar", troughcolor="#2d3139", background="#fdd663")

        pad = {"padx": 14, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="galsave 监视器", style="Title.TLabel").pack(side="left")
        self.lbl_run = ttk.Label(top, text="● 未在跑", style="Muted.TLabel")
        self.lbl_run.pack(side="right")

        grid = ttk.Frame(self.root)
        grid.pack(fill="x", **pad)

        def row(r: int, name: str) -> ttk.Label:
            ttk.Label(grid, text=name, style="Muted.TLabel").grid(row=r, column=0, sticky="w", pady=2)
            v = ttk.Label(grid, text="—", style="Value.TLabel")
            v.grid(row=r, column=1, sticky="w", padx=8)
            return v

        grid.columnconfigure(1, weight=1)
        self.lbl_total = row(0, "已入库")
        self.lbl_c = row(1, "本批本地下载")
        self.lbl_bucket = row(2, "当前分数档")
        self.lbl_vn = row(3, "当前作品")
        self.lbl_phase = row(4, "当前阶段")
        self.lbl_channel = row(5, "渠道")
        self.lbl_speed = row(6, "下载速度")
        self.lbl_progress = row(7, "下载进度")
        self.lbl_ok = row(8, "最近入库")
        self.lbl_miss = row(9, "未收录 / 重试")
        self.lbl_err = row(10, "最近异常")

        bars = ttk.Frame(self.root)
        bars.pack(fill="x", padx=14, pady=8)
        ttk.Label(bars, text="总量进度", style="Muted.TLabel").pack(anchor="w")
        self.bar_total = ttk.Progressbar(bars, style="green.Horizontal.TProgressbar", maximum=100)
        self.bar_total.pack(fill="x", pady=(2, 8))
        ttk.Label(bars, text="本批直链/磁力配额", style="Muted.TLabel").pack(anchor="w")
        self.bar_c = ttk.Progressbar(bars, style="amber.Horizontal.TProgressbar", maximum=100)
        self.bar_c.pack(fill="x", pady=(2, 4))

        ttk.Label(self.root, text="最近日志", style="Muted.TLabel").pack(anchor="w", padx=14)
        self.log_box = tk.Text(
            self.root,
            height=LOG_TAIL,
            bg="#12141a",
            fg="#c5c8ce",
            insertbackground="#c5c8ce",
            font=("Consolas", 9),
            relief="flat",
            wrap="none",
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self.log_box.tag_configure("ok", foreground="#81c995")
        self.log_box.tag_configure("warn", foreground="#fdd663")
        self.log_box.tag_configure("err", foreground="#f28b82")

        self.root.after(200, self.tick)

    def tick(self) -> None:
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001 - UI 不能因一次读失败崩掉
            self.lbl_err.configure(text=str(exc)[:120], style="Err.TLabel")
        self.root.after(REFRESH_MS, self.tick)

    def refresh(self) -> None:
        stats = {"entries": 0, "total_gb": 0.0, "c_quota_gb": 0.0, "retry": 0, "missing": 0, "by_source": {}}
        try:
            with TaskDB(self.db_path) as db:
                stats = db.stats()
        except Exception:
            pass

        total = float(stats.get("total_gb") or 0)
        entries = int(stats.get("entries") or 0)
        c_used = float(stats.get("c_quota_gb") or 0)
        by = stats.get("by_source") or {}
        src = "  ".join(f"{k}:{v}" for k, v in by.items()) or "空"
        self.lbl_total.configure(text=f"{entries} 部 / {total:.2f} GB  （上限 {self.max_gb:.0f} GB）  [{src}]")
        self.lbl_c.configure(text=f"{c_used:.2f} / {self.c_quota:.0f} GB")
        self.lbl_miss.configure(
            text=f"未收录 {stats.get('missing') or 0} · 待重试 {stats.get('retry') or 0}"
        )
        pct_t = min(100.0, total / self.max_gb * 100) if self.max_gb else 0
        pct_c = min(100.0, c_used / self.c_quota * 100) if self.c_quota else 0
        self.bar_total["value"] = pct_t
        self.bar_c["value"] = pct_c

        lines = _tail_lines(self.log_path, 250)
        st = parse_log(lines)
        self.lbl_bucket.configure(text=str(st.get("bucket") or "-"))
        self.lbl_vn.configure(text=str(st.get("vn") or "-"))
        self.lbl_phase.configure(text=str(st.get("phase") or "-"))
        self.lbl_channel.configure(text=str(st.get("channel") or "-"))
        self.lbl_speed.configure(text=str(st.get("speed") or "-"))
        self.lbl_progress.configure(text=str(st.get("progress") or "-"))
        self.lbl_ok.configure(text=str(st.get("last_ok") or "-"), style="Ok.TLabel")
        err = str(st.get("last_err") or "-")
        self.lbl_err.configure(text=err, style="Err.TLabel" if err != "-" else "Muted.TLabel")

        if st.get("running"):
            self.lbl_run.configure(text="● 运行中", style="Ok.TLabel")
        else:
            self.lbl_run.configure(text="● 未在跑 / 已停", style="Muted.TLabel")

        recent = lines[-LOG_TAIL:]
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        for line in recent:
            tag = ()
            if "✓" in line or "入库" in line and "INFO" in line:
                tag = ("ok",)
            elif "WARNING" in line or "✗" in line:
                tag = ("warn",)
            elif "ERROR" in line:
                tag = ("err",)
            self.log_box.insert("end", line + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


def run_watch(cfg: dict[str, Any] | None = None) -> int:
    app = WatchApp(cfg or load_config())
    app.run()
    return 0
