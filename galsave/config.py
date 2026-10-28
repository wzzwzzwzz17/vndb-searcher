# -*- coding: utf-8 -*-
"""配置加载：默认值 → config.yaml 覆盖 → 环境变量覆盖。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ConfigError(RuntimeError):
    """配置写错时抛出，带上出错的键名。"""


DEFAULTS: dict[str, Any] = {
    "rating_buckets": [8.0, 7.5, 7.0, 6.5, 6.0],
    "max_total_gb": 4000.0,
    # 优先鲲（云端转存，不占本地流量），其次真红直链，最后才用磁力（要本机 BT 下载）
    "source_priority": ["share", "direct", "magnet"],
    "c_quota_per_batch_gb": 500.0,
    "stop_on_reach_limit": True,
    "kill_running_task_on_stop": False,
    "fuzzy_match_threshold": 85,
    "max_retry": 3,
    "concurrency_per_vn": 3,
    "min_resource_gb": 0.3,
    "max_resource_gb": 60.0,
    # 入库后体检：超过阈值就当多平台包，只留 PC；不是游戏本体的诱饵分享直接删掉
    "prune": {
        "enabled": True,
        "threshold_gb": 4.0,
        # 转存后用 DeepSeek 看最底层文件：辨认伪装包里的游戏本体，并标 PC 端
        "ai_inspect": True,
    },
    "vndb": {
        "base_url": "https://api.vndb.org/kana",
        "timeout": 30,
        "page_size": 100,
        "request_interval": 0.5,
        "proxy": "",
    },
    "crawler": {
        "ggbase_bases": ["https://ggb.dlgal.com", "https://www.ggbases.com"],
        "kungal_base": "https://www.kungal.com",
        "shinnku_base": "https://www.shinnku.com",
        "timeout": 25,
        "interval": 1.0,
        "max_pages": 2,
        "proxy": "",
        "allow_collection": False,
        # 老磁力基本没种：一部作品最多真试几条磁力，剩下的只登记不下载
        "max_magnet_tries": 1,
        # 直链用 aria2 分段下载：实测本校校园网单机约 1.5MB/s，分 8 路没有收益，
        # 只有线路不限速时才值得打开
        "aria2_direct": False,
    },
    "baidu": {
        "root_dir": "/gal_save",
        # 所有作品都放在 root_dir 下，一部件一个目录，名字统一带 VNDB 编号
        "folder_pattern": "v{id} {name}",
        "upload_chunk_mb": 4,
        # 超过这个体积就切成多个加密包：大文件的分片会被放大到 30MB+，实测容易被重置
        "upload_split_gb": 1.0,
        # 上传并发分片数：百度单连接约 1.7MB/s，3 路实测能翻倍
        "upload_parallel": 3,
        "app_id": "250528",
        "interval": 1.5,
        # 磁力：百度已不收离线，默认用本机 aria2 下载再加密上传
        "magnet_local": True,
        "aria2_path": "",
        "aria2_args": [],
        "aria2_seed_time": 0,
        "aria2_timeout_seconds": 21600,
        # aria2 下完经常不肯退出，超过这么久没有新数据就当收工（有 .aria2 控制文件则算失败）
        "aria2_stall_seconds": 300,
        # 连种子元数据都拿不到（0 字节）超过这么久就放弃：老磁力常常没人做种了
        "aria2_metadata_seconds": 60,
        # 磁力没带 tracker 时补的公共 tracker
        "aria2_trackers": [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.tracker.cl:1337/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "http://tracker.openbittorrent.com:80/announce",
        ],
        "offline_enabled": True,
        "offline_wait_hours": 6.0,
        "poll_seconds": 20.0,
    },
    "encrypt": {
        "password": "gal-save-change-me",
        "enabled": True,
    },
    "run": {
        "max_vns": 0,
        "max_minutes": 0,
        "stop_after_consecutive_failures": 0,
    },
    "paths": {
        "db": "galsave/data/task.db",
        "log_dir": "galsave/logs",
        "missing_doc": "../未收录作品.md",
        "password_doc": "../解压码.md",
        "catalog_doc": "../已收录作品.md",
        "heartbeat": "galsave/data/heartbeat.json",
        "run_lock": "galsave/data/run.lock",
        "prompt_ops": "../DEEPSEEK续跑提示词.md",
    },
    # Cursor 断连/崩溃后仍能续跑 + 卡住时问 DeepSeek；本机覆盖写 config.local.yaml（已 gitignore）
    "guardian": {
        "enabled": True,
        "poll_seconds": 45,
        "stale_seconds": 180,
        "auto_resume": True,
        "max_restarts": 20,
        "network_backoff_seconds": [30, 60, 120, 300, 600],
        "ask_deepseek_when_stuck": True,
        "stuck_after_restarts": 3,
    },
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "timeout": 90,
        "max_tokens": 1200,
    },
}


def _deep_merge(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def cget(cfg: dict, dotted: str, default: Any = None) -> Any:
    """按 "a.b.c" 取配置，取不到返回 default。"""
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def cpath(cfg: dict, dotted: str) -> Path:
    """把配置里的相对路径解析成基于仓库根目录的绝对路径。"""
    raw = str(cget(cfg, dotted, "") or "")
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - 统一转成配置错误
        raise ConfigError(f"读取 {path} 失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是映射（键: 值）")
    return data


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取配置。找不到文件就用默认值，YAML 写错时报出具体原因。"""
    config_path = Path(path or os.environ.get("GALSAVE_CONFIG") or DEFAULT_CONFIG_PATH)
    cfg = copy.deepcopy(DEFAULTS)

    if config_path.exists():
        _deep_merge(cfg, _load_yaml(config_path))

    # 本机私有覆盖，放在 config.local.yaml（已进 .gitignore）
    local_path = config_path.with_name("config.local.yaml")
    if local_path.exists() and local_path.resolve() != config_path.resolve():
        _deep_merge(cfg, _load_yaml(local_path))

    _apply_env(cfg)
    _normalize(cfg)
    cfg["_path"] = str(config_path) if config_path.exists() else f"{config_path}（未找到，使用默认值）"
    if local_path.exists():
        cfg["_local_path"] = str(local_path)
    return cfg


