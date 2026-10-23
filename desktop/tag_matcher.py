# -*- coding: utf-8 -*-
"""自然语言 → VNDB 官方标签候选（基于全量标签库检索）。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

import requests

import config
from tag_catalog import ensure_tag_catalog

# 中文/口语 → 英文检索词（覆盖常见 galgame 说法，再去全库模糊匹配）
CN_SYNONYMS: dict[str, list[str]] = {
    "露出": ["Exhibitionism", "Public Exposure", "Outdoor Sex"],
    "露出系": ["Exhibitionism", "Public Exposure"],
    "暴露": ["Exhibitionism", "Public Nudity"],
    "校园": ["School", "High School", "School Life"],
    "高中": ["High School"],
    "恋爱": ["Romance"],
    "纯爱": ["Romance"],
    "日常": ["Slice of Life"],
    "治愈": ["Healing", "Slice of Life"],
    "喜剧": ["Comedy"],
    "悲剧": ["Tragedy", "Utsuge"],
    "致郁": ["Utsuge", "Tragedy"],
    "哭戏": ["Nakige"],
    "泣きゲー": ["Nakige"],
    "拔作": ["Nukige"],
    "黄游": ["Nukige", "Sexual Content"],
    "黄油": ["Sexual Content", "Nukige"],
    "有h": ["Sexual Content"],
    "有H": ["Sexual Content"],
    "色情": ["Sexual Content"],
    "成人向": ["Sexual Content"],
    "开车": ["Sexual Content"],
    "无h": ["No Sexual Content"],
    "无H": ["No Sexual Content"],
    "全年龄": ["No Sexual Content"],
    "ntr": ["Netorare"],
    "NTR": ["Netorare"],
    "绿帽": ["Netorare"],
    "百合": ["Yuri", "Girl x Girl Romance", "Lesbian Sex"],
    "耽美": ["Yaoi", "Boy x Boy Romance", "Male on Male Sex"],
    "扶她": ["Futanari"],
    "触手": ["Tentacle"],
    "群p": ["Group Sex"],
    "群P": ["Group Sex"],
    "乱交": ["Group Sex"],
    "近亲": ["Incest"],
    "骨科": ["Incest"],
    "奇幻": ["Fantasy"],
    "科幻": ["Science Fiction"],
    "悬疑": ["Mystery"],
    "恐怖": ["Horror"],
    "战斗": ["Combat"],
    "后宫": ["Harem"],
    "逆后宫": ["Reverse Harem"],
    "女仆": ["Maid"],
    "青梅竹马": ["Childhood Friend"],
    "学园": ["School"],
    "魔法少女": ["Mahou Shoujo"],
    "无刀": ["No Tragedy"],
    "不要刀": ["No Tragedy"],
    "高分": [],
    "口碑": [],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokenize_user_text(text: str) -> list[str]:
    """粗分词：中文同义词键、英文单词、较长中文片段。"""
    text = text.strip()
    terms: list[str] = []

    for cn, en_list in CN_SYNONYMS.items():
        if cn.lower() in text.lower() or cn in text:
            terms.append(cn)
            terms.extend(en_list)

    # 英文短语/单词
    terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9\-']{1,}", text))

    # 中文连续片段（2–6 字）
    for m in re.finditer(r"[\u4e00-\u9fff]{2,6}", text):
        terms.append(m.group(0))

    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        t = t.strip()
        if not t or t in seen:
            continue
        # 跳过纯语气词
        if t in {"我想", "要求", "一些", "作品", "大量", "行为", "要有", "我想要"}:
            continue
        seen.add(t)
        out.append(t)
    return out


def _score_tag(term: str, tag: dict[str, Any]) -> float:
    t = _norm(term)
    if not t:
        return 0.0
    name = _norm(tag.get("name") or "")
    aliases = [_norm(a) for a in (tag.get("aliases") or []) if a]
    best = 0.0
    for cand in [name, *aliases]:
        if not cand:
            continue
        if t == cand:
            best = max(best, 1.0)
        elif t in cand or cand in t:
            best = max(best, 0.92)
        else:
            best = max(best, SequenceMatcher(None, t, cand).ratio())
    return best


def _live_tag_search(term: str, limit: int = 8) -> list[dict[str, Any]]:
    """调用 VNDB /tag search 作为补充（英文词更有效）。"""
    if not re.search(r"[A-Za-z]", term):
        return []
    try:
        resp = requests.post(
            f"{config.VNDB_API_BASE.rstrip('/')}/tag",
            json={
                "filters": ["search", "=", term],
                "fields": "name, aliases, category, searchable, applicable, vn_count",
                "sort": "searchrank",
                "reverse": True,
                "results": limit,
            },
            headers={
                "Content-Type": "application/json",
                "User-Agent": config.VNDB_USER_AGENT,
            },
            timeout=max(config.VNDB_TIMEOUT, 5.0),
        )
        if resp.status_code != 200:
            return []
        return list(resp.json().get("results") or [])
    except requests.RequestException:
        return []


def find_candidate_tags(
    user_text: str,
    top_k: int = 40,
    min_score: float = 0.62,
    extra_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    从全量标签库中为自然语言找到候选官方标签。
    返回: [{id,name,aliases,category,vn_count,score,match_term}, ...]
    """
    catalog = ensure_tag_catalog()
    terms = _tokenize_user_text(user_text)
    if extra_keywords:
        for k in extra_keywords:
            if k and k not in terms:
                terms.append(k)

    scored: dict[str, dict[str, Any]] = {}

    def consider(tag: dict[str, Any], score: float, term: str) -> None:
        if score < min_score:
            return
        # 优先 searchable/applicable
        if tag.get("searchable") is False and tag.get("applicable") is False:
            score *= 0.7
        tid = tag.get("id")
        if not tid:
            return
        prev = scored.get(tid)
        item = {
            "id": tid,
            "name": tag.get("name"),
            "aliases": tag.get("aliases") or [],
            "category": tag.get("category"),
            "vn_count": tag.get("vn_count") or 0,
            "score": score,
            "match_term": term,
        }
        if prev is None or score > prev["score"]:
            scored[tid] = item

    for term in terms:
        # 本地全库扫描（标签约 3k，可接受）
        for tag in catalog:
            consider(tag, _score_tag(term, tag), term)
        # 在线 search 补充
        for tag in _live_tag_search(term):
            # live 结果给较高基础分
            consider(tag, max(_score_tag(term, tag), 0.88), term)

    results = sorted(
        scored.values(),
        key=lambda x: (x["score"], x.get("vn_count") or 0),
        reverse=True,
    )
    return results[:top_k]


def format_candidates_for_prompt(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "（未检索到候选标签，请仅用 search 字段做模糊匹配，勿编造 gID）"
    lines = []
    for c in candidates:
        aliases = ", ".join((c.get("aliases") or [])[:3])
        alias_s = f" | aliases: {aliases}" if aliases else ""
        lines.append(
            f"- {c['id']} | {c.get('name')} | cat={c.get('category')} | "
            f"score={c['score']:.2f} | via={c.get('match_term')}{alias_s}"
        )
    return "\n".join(lines)
