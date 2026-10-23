# -*- coding: utf-8 -*-
"""结果 enrichment：简介中译、作品权重前 N 标签（中文，排除搜索用标签）。"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

import config
from tag_i18n import translate_tags_zh


def _client() -> OpenAI | None:
    key = config.DEEPSEEK_API_KEY
    if not key or key.startswith("在此填入"):
        return None
    return OpenAI(api_key=key, base_url=config.DEEPSEEK_BASE_URL)


def pick_top_tags_excluding(
    raw_tags: list[dict[str, Any]],
    exclude_ids: set[str],
    top_n: int = 4,
) -> list[dict[str, Any]]:
    """按 VNDB tag.rating（权重）降序，排除搜索标签，取前 N。"""
    scored: list[tuple[float, str]] = []
    for t in raw_tags or []:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.startswith("g"):
            continue
        if tid in exclude_ids:
            continue
        # 剧透标签尽量跳过
        if t.get("spoiler") not in (None, 0):
            continue
        try:
            w = float(t.get("rating") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if w <= 0:
            continue
        scored.append((w, tid))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_n]
    if not top:
        return []
    rows = translate_tags_zh([tid for _, tid in top], use_llm=True)
    # 附上权重
    weight_map = {tid: w for w, tid in top}
    for r in rows:
        r["weight"] = weight_map.get(r["id"])
    # 保持权重序
    order = {tid: i for i, (_, tid) in enumerate(top)}
    rows.sort(key=lambda r: order.get(r["id"], 99))
    return rows


def format_top_tags_zh(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "（暂无其它高权重标签）"
    parts = []
    for r in rows:
        w = r.get("weight")
        w_s = f"{w:.1f}" if isinstance(w, (int, float)) else "?"
        parts.append(f"{r.get('zh') or r.get('en')}（权重{w_s}）")
    return "、".join(parts)


def translate_texts_to_zh(texts: list[str]) -> list[str]:
    """批量把简介译为中文；已是中文的尽量原样返回。"""
    if not texts:
        return []
    # 已含较多中文则跳过翻译
    out = list(texts)
    need_idx = []
    need_payload = []
    for i, t in enumerate(texts):
        s = (t or "").strip()
        if not s or s in ("（无简介）", "(无简介)"):
            out[i] = s or "（无简介）"
            continue
        cn = len(re.findall(r"[\u4e00-\u9fff]", s))
        if cn >= max(8, len(s) // 4):
            out[i] = s
            continue
        need_idx.append(i)
        need_payload.append({"i": i, "text": s[:1200]})

    if not need_payload:
        return out

    client = _client()
    if client is None:
        return out

    try:
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是视觉小说简介翻译器。把英文/日文简介译成通顺简体中文，保留剧情信息，不要剧透过度发挥。"
                        "只输出 JSON：{\"items\":[{\"i\":0,\"zh\":\"中文简介\"}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(need_payload, ensure_ascii=False),
                },
            ],
            temperature=0.2,
            max_tokens=3500,
        )
        text = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return out
        data = json.loads(m.group(0))
        for item in data.get("items") or []:
            i = item.get("i")
            zh = (item.get("zh") or "").strip()
            if isinstance(i, int) and 0 <= i < len(out) and zh:
                out[i] = zh
    except Exception:
        pass
    return out


def enrich_result_items(
    items: list[dict[str, Any]],
    search_tag_ids: set[str],
    top_n: int = 4,
) -> list[dict[str, Any]]:
    """为每条结果附加 top_tags_zh，并把 description 译为中文。"""
    if not items:
        return items

    # 先抽 top tags
    for it in items:
        rows = pick_top_tags_excluding(
            it.get("raw_tags") or [], search_tag_ids, top_n=top_n
        )
        it["top_tags"] = rows
        it["top_tags_zh"] = format_top_tags_zh(rows)

    # 批量译简介
    descs = [it.get("description") or "" for it in items]
    zh_descs = translate_texts_to_zh(descs)
    for it, zh in zip(items, zh_descs):
        it["description_en"] = it.get("description") or ""
        it["description"] = zh
        it["description_zh"] = zh

    return items