def _apply_env(cfg: dict[str, Any]) -> None:
    """少量环境变量覆盖，方便临时跑一次不改文件。"""
    env_map = {
        "GALSAVE_MAX_GB": ("max_total_gb", float),
        "GALSAVE_C_QUOTA_GB": ("c_quota_per_batch_gb", float),
        "GALSAVE_FUZZY": ("fuzzy_match_threshold", float),
        "GALSAVE_PROXY": ("crawler.proxy", str),
        "GALSAVE_VNDB_PROXY": ("vndb.proxy", str),
        "DEEPSEEK_API_KEY": ("deepseek.api_key", str),
        "GALSAVE_DEEPSEEK_KEY": ("deepseek.api_key", str),
    }
    for env_name, (dotted, caster) in env_map.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            value = caster(raw)
        except ValueError as exc:
            raise ConfigError(f"环境变量 {env_name}={raw!r} 不是合法数值") from exc
        _set(cfg, dotted, value)


def _set(cfg: dict, dotted: str, value: Any) -> None:
    node = cfg
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _normalize(cfg: dict[str, Any]) -> None:
    """类型统一：分数桶、阈值、容量都转 float，并做范围检查。"""
    try:
        cfg["rating_buckets"] = sorted({float(x) for x in cfg["rating_buckets"]}, reverse=True)
    except (TypeError, ValueError) as exc:
        raise ConfigError("rating_buckets 必须是数字列表，例如 [8.0, 7.5]") from exc
    if not cfg["rating_buckets"]:
        raise ConfigError("rating_buckets 不能为空")

    for key in ("max_total_gb", "c_quota_per_batch_gb", "min_resource_gb", "max_resource_gb"):
        try:
            cfg[key] = float(cfg[key])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} 必须是数字") from exc
    if cfg["max_total_gb"] <= 0:
        raise ConfigError("max_total_gb 必须大于 0")

    threshold = float(cfg["fuzzy_match_threshold"])
    if 0 < threshold <= 1:
        threshold *= 100  # 允许写 0.85
    if not 0 < threshold <= 100:
        raise ConfigError("fuzzy_match_threshold 必须在 0-100（或 0-1）之间")
    cfg["fuzzy_match_threshold"] = threshold

    priority = [str(x).strip().lower() for x in cfg.get("source_priority") or []]
    unknown = [x for x in priority if x not in {"magnet", "share", "direct"}]
    if unknown:
        raise ConfigError(f"source_priority 里有未知来源: {unknown}")
    cfg["source_priority"] = priority or ["magnet", "share", "direct"]

    if int(cfg.get("max_retry", 3)) < 0:
        raise ConfigError("max_retry 不能是负数")
