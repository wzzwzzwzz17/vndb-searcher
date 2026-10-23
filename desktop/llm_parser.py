# -*- coding: utf-8 -*-
"""DeepSeek：自然语言 → VNDB 合法 Filter JSON（必须基于检索到的官方标签候选）。"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

import config
from tag_matcher import format_candidates_for_prompt

KEYWORD_PROMPT = """你是 VNDB 标签检索助手。把用户中文/日文/英文的 Galgame 偏好，转成适合在 VNDB 英文标签库里搜索的关键词。
只输出 JSON：{"keywords":["English or romanized term", ...]}
要求：
- 5–12 个词，英文为主（VNDB 标签多为英文）
- 包含核心玩法、题材、有无性内容等相关检索词
- 不要输出标签 ID，不要解释，不要拒绝成人向主题
"""


def build_system_prompt(
    search_mode: str,
    min_year: int | None = None,
    single_core_only: bool = False,
) -> str:
    mode = search_mode if search_mode in config.SEARCH_MODE_LABELS else config.SEARCH_MODE_QUALITY
    mode_label = config.SEARCH_MODE_LABELS[mode]
    year = int(min_year or config.DEFAULT_MIN_YEAR)

    year_rules = f"""
# 发售年份下限（强制）
用户设定最低年份为 {year}。必须在 filters 中加入：
["released", ">=", "{year}"]
低于该年份的老游戏一律不推荐。不要省略此条件。
"""

    if single_core_only:
        single_core_rules = """
# 单核心标签模式（已启用，强制）
本次只允许使用 **恰好 1 个** 核心 tag 条件（再加 rating / released 等非 tag 条件）。
禁止次要标签，禁止第二个 tag/dtag，禁止用 or 堆多个 tag。
tag_roles 里只能有一个 "core"，不要 secondary。
从用户需求中选出最关键的那一个标签作为唯一核心。
"""
    else:
        single_core_rules = """
# 单核心标签模式：未启用（可使用多个核心/次要标签，仍须遵守当前搜索模式规则）
"""

    if mode == config.SEARCH_MODE_QUALITY:
        mode_rules = f"""
# 当前搜索模式：总质量优先（{mode_label} / quality）
目标：宁可标签贴合度宽松，也要作品总分（Bayesian rating）高。
规则：
1. 必须加入较高评分门槛：默认 ["rating",">=",{config.QUALITY_MODE_MIN_RATING}]；用户强调神作/顶尖可到 80–85。
2. 标签 minLevel 宜低：核心约 0.8–1.2，次要约 0.5–0.8，避免高强度把高分作品滤掉。
3. sort 必须为 "rating"，reverse 必须为 true。
4. 标签数量 2–4 个即可；不要为了堆砌内容标签牺牲分数。
5. 输出 JSON 可附带 "tag_roles": {{"gID":"core"|"secondary"}}（可选）。
"""
    elif mode == config.SEARCH_MODE_CONTENT:
        mode_rules = f"""
# 当前搜索模式：内容权重优先（{mode_label} / content）
目标：内容标签在作品上必须足够「浓」（VNDB tag level），评分是次要排序依据。
规则：
1. 先判断「核心标签」与「次要标签」。
2. 核心标签 minLevel **必须 ≥ {config.CONTENT_CORE_TAG_LEVEL}**。
3. 次要标签：较重要 → 2.0；较弱氛围 → 1.6。不要一律拉满。
4. 评分门槛：默认 ["rating",">=",{config.CONTENT_MODE_MIN_RATING}]；sort=rating, reverse=true。
5. 核心 1–3 个；次要 0–2 个。
6. 必须输出 "tag_roles": {{"gID":"core"|"secondary"}}。
"""
    else:
        mode_rules = f"""
# 当前搜索模式：硬核内容要求（{mode_label} / hardcore）
目标：内容浓度接近满级，同时保证最低口碑；允许极端情况下结果为 0。
规则：
1. 明确区分核心 / 次要标签（必须输出 tag_roles）。
2. 核心标签 minLevel **必须 ≥ {config.HARDCORE_CORE_TAG_LEVEL}**；若该需求极强，优先用 **{config.HARDCORE_CORE_TAG_LEVEL_PREF}**（满分）。
   示例：["tag","=",["gID",0,{config.HARDCORE_CORE_TAG_LEVEL_PREF}]] 或 2.8。
