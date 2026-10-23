# -*- coding: utf-8 -*-
"""VNDB 标签中文名：本地词典 + 缓存 +（必要时）DeepSeek 补译。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import config
from tag_catalog import ensure_tag_catalog

ZH_CACHE_FILE = os.path.join(config.CACHE_DIR, "tag_zh_cache.json")

# 常用标签中文（可继续扩充）
TAG_ZH: dict[str, str] = {
    "g23": "色情内容",
    "g235": "无色情内容",
    "g236": "低色情内容",
    "g214": "拔作/黄游向",
    "g96": "恋爱",
    "g97": "百合恋爱",
    "g98": "耽美恋爱",
    "g47": "校园",
    "g48": "高中",
    "g454": "日常",
    "g142": "日常喜剧",
    "g104": "喜剧",
    "g147": "戏剧",
    "g2": "奇幻",
    "g105": "科幻",
    "g19": "悬疑",
    "g7": "恐怖",
    "g13": "战斗",
    "g133": "男主角",
    "g134": "女主角",
    "g596": "泣きゲー/催泪",
    "g693": "鬱ゲー/致郁",
    "g32": "ADV",
    "g43": "NVL",
    "g513": "NTR/绿帽",
    "g514": "Netori/逆NTR",
    "g179": "群P",
    "g1117": "BDSM",
    "g178": "触手（强制）",
    "g600": "触手（合意）",
    "g82": "百合H",
    "g83": "耽美H",
    "g733": "破处",
    "g396": "孕交",
    "g413": "扶她女主",
    "g84": "强制/强奸描写",
    "g86": "近亲",
    "g844": "露出/暴露癖",
    "g1393": "户外性爱",
    "g735": "公共场所性爱",
    "g3436": "公共使用",
    "g151": "校园生活喜剧",
    "g160": "治愈",
    "g220": "恋爱（旧映射）",
}


def _load_zh_cache() -> dict[str, str]:
    if not os.path.isfile(ZH_CACHE_FILE):
        return {}
    try:
        with open(ZH_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_zh_cache(cache: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(ZH_CACHE_FILE), exist_ok=True)
    with open(ZH_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def extract_tag_ids_from_filters(filters: Any) -> list[str]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] in ("and", "or"):
            for c in node[1:]:
                walk(c)
            return
        if node[0] == "not":
            walk(node[1] if len(node) > 1 else None)
            return
        if len(node) == 3 and node[0] in ("tag", "dtag"):
            val = node[2]
            tid = val[0] if isinstance(val, list) and val else val
            if isinstance(tid, str) and re.match(r"^g\d+$", tid):
                found.append(tid)

    walk(filters)
    # 去重保序
    seen = set()
    out = []
    for t in found:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _catalog_name(tag_id: str, catalog: list[dict] | None = None) -> str:
    catalog = catalog if catalog is not None else ensure_tag_catalog()
    for t in catalog:
        if t.get("id") == tag_id:
            return t.get("name") or tag_id
    return tag_id


def _heuristic_zh(en_name: str) -> str | None:
    """无词典时的弱启发：不做英数糊弄，尽量给可读中文。"""
    n = (en_name or "").strip()
    if not n:
        return None
    # 已是中文
    if re.search(r"[\u4e00-\u9fff]", n):
        return n
    return None


def translate_tags_zh(
    tag_ids: list[str],
    tag_roles: dict[str, str] | None = None,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """
    返回 [{id, en, zh, role, level_hint}]，zh 必为中文可读名。
    """
    catalog = ensure_tag_catalog()
    cache = _load_zh_cache()
    cache.update({k: v for k, v in TAG_ZH.items()})
    roles = tag_roles or {}

    pending_en: dict[str, str] = {}
    rows: list[dict[str, Any]] = []

    for tid in tag_ids:
        en = _catalog_name(tid, catalog)
        zh = cache.get(tid) or TAG_ZH.get(tid) or _heuristic_zh(en)
        row = {
            "id": tid,
            "en": en,
            "zh": zh or "",
            "role": roles.get(tid, ""),
        }
        if not zh:
            pending_en[tid] = en
        rows.append(row)

    if pending_en and use_llm:
        try:
            from openai import OpenAI

            key = config.DEEPSEEK_API_KEY
            if key and not key.startswith("在此填入"):
                client = OpenAI(api_key=key, base_url=config.DEEPSEEK_BASE_URL)
                payload = [{"id": k, "name": v} for k, v in pending_en.items()]
                resp = client.chat.completions.create(
                    model=config.DEEPSEEK_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是 Galgame/VNDB 标签中文翻译器。"
                                "把英文标签译成简短中文名（2–10字），适合给玩家看。"
                                "只输出 JSON：{\"translations\":[{\"id\":\"g123\",\"zh\":\"中文\"}]}。"
                                "不要音译堆砌，不要只返回英文或 ID。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ],
                    temperature=0.1,
                    max_tokens=800,
                )
                text = resp.choices[0].message.content or ""
                m = re.search(r"\{[\s\S]*\}", text)
                if m:
                    data = json.loads(m.group(0))
                    for item in data.get("translations") or []:
                        tid = item.get("id")
                        zh = (item.get("zh") or "").strip()
                        if tid and zh and re.search(r"[\u4e00-\u9fff]", zh):
                            cache[tid] = zh
                    _save_zh_cache(cache)
        except Exception:
            pass

    # 回填；仍无中文则用「中文释义（保留英文名）」而不是纯英文数字
    for row in rows:
        tid = row["id"]
        zh = cache.get(tid) or row["zh"]
        if not zh or not re.search(r"[\u4e00-\u9fff]", zh):
            en = row["en"]
            zh = f"「{en}」题材/标签"
        row["zh"] = zh
        cache.setdefault(tid, zh)

    _save_zh_cache(cache)
    return rows


def format_tags_zh_line(tag_rows: list[dict[str, Any]]) -> str:
    if not tag_rows:
        return "（本轮未解析到具体标签）"
    parts = []
    for r in tag_rows:
        role = r.get("role") or ""
        role_s = {"core": "核心", "secondary": "次要"}.get(role, "")
        if role_s:
            parts.append(f"{r['zh']}（{role_s}，{r['id']}）")
        else:
            parts.append(f"{r['zh']}（{r['id']}）")
    return "、".join(parts)
