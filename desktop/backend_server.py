# -*- coding: utf-8 -*-
"""本地后端：新一轮 / 刷新 / 优化上轮 / 详情 / 单核心 / 中文简介与高权重标签。"""

from __future__ import annotations

from typing import Any

import config
from community_scores import attach_community_scores, score_line
from llm_parser import (
    extract_tag_keywords,
    optimize_previous_query,
    parse_description_to_query,
)
from result_enrich import enrich_result_items
from session_store import SESSION
from tag_catalog import ensure_tag_catalog
from tag_i18n import (
    extract_tag_ids_from_filters,
    format_tags_zh_line,
    translate_tags_zh,
)
from tag_matcher import find_candidate_tags
from vndb_api import (
    FilterValidationError,
    VndbClient,
    ensure_japanese_original,
    ensure_min_year_filter,
    filter_items_by_min_year,
    filter_items_japanese,
    force_single_core_query,
    relax_filters,
    validate_query,
)


class BackendError(Exception):
    pass


def extract_user_description(raw_text: str) -> str:
    text = (raw_text or "").strip()
    prefix = config.INPUT_PREFIX
    if not text.startswith(prefix):
        raise BackendError(f'输入必须以「{prefix}」开头')
    desc = text[len(prefix) :].strip()
    if not desc:
        raise BackendError("前缀后请填写具体偏好描述")
    return desc