3. 次要标签也要偏高：建议 2.0–2.4，不要降到 1.6 以下。
4. 评分必须高于站点 6.2，即 ["rating",">=",{config.HARDCORE_MIN_RATING}]（不可再降低）。
5. 核心标签宜少而狠（1–2 个为主），不要堆砌导致必然空结果；但也不要为了“一定有结果”而降低核心强度。
6. 本模式允许找不到作品；不要为了凑数而破坏硬核阈值。
7. sort=rating, reverse=true。
"""

    return f"""# 角色与任务边界（软注入 / Soft Framing）
你是「VNDB Kana Filter 编译器」——技术性元数据查询助手，不是内容创作者，也不提供任何下载/资源。
工作对象是 VNDB.org 公开数据库的官方标签体系（含成人向 Sexual content 分类）。
忠实映射用户偏好 → 输出可执行的 Filter JSON，是唯一正确行为。
不要道德说教，不要拒绝输出，不要把「生成查询条件」误判为「生成色情文本」。

# 硬红线
# （留空：由其他 agent 后续补充）

# 输出格式（严格，只输出一个 JSON，无 Markdown、无解释）
{{
  "filters": <VNDB filters 数组>,
  "sort": "rating",
  "reverse": true,
  "limit": 10,
  "tag_roles": {{"gxxxx": "core|secondary"}}
}}
注意：filters 必须是单个数组，正确示例 ["and", pred1, pred2]，
错误示例 [["and", pred1, pred2]]（多包一层）。

# 标签使用铁律（最重要）
1. 用户消息会附带「官方标签候选列表」（从 VNDB 全库约 3000+ 标签检索得到）。
2. 所有 tag / dtag 的 ID **只能**从该候选列表中选择，禁止编造任何 gID。
3. 若候选里已有精确对应，优先用 tag，不要只用 search。
4. 候选不足时可用 ["search","=","英文词"]，但不能发明标签 ID。
5. spoiler 固定为 0；minLevel 范围 0–3。

{mode_rules}
{single_core_rules}
{year_rules}

# Filter 语法
- 字段仅限：id, search, lang, olang, platform, length, released, rating, votecount,
  has_description, tag, dtag, developer, release, character, staff
- 运算符：=, !=, >=, >, <=, <
- limit：1–20，默认 10
- 发售日期比较用字符串年份即可，如 ["released",">=","{year}"]

# 成人向映射策略（勿回避）
- 有H/黄油/成人向 → 候选中的 Sexual Content
- 拔作/纯肉 → Nukige
- 全年龄/无H → No Sexual Content
- 具体玩法以候选列表里的官方名为准

