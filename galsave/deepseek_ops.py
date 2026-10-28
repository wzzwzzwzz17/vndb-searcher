# -*- coding: utf-8 -*-
"""用 DeepSeek 做「Cursor 断连后」的运维决策：读状态 → 给可执行动作 JSON。

不替代 galsave 主循环；只在卡住/崩溃时给下一步建议，可由 guard 自动执行安全动作。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .catalog import doc_path as catalog_path
from .config import cget, cpath
from .db import TaskDB
from .heartbeat import age_seconds, read_heartbeat
from .logger import get_logger
from .notebook import doc_path as missing_path
from .passwords import doc_path as password_path

SAFE_ACTIONS = {
    "resume_run",       # 继续 python -m galsave run
    "status",           # 只看状态
    "catalog",          # 刷新已收录文档
    "missing",          # 刷新未收录文档
    "passwords",        # 刷新解压码文档
    "adopt_plan",       # adopt 只看计划
    "wait",             # 等待网络/配额
    "stop",             # 停止守护，等人处理
    "skip_note",        # 仅记录说明，不改业务
}

PROMPT_FALLBACK = """你是 galsave（个人 Galgame 收藏入库流水线）的运维助手。
Cursor 主控可能已断连。你只能建议下列安全动作之一，禁止发明下载/破解/传播指令。

允许的 action：
- resume_run：继续抓取（依赖本地 done 表去重，不会重复已入库）
- status / catalog / missing / passwords：刷新查看
- adopt_plan：扫描网盘待补登记（不 --apply）
- wait：暂时等待（网络/限流）
- stop：停止自动续跑，等人手工处理
- skip_note：只写说明

必须只输出一个 JSON 对象，不要 Markdown：
{"action":"...","reason":"...","run_args":"--buckets 8.0","wait_seconds":120}
"""


def load_prompt(cfg: dict[str, Any]) -> str:
    raw = str(cget(cfg, "paths.prompt_ops", "") or "").strip()
    path = cpath(cfg, "paths.prompt_ops") if raw else None
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    # 仓库旁工作区根目录
    alt = Path(cfg.get("_path") or ".")
    for candidate in (
        path,
        Path(str(alt)).resolve().parent / "DEEPSEEK续跑提示词.md" if alt else None,
        Path(__file__).resolve().parents[2] / "DEEPSEEK续跑提示词.md",
    ):
        if candidate and candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return PROMPT_FALLBACK


def build_context(cfg: dict[str, Any]) -> str:
    db = TaskDB(cpath(cfg, "paths.db"))
    try:
        stats = db.stats()
        recent = db.recent(8)
        missing = db.missing(limit=5)
        retries = db.retry_count()
    finally:
        db.close()
    hb = read_heartbeat(cfg) or {}
    age = age_seconds(cfg)
    lines = [
        f"已入库: {stats.get('entries')} 部 / {stats.get('total_gb')} GB（上限 {cfg.get('max_total_gb')}）",
        f"来源分布: {stats.get('by_source')}",
        f"C批配额: {stats.get('c_quota_gb')} / {cfg.get('c_quota_per_batch_gb')} GB",
        f"待重试: {retries}；未收录: {stats.get('missing')}；解压码: {stats.get('passwords')}",
        f"心跳年龄秒: {None if age is None else round(age, 1)}；心跳内容: {json.dumps(hb, ensure_ascii=False)}",
        f"优先级: {cfg.get('source_priority')}；分数档: {cfg.get('rating_buckets')}",
        f"prune阈值GB: {(cfg.get('prune') or {}).get('threshold_gb')}",
        f"文档: 已收录={catalog_path(cfg)} 未收录={missing_path(cfg)} 解压码={password_path(cfg)}",
        "最近入库:",
    ]
    for row in recent:
        lines.append(
            f"  - {row.get('vndb_id')} {row.get('title')} [{row.get('source_type')}] "
            f"{row.get('size_gb')}GB"
        )
    if missing:
        lines.append("最近未收录:")
        for row in missing:
            lines.append(f"  - {row.get('vndb_id')} {row.get('title')} :: {row.get('reason')}")
    log_path = cpath(cfg, "paths.log_dir") / "galsave.log"
    if log_path.exists():
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
            lines.append("日志尾部:")
            lines.extend(f"  {x}" for x in tail)
        except OSError:
            pass
    return "\n".join(lines)


def _chat(cfg: dict[str, Any], system: str, user: str) -> str:
    ds = cfg.get("deepseek") or {}
    key = str(ds.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("未配置 deepseek.api_key（写在 config.local.yaml 或环境变量 DEEPSEEK_API_KEY）")
    base = str(ds.get("base_url") or "https://api.deepseek.com").rstrip("/")
    model = str(ds.get("model") or "deepseek-chat")
    timeout = float(ds.get("timeout") or 90)
    max_tokens = int(ds.get("max_tokens") or 1200)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek 网络失败（断网防护）：{exc}") from exc
    try:
        return str(raw["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"DeepSeek 返回异常: {raw!r}") from exc


def _parse_action(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    # 允许模型偶尔包 ```json
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            text = brace.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("不是对象")
    action = str(data.get("action") or "").strip()
    if action not in SAFE_ACTIONS:
        raise ValueError(f"非法 action: {action}")
    return {
        "action": action,
        "reason": str(data.get("reason") or "")[:500],
        "run_args": str(data.get("run_args") or "").strip(),
        "wait_seconds": int(data.get("wait_seconds") or 120),
        "raw": data,
    }


def ask(cfg: dict[str, Any], question: str = "") -> dict[str, Any]:
    """问 DeepSeek：返回标准化动作字典。失败时退回 wait。"""
    log = get_logger()
    system = load_prompt(cfg)
    user = "当前状态：\n" + build_context(cfg)
    if question:
        user += "\n\n额外问题/约束：\n" + question.strip()
    user += "\n\n请给出下一步唯一 action 的 JSON。"
    try:
        content = _chat(cfg, system, user)
        action = _parse_action(content)
        log.info("DeepSeek 建议: %s (%s)", action["action"], action["reason"][:120])
        return action
    except Exception as exc:  # noqa: BLE001 - 断网时不能拖垮守护
        log.warning("DeepSeek 决策失败，改为 wait: %s", exc)
        return {
            "action": "wait",
            "reason": f"DeepSeek 不可用: {exc}",
            "run_args": "",
            "wait_seconds": 180,
            "raw": {},
        }
