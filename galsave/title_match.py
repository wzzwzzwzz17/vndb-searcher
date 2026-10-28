# -*- coding: utf-8 -*-
"""标题匹配：把各个资源站的条目标题和 VNDB 作品名对上。

站点命名风格差异很大（`ランス10` vs `Rance X -決戦-` vs `Rance 10`），所以：
1. 先做 NFKC 归一化、去空白、去标点；
2. 再用包含关系 + 序列相似度给分；
3. 低于 fuzzy_match_threshold 的一律不认，宁缺毋滥（避免下错作品）。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）")
_PUNCT = re.compile(r"[!-/:-@\[-`{-~・、。，．,.!?~〜～—\-_=+*\\/|:;'\"“”‘’]+")
_SPACES = re.compile(r"[\s\u3000]+")

# 副标题分隔符：连字符/波浪号/冒号。日文标题里空格完全不可靠
# （`ランス10－決戦－` 没有空格、`Rance X -Kessen-` 有），所以不要求空格，直接切。
# 波浪号同时覆盖 ASCII "~"、制表符波浪 U+301C "〜"、全角 U+FF5E "～"
# 连字符同时覆盖半角 "-"、EN/EM dash、水平线 U+2015 "―"、减号 U+2212 "−"、全角 U+FF0D "－"
_SEP = re.compile(r"\s*[-–—―−－~〜～:：]\s*")

# 常见的版本/后缀词，归一化时丢掉不影响判断
_NOISE_WORDS = (
    "初回限定版", "限定版", "通常版", "体験版", "完整版", "汉化版", "漢化版",
    "中文版", "中文", "汉化", "漢化", "生肉", "熟肉", "破解版", "免安装",
    "硬盘版", "绿色版", "整合版", "修正版", "patch", "update", "iso", "rar", "7z",
)


def normalize(text: str, strip_brackets: bool = False) -> str:
    """全角转半角、小写、去标点空白。"""
    raw = unicodedata.normalize("NFKC", str(text or "")).lower()
    if strip_brackets:
        raw = _BRACKETS.sub(" ", raw)
    for word in _NOISE_WORDS:
        raw = raw.replace(word, " ")
    raw = _PUNCT.sub(" ", raw)
    raw = _SPACES.sub(" ", raw).strip()
    return raw


def _tight(text: str) -> str:
    """再去掉所有空格：日文标题里空格位置完全不可靠（`ランス10 -決戦-` vs `ランス10－決戦－`）。"""
    return normalize(text, strip_brackets=True).replace(" ", "")


def similarity(a: str, b: str) -> float:
    """0-100 的相似度。

    站点标题常带前缀元数据（`│2D.G.F.│ [180223] [ALICESOFT] ランス10 -決戦- ...`），
    所以先去掉括号内容和空格，再用「包含关系」和序列相似度取分。
    原声碟这类非游戏本体的条目由爬虫侧的噪声词过滤，不靠分数区分。
    """
    na, nb = _tight(a), _tight(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    total = min(len(na), len(nb))
    longest = max(len(na), len(nb))
    # 短名被长名完整包含才算强匹配，且短名至少 5 字符：
    # 否则 `R10` 这种缩写会命中 `ver105` 之类完全无关的标题。
    if total >= 5 and (na in nb or nb in na):
        long_, short_ = (na, nb) if len(na) >= len(nb) else (nb, na)
        if long_.startswith(short_):
            # 后缀是副标题（`ランス10` ⊂ `ランス10決戦`），可信
            return round(88 + 12 * (total / longest), 2)
        extra = long_[: len(long_) - len(short_)]
        if len(extra) <= 2 and extra.isalpha():
            # 前缀只多一两个字，多半是另一个作品：`大戦国ランス` ≠ `戦国ランス`
            return 72.0
        return round(88 + 12 * (total / longest), 2)
    return round(SequenceMatcher(None, na, nb).ratio() * 100, 2)
    return round(SequenceMatcher(None, na, nb).ratio() * 100, 2)


def best_match(query: str, candidates: list[str]) -> tuple[float, str]:
    """在一组候选名里找最像 query 的那个。"""
    best: tuple[float, str] = (0.0, "")
    for name in candidates:
        score = similarity(query, name)
        if score > best[0]:
            best = (score, name)
    return best


def is_match(query: str, candidates: list[str], threshold: float = 85.0) -> bool:
    return best_match(query, candidates)[0] >= float(threshold)


def _cjk_ratio(text: str) -> float:
    """假名/汉字占比，用来把「日文原名」排在「罗马字+汉字副标题」之前。"""
    if not text:
        return 0.0
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", text))
    return cjk / len(text)


def search_terms(names: list[str], max_terms: int = 8) -> list[str]:
    """把作品名拆成适合丢给站点搜索框的关键词。

    日文原名优先：ggbase 镜像、shinnku 只认日文标题，而 VNDB 的 `alttitle`
    常是罗马字（`Rance Ⅹ -決戦-`），拿它去搜镜像站会一条都搜不到。
    """
    terms: list[str] = []
    for name in names:
        name = str(name or "").strip()
        if not name:
            continue
        for term in _split_terms(name):
            if term and term not in terms:
                terms.append(term)
    # sorted 是稳定排序，同组内保持原名顺序
    return sorted(terms, key=lambda t: -_cjk_ratio(t))[:max_terms]


def _split_terms(name: str) -> list[str]:
    out = [name]
    # "副标题 - xxx" / "～xxx～" 拆开，主标题往往才是站点的标题。
    # 分隔符两侧通常有一侧带空格，用正则兼容 " -決戦-"、" ～真説～" 这类写法。
    parts = [p.strip() for p in _SEP.split(name) if p.strip()]
    if len(parts) > 1:
        head = parts[0]
        if len(head) >= 3:
            out.append(head)
    # 去掉英文副标题后剩下的日文部分
    ja_only = re.sub(r"[A-Za-z0-9\s\-_:.'’]+$", "", name).strip()
    if len(ja_only) >= 3:
        out.append(ja_only)
    seen: list[str] = []
    for term in out:
        if term not in seen:
            seen.append(term)
    return seen