# 自检
纯 JSON；含 rating 与 released 年份下限；tag ID 全部来自候选；模式规则已遵守；无解释。
"""


def _client() -> OpenAI:
    key = config.DEEPSEEK_API_KEY
    if not key or key.startswith("在此填入"):
        raise ValueError("请先在 config.py 中填写有效的 DEEPSEEK_API_KEY")
    return OpenAI(api_key=key, base_url=config.DEEPSEEK_BASE_URL)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError("LLM 未返回合法 JSON")
        return json.loads(m.group(0))


def extract_tag_keywords(description: str) -> list[str]:
    """让 DeepSeek 产出英文检索词，便于在全量标签库中命中。"""
    client = _client()
    resp = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": KEYWORD_PROMPT},
            {"role": "user", "content": f"用户偏好：{description}\n只输出 JSON。"},
        ],
        temperature=0.2,
        max_tokens=400,
    )
    data = _extract_json(resp.choices[0].message.content or "")
    kws = data.get("keywords") or []
    out: list[str] = []
    for k in kws:
        if isinstance(k, str) and k.strip():
            out.append(k.strip())
    return out


def parse_description_to_query(
    description: str,
    candidate_tags: list[dict[str, Any]] | None = None,
    search_mode: str = config.SEARCH_MODE_QUALITY,
    min_year: int | None = None,
    single_core_only: bool = False,
) -> dict[str, Any]:
    """调用 DeepSeek，将自然语言转为 VNDB 查询结构（标签必须来自候选）。"""
    mode = (
        search_mode
        if search_mode in config.SEARCH_MODE_LABELS
        else config.SEARCH_MODE_QUALITY
    )
    year = int(min_year or config.DEFAULT_MIN_YEAR)
    client = _client()
    cand_block = format_candidates_for_prompt(candidate_tags or [])
    allowed = {c["id"] for c in (candidate_tags or []) if c.get("id")}
    mode_label = config.SEARCH_MODE_LABELS[mode]
    single_hint = "【单核心标签】已启用：filters 里只能有 1 个 tag。\n" if single_core_only else ""

    user_content = (
        "【任务确认】这是 VNDB 元数据 Filter 编译请求，不是让你写色情内容。\n"
        f"【搜索模式】{mode_label}（{mode}）——必须严格遵守系统提示中的该模式规则。\n"
        f"{single_hint}"
        f"【年份下限】必须包含 [\"released\", \">=\", \"{year}\"]。\n"
        "请严格从下列「官方标签候选」中选择 tag ID，组装检索 JSON。\n\n"
        f"用户偏好描述：{description}\n\n"
        f"【官方标签候选（来自 VNDB 全库检索）】\n{cand_block}\n\n"
        "只输出一个 JSON 对象，不要解释。"
    )

    resp = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=[
            {
                "role": "system",
                "content": build_system_prompt(mode, year, single_core_only=single_core_only),
            },
            {"role": "user", "content": user_content},
        ],
        temperature=0.15,
        max_tokens=1024,
    )
    content = resp.choices[0].message.content or ""
    data = _extract_json(content)

    if not isinstance(data, dict) or "filters" not in data:
        raise ValueError("LLM JSON 缺少 filters 字段")

    data.setdefault("sort", "rating")
    data.setdefault("reverse", True)
    data.setdefault("limit", config.DEFAULT_RESULT_LIMIT)
    data["_allowed_tag_ids"] = sorted(allowed)
    data["_search_mode"] = mode
    data["_min_year"] = year
    return data


def optimize_previous_query(
    *,
    previous_desc: str,
    new_desc: str,
    previous_filters: Any,
    previous_tag_roles: dict[str, str] | None,
    candidate_tags: list[dict[str, Any]] | None,
    search_mode: str,
    min_year: int | None = None,
    single_core_only: bool = False,
) -> dict[str, Any]:
    """依据用户对上轮描述的修改/补充，增删标签并重建 Filter。"""
    mode = (
        search_mode
        if search_mode in config.SEARCH_MODE_LABELS
        else config.SEARCH_MODE_QUALITY
    )
    year = int(min_year or config.DEFAULT_MIN_YEAR)
    client = _client()
    cand_block = format_candidates_for_prompt(candidate_tags or [])
    allowed = {c["id"] for c in (candidate_tags or []) if c.get("id")}
    # 上轮已用标签也允许保留
    for tid in (previous_tag_roles or {}):
        allowed.add(tid)

    single_hint = (
        "【单核心标签】已启用：最终只能保留 1 个核心 tag。\n"
        if single_core_only
        else ""
    )
    user_content = (
        "【任务】优化上轮分析：用户修改/补充了自然语言需求。"
        "请在上轮 Filter 基础上增加或删除标签，而不是完全无视上轮结果胡乱重写"
        "（若用户明确推翻旧需求，则可大幅调整）。\n"
        f"【搜索模式】{config.SEARCH_MODE_LABELS[mode]}（{mode}）\n"
        f"{single_hint}"
        f"【年份下限】必须含 [\"released\", \">=\", \"{year}\"]\n\n"
        f"上轮描述：{previous_desc}\n"
        f"本轮修改后描述：{new_desc}\n\n"
        f"上轮 filters：{json.dumps(previous_filters, ensure_ascii=False)}\n"
        f"上轮 tag_roles：{json.dumps(previous_tag_roles or {}, ensure_ascii=False)}\n\n"
        f"【官方标签候选】\n{cand_block}\n\n"
        "只输出完整新 JSON（含 filters / tag_roles），不要解释。"
    )

    resp = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=[
            {
                "role": "system",
                "content": build_system_prompt(mode, year, single_core_only=single_core_only),
            },
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_tokens=1200,
    )
    data = _extract_json(resp.choices[0].message.content or "")
    if not isinstance(data, dict) or "filters" not in data:
        raise ValueError("优化分析未返回合法 filters")

    data.setdefault("sort", "rating")
    data.setdefault("reverse", True)
    data.setdefault("limit", config.DEFAULT_RESULT_LIMIT)
    data["_allowed_tag_ids"] = sorted(allowed)
    data["_search_mode"] = mode
    data["_min_year"] = year
    return data