def format_results(items: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    try:
        attach_community_scores(items)
        items.sort(key=lambda it: it.get("composite") if isinstance(it.get("composite"), (int, float)) else -1, reverse=True)
    except Exception:
        pass
    lines: list[str] = []
    lines.append("=" * 52)
    lines.append("Galgame 推荐结果（仅元数据，无资源链接）")
    lines.append("=" * 52)
    mode = meta.get("search_mode")
    if mode:
        lines.append(f"※ 搜索模式: {config.SEARCH_MODE_LABELS.get(mode, mode)}")
    if meta.get("single_core_only"):
        lines.append("※ 单核心标签: 已启用（仅按 1 个核心标签搜索）")
    if meta.get("min_year"):
        lines.append(f"※ 年份下限: {meta['min_year']} 年及以后")
    lines.append("※ 官方原始语言: 仅日语（olang=ja）")
    if meta.get("action"):
        lines.append(f"※ 本轮动作: {meta['action']}")
    if meta.get("relaxed"):
        lines.append("※ 首轮无结果，已自动宽松条件重试 1 次")
    if mode == config.SEARCH_MODE_HARDCORE and not items:
        lines.append("※ 硬核模式允许极端情况无结果（未强制放宽）")
    if meta.get("tag_zh_line"):
        lines.append(f"※ AI 搜索标签（中文）: {meta['tag_zh_line']}")
    if meta.get("tag_catalog_size"):
        lines.append(f"※ 标签库: {meta['tag_catalog_size']} 个官方标签")
    if meta.get("seen_count"):
        lines.append(f"※ 本会话已展示过: {meta['seen_count']} 部（刷新会尽量避开）")
    lines.append(f"匹配数量: {len(items)}")
    lines.append("")
    lines.append("提示: 双击列表项可查看剧情与出版社；勾选可加入收藏夹。")
    lines.append("简介已译为中文；每款附带「其它高权重标签」前4（不含本次搜索标签）。")
    lines.append("")

    if not items:
        lines.append("未找到匹配作品。可尝试刷新、优化上轮分析、换模式或放宽条件。")
        return "\n".join(lines)

    for i, it in enumerate(items, 1):
        rating = it.get("rating")
        rating_s = f"{rating:.2f}" if isinstance(rating, (int, float)) else "N/A"
        title = it.get("title") or "(无标题)"
        original = it.get("original") or ""
        released = it.get("released") or "未知"
        url = it.get("url") or ""
        desc = it.get("description") or "（无简介）"
        top_zh = it.get("top_tags_zh") or "（暂无）"

        lines.append(f"【{i}】{title}")
        if original:
            lines.append(f"    原名: {original}")
        lines.append(f"    评分: {score_line(it)}  |  发售: {released}")
        lines.append(f"    链接: {url}")
        lines.append(f"    其它高权重标签: {top_zh}")
        lines.append(f"    简介: {desc}")
        lines.append("")

    lines.append("-" * 52)
    lines.append("数据来源: VNDB 评分 + 鲲 Galgame / TouchGal 评分（取得到才计入综合分）")
    return "\n".join(lines)


def _resolve_candidate_tags(desc: str) -> tuple[list[dict[str, Any]], list[str], int]:
    catalog = ensure_tag_catalog()
    candidates = find_candidate_tags(desc, top_k=40)
    keywords: list[str] = []
    if len(candidates) < 8:
        try:
            keywords = extract_tag_keywords(desc)
        except Exception:
            keywords = []
        if keywords:
            candidates = find_candidate_tags(desc, top_k=50, extra_keywords=keywords)
    return candidates, keywords, len(catalog)


def _build_tag_zh(query: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    ids = extract_tag_ids_from_filters(query.get("filters"))
    for tid in (query.get("tag_roles") or {}):
        if tid not in ids:
            ids.append(tid)
    rows = translate_tags_zh(ids, query.get("tag_roles") or {})
    return rows, format_tags_zh_line(rows)


def _search_tag_id_set(query: dict[str, Any]) -> set[str]:
    ids = set(extract_tag_ids_from_filters(query.get("filters")))
    ids.update((query.get("tag_roles") or {}).keys())
    return ids


def _enrich(items: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    return enrich_result_items(
        items,
        _search_tag_id_set(query),
        top_n=config.RESULT_TOP_TAG_COUNT,
    )


def _run_vndb(
    query: dict[str, Any],
    mode: str,
    year: int,
    *,
    page: int = 1,
    exclude_ids: list[str] | None = None,
    allow_relax: bool = True,
) -> tuple[list[dict[str, Any]], bool, int]:
    client = VndbClient()
    vndb_calls = 0
    relaxed = False

    q = dict(query)
    q["filters"] = ensure_japanese_original(q.get("filters"))
    q["filters"] = ensure_min_year_filter(q.get("filters"), year)
    body = validate_query(q)
    q["filters"] = body["filters"]

    items = client.search_vn(
        q, use_cache=(page == 1 and not exclude_ids), page=page, exclude_ids=exclude_ids
    )
    vndb_calls += 1

    if (
        not items
        and allow_relax
        and mode != config.SEARCH_MODE_HARDCORE
        and vndb_calls < config.MAX_SESSION_CALL
        and page == 1
        and not exclude_ids
    ):
        relaxed_q = dict(q)
        relaxed_q["filters"] = ensure_japanese_original(
            relax_filters(q["filters"], search_mode=mode)
        )
        relaxed_q["filters"] = ensure_min_year_filter(relaxed_q["filters"], year)
        try:
            validate_query(relaxed_q)
            items = client.search_vn(relaxed_q, use_cache=False)
            vndb_calls += 1
            relaxed = True
        except FilterValidationError:
            pass

    items = filter_items_by_min_year(items, year)
    items = filter_items_japanese(items)
    if exclude_ids:
        ban = set(exclude_ids)
        items = [it for it in items if it.get("id") not in ban]
    return items, relaxed, vndb_calls


def recommend_new_round(
    raw_user_input: str,
    search_mode: str = config.SEARCH_MODE_QUALITY,
    min_year: int | None = None,
    single_core_only: bool = False,
) -> dict[str, Any]:
    mode = (
        search_mode
        if search_mode in config.SEARCH_MODE_LABELS
        else config.SEARCH_MODE_QUALITY
    )
    year = int(min_year or config.DEFAULT_MIN_YEAR)
    desc = extract_user_description(raw_user_input)

    try:
        candidates, keywords, catalog_size = _resolve_candidate_tags(desc)
    except Exception as e:
        raise BackendError(f"标签库检索失败: {e}") from e

    try:
        query = parse_description_to_query(
            desc,
            candidates,
            search_mode=mode,
            min_year=year,
            single_core_only=single_core_only,
        )
    except Exception as e:
        raise BackendError(f"DeepSeek 解析失败: {e}") from e

    if single_core_only:
        query = force_single_core_query(query)

    try:
        items, relaxed, vndb_calls = _run_vndb(query, mode, year, allow_relax=True)
    except Exception as e:
        raise BackendError(f"VNDB 查询失败: {e}") from e

    try:
        items = _enrich(items, query)
    except Exception as e:
        raise BackendError(f"结果 enrichment 失败: {e}") from e

    tag_zh_rows, tag_zh_line = _build_tag_zh(query)
    meta = {
        "action": "新一轮搜索",
        "relaxed": relaxed,
        "search_mode": mode,
        "min_year": year,
        "single_core_only": single_core_only,
        "tag_catalog_size": catalog_size,
        "keywords": keywords,
        "tag_roles": query.get("tag_roles") or {},
        "tag_zh_line": tag_zh_line,
        "vndb_calls": vndb_calls,
        "filters": query.get("filters"),
        "seen_count": len(items),
    }
    text = format_results(items, meta)

    SESSION.start_new(
        raw_input=raw_user_input,
        user_desc=desc,
        search_mode=mode,
        min_year=year,
        single_core_only=single_core_only,
        query=query,
        items=items,
        tag_zh_rows=tag_zh_rows,
        candidates=candidates,
        meta=meta,
    )
    return {
        "text": text,
        "items": items,
        "meta": meta,
        "tag_zh_rows": tag_zh_rows,
        "session_active": True,
    }


def refresh_round() -> dict[str, Any]:
    if not SESSION.has_round():
        raise BackendError("当前没有可刷新的上轮结果，请先「开始新一轮搜索」。")

    mode = SESSION.search_mode
    year = SESSION.min_year
    query = dict(SESSION.query)
    exclude = list(SESSION.seen_ids)
    page = SESSION.page + 1

    try:
        items, relaxed, vndb_calls = _run_vndb(
            query,
            mode,
            year,
            page=page,
            exclude_ids=exclude,
            allow_relax=False,
        )
        if not items:
            items, relaxed, extra = _run_vndb(
                query,
                mode,
                year,
                page=1,
                exclude_ids=exclude,
                allow_relax=False,
            )
            vndb_calls += extra
            page = 1
    except Exception as e:
        raise BackendError(f"刷新失败: {e}") from e

    try:
        items = _enrich(items, query)
    except Exception as e:
        raise BackendError(f"结果 enrichment 失败: {e}") from e

    tag_zh_rows = SESSION.tag_zh_rows
    tag_zh_line = format_tags_zh_line(tag_zh_rows)
    meta = {
        "action": "刷新结果（避重）",
        "relaxed": relaxed,
        "search_mode": mode,
        "min_year": year,
        "single_core_only": SESSION.single_core_only,
        "tag_zh_line": tag_zh_line,
        "tag_roles": SESSION.tag_roles,
        "vndb_calls": vndb_calls,
        "filters": query.get("filters"),
        "seen_count": len(SESSION.seen_ids) + len(items),
        "tag_catalog_size": SESSION.meta.get("tag_catalog_size"),
    }
    text = format_results(items, meta)
    SESSION.update_after_refresh(items, page, meta)
    return {
        "text": text,
        "items": items,
        "meta": meta,
        "tag_zh_rows": tag_zh_rows,
        "session_active": True,
    }


def optimize_round(
    new_raw_input: str,
    single_core_only: bool | None = None,
) -> dict[str, Any]:
    if not SESSION.has_round():
        raise BackendError("没有上轮分析可优化，请先「开始新一轮搜索」。")

    desc = extract_user_description(new_raw_input)
    mode = SESSION.search_mode
    year = SESSION.min_year
    single = (
        SESSION.single_core_only if single_core_only is None else bool(single_core_only)
    )

    try:
        new_cands, _, catalog_size = _resolve_candidate_tags(desc)
    except Exception as e:
        raise BackendError(f"标签库检索失败: {e}") from e
    merged = {c["id"]: c for c in SESSION.candidates if c.get("id")}
    for c in new_cands:
        if c.get("id"):
            merged[c["id"]] = c
    candidates = list(merged.values())

    try:
        query = optimize_previous_query(
            previous_desc=SESSION.user_desc,
            new_desc=desc,
            previous_filters=SESSION.filters,
            previous_tag_roles=SESSION.tag_roles,
            candidate_tags=candidates,
            search_mode=mode,
            min_year=year,
            single_core_only=single,
        )
    except Exception as e:
        raise BackendError(f"优化分析失败: {e}") from e

    if single:
        query = force_single_core_query(query)

    try:
        items, relaxed, vndb_calls = _run_vndb(
            query,
            mode,
            year,
            exclude_ids=list(SESSION.seen_ids),
            allow_relax=(mode != config.SEARCH_MODE_HARDCORE),
        )
    except Exception as e:
        raise BackendError(f"VNDB 查询失败: {e}") from e

    try:
        items = _enrich(items, query)
    except Exception as e:
        raise BackendError(f"结果 enrichment 失败: {e}") from e

    tag_zh_rows, tag_zh_line = _build_tag_zh(query)
    meta = {
        "action": "优化上轮分析",
        "relaxed": relaxed,
        "search_mode": mode,
        "min_year": year,
        "single_core_only": single,
        "tag_catalog_size": catalog_size,
        "tag_roles": query.get("tag_roles") or {},
        "tag_zh_line": tag_zh_line,
        "vndb_calls": vndb_calls,
        "filters": query.get("filters"),
        "seen_count": len(SESSION.seen_ids),
    }
    text = format_results(items, meta)
    SESSION.update_after_optimize(
        raw_input=new_raw_input,
        user_desc=desc,
        query=query,
        items=items,
        tag_zh_rows=tag_zh_rows,
        meta=meta,
        single_core_only=single,
    )
    SESSION.candidates = candidates
    return {
        "text": text,
        "items": items,
        "meta": meta,
        "tag_zh_rows": tag_zh_rows,
        "session_active": True,
    }


def fetch_vn_detail(vn_id: str) -> dict[str, Any]:
    try:
        detail = VndbClient().get_vn_detail(vn_id)
        # 详情简介也译中文；高权重标签同样附上（排除当前会话搜索标签）
        exclude = _search_tag_id_set(SESSION.query) if SESSION.has_round() else set()
        enriched = enrich_result_items([detail], exclude, top_n=config.RESULT_TOP_TAG_COUNT)
        return enriched[0]
    except Exception as e:
        raise BackendError(f"获取详情失败: {e}") from e


def recommend(*args, **kwargs):
    return recommend_new_round(*args, **kwargs)
