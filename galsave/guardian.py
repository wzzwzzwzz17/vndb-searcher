# -*- coding: utf-8 -*-
"""本地守护：Cursor 断连后仍能续跑；断网时退避，卡住时问 DeepSeek。

用法：
  python -m galsave guard              # 前台守护（Ctrl+C 停）
  python -m galsave guard --once       # 只检查一轮
  python -m galsave run --guarded      # 单次带心跳的 run（供守护拉起）
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import cpath, load_config
from .deepseek_ops import ask as deepseek_ask
from .heartbeat import (
    acquire_lock,
    age_seconds,
    is_stale,
    pid_alive,
    pulse,
    read_heartbeat,
    release_lock,
)
from .logger import get_logger, setup_logging


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _child_running(cfg: dict[str, Any]) -> tuple[bool, int]:
    hb = read_heartbeat(cfg) or {}
    pid = int(hb.get("pid") or 0)
    if pid and pid_alive(pid) and not is_stale(cfg):
        return True, pid
    if pid and pid_alive(pid) and is_stale(cfg):
        # 进程还在但心跳过旧：可能卡死在网络
        return True, pid
    return False, pid


def start_run(cfg: dict[str, Any], extra_args: list[str] | None = None) -> subprocess.Popen:
    """拉起受监护的 run 子进程（独立会话，Cursor 关掉也不一定带走）。"""
    log = get_logger()
    cmd = [sys.executable, "-m", "galsave", "run", "--guarded"]
    if extra_args:
        cmd.extend(extra_args)
    log_dir = cpath(cfg, "paths.log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    out = open(log_dir / "guard_child.log", "a", encoding="utf-8")  # noqa: SIM115
    creation = 0
    if os.name == "nt":
        # 新进程组，避免父进程结束时连带杀掉（尽量独立）
        creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    log.info("守护拉起: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        cwd=str(_repo_root()),
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=creation,
        close_fds=False,
    )


def run_guarded(
    cfg: dict[str, Any],
    run_kwargs: dict[str, Any] | None = None,
    skip_types: set[str] | None = None,
) -> dict[str, Any]:
    """带锁 + 心跳的正式 run。"""
    from .scheduler import Scheduler

    setup_logging(cpath(cfg, "paths.log_dir"))
    log = get_logger()
    if not acquire_lock(cfg):
        raise SystemExit("已有 galsave run 在跑（run.lock），不要并行开两个。")
    try:
        pulse(cfg, phase="starting")
        sched = Scheduler(cfg)
        if skip_types:
            sched.skip_types = set(skip_types)

        # 包装：每处理一部刷新心跳
        orig_handle = sched._handle

        def _handle_with_pulse(vn):  # type: ignore[no-untyped-def]
            pulse(cfg, phase="handling", vndb_id=vn.id, title=vn.preferred_name,
                  total_gb=sched.total_gb())
            try:
                return orig_handle(vn)
            finally:
                pulse(cfg, phase="idle_between", vndb_id=vn.id, total_gb=sched.total_gb())

        sched._handle = _handle_with_pulse  # type: ignore[method-assign]
        pulse(cfg, phase="running")
        result = sched.run(**(run_kwargs or {}))
        pulse(cfg, phase="finished", **{k: result.get(k) for k in ("success", "failed", "total_gb")})
        return result
    except KeyboardInterrupt:
        pulse(cfg, phase="interrupted")
        raise
    except Exception as exc:
        pulse(cfg, phase="crashed", error=str(exc)[:300])
        log.exception("guarded run 崩溃: %s", exc)
        raise
    finally:
        release_lock(cfg)


def guard_loop(cfg: dict[str, Any], once: bool = False) -> int:
    setup_logging(cpath(cfg, "paths.log_dir"))
    log = get_logger()
    gcfg = cfg.get("guardian") or {}
    if not gcfg.get("enabled", True):
        log.warning("guardian.enabled=false，退出")
        return 0

    poll = float(gcfg.get("poll_seconds") or 45)
    auto = bool(gcfg.get("auto_resume", True))
    max_restarts = int(gcfg.get("max_restarts") or 20)
    backoff = list(gcfg.get("network_backoff_seconds") or [30, 60, 120, 300, 600])
    ask_ds = bool(gcfg.get("ask_deepseek_when_stuck", True))
    stuck_after = int(gcfg.get("stuck_after_restarts") or 3)

    restarts = 0
    backoff_i = 0
    child: subprocess.Popen | None = None
    log.info("守护启动：poll=%ss auto_resume=%s", poll, auto)

    stopping = False

    def _stop(*_a: object) -> None:
        nonlocal stopping
        stopping = True
        log.warning("收到停止信号，守护退出（子进程若仍在会继续跑）")

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, OSError):
        pass

    while not stopping:
        alive, pid = _child_running(cfg)
        age = age_seconds(cfg)
        hb = read_heartbeat(cfg) or {}
        phase = str(hb.get("phase") or "")

        if alive and not is_stale(cfg):
            log.info("子进程正常 pid=%s age=%.0fs phase=%s", pid, age or -1, phase)
            backoff_i = 0
        elif alive and is_stale(cfg):
            log.warning("进程仍在但心跳过旧 pid=%s age=%.0fs，可能断网卡住", pid, age or -1)
            wait = int(backoff[min(backoff_i, len(backoff) - 1)])
            backoff_i += 1
            if ask_ds and backoff_i >= stuck_after:
                decision = deepseek_ask(cfg, "心跳过旧，进程可能卡在下载/上传/转存。给下一步。")
                log.info("DeepSeek: %s — %s", decision["action"], decision["reason"])
                if decision["action"] == "stop":
                    return 0
                if decision["action"] == "wait":
                    wait = max(wait, int(decision.get("wait_seconds") or wait))
                elif decision["action"] == "resume_run" and auto:
                    # 不杀旧进程，只记录；用户可手动停。避免误杀正在上传的任务。
                    log.warning("建议续跑但旧进程仍在，先等待；若确认卡死请人工结束 pid=%s", pid)
            log.info("断网/卡住退避 %ss", wait)
            time.sleep(wait)
            if once:
                return 0
            continue
        else:
            # 没有存活心跳
            if phase == "finished":
                log.info("上一轮已正常结束。守护待命（once 则退出）。")
                if once:
                    return 0
                time.sleep(poll)
                continue

            log.warning("未检测到活跃 run（age=%s phase=%s）", age, phase)
            if not auto:
                if ask_ds:
                    decision = deepseek_ask(cfg, "没有活跃抓取进程。是否 resume_run？")
                    log.info("DeepSeek: %s — %s", decision["action"], decision["reason"])
                    if decision["action"] != "resume_run":
                        if decision["action"] == "stop":
                            return 0
                        time.sleep(int(decision.get("wait_seconds") or poll))
                        if once:
                            return 0
                        continue
                else:
                    time.sleep(poll)
                    if once:
                        return 0
                    continue

            if restarts >= max_restarts:
                log.error("已重启 %d 次，停止自动续跑，改问 DeepSeek", restarts)
                if ask_ds:
                    deepseek_ask(cfg, f"已自动重启 {restarts} 次仍失败，请决策 stop 或 wait。")
                return 1

            extra: list[str] = []
            if ask_ds and restarts >= stuck_after:
                decision = deepseek_ask(cfg, "准备自动 resume_run，如需限定分数档请给 run_args。")
                log.info("DeepSeek: %s — %s", decision["action"], decision["reason"])
                if decision["action"] == "stop":
                    return 0
                if decision["action"] == "wait":
                    time.sleep(int(decision.get("wait_seconds") or backoff[0]))
                    if once:
                        return 0
                    continue
                if decision["action"] == "resume_run" and decision.get("run_args"):
                    # 只允许简单安全参数
                    for tok in str(decision["run_args"]).split():
                        if tok.startswith("-") or tok.replace(".", "").replace(",", "").isdigit():
                            extra.append(tok)
                        elif tok in {"--buckets", "--max-vns", "--skip", "--no-retries"}:
                            extra.append(tok)
                        elif extra and extra[-1] in {"--buckets", "--max-vns", "--skip"}:
                            extra.append(tok)

            wait = int(backoff[min(backoff_i, len(backoff) - 1)])
            backoff_i = min(backoff_i + 1, len(backoff) - 1)
            try:
                child = start_run(cfg, extra or None)
                restarts += 1
                pulse(cfg, phase="spawned", pid=child.pid, restarts=restarts)
                log.info("已拉起 run pid=%s（第 %d 次）", child.pid, restarts)
            except Exception as exc:  # noqa: BLE001
                log.error("拉起失败: %s；退避 %ss", exc, wait)
                time.sleep(wait)
                if once:
                    return 1
                continue

        if once:
            return 0
        time.sleep(poll)

    return 0
