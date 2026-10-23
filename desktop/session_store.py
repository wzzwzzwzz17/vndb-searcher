# -*- coding: utf-8 -*-
"""本轮会话状态：未明确「新一轮」前保留分析结果与结果列表。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class SearchSession:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.active = False
        self.user_desc = ""
        self.raw_input = ""
        self.search_mode = ""
        self.min_year = 0
        self.single_core_only = False
        self.query: dict[str, Any] = {}
        self.filters: Any = None
        self.tag_roles: dict[str, str] = {}
        self.tag_zh_rows: list[dict[str, Any]] = []
        self.candidates: list[dict[str, Any]] = []
        self.items: list[dict[str, Any]] = []
        self.seen_ids: list[str] = []
        self.page = 1
        self.meta: dict[str, Any] = {}

    def has_round(self) -> bool:
        return self.active and bool(self.filters)

    def start_new(
        self,
        *,
        raw_input: str,
        user_desc: str,
        search_mode: str,
        min_year: int,
        single_core_only: bool,
        query: dict[str, Any],
        items: list[dict[str, Any]],
        tag_zh_rows: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        meta: dict[str, Any],
    ) -> None:
        self.clear()
        self.active = True
        self.raw_input = raw_input
        self.user_desc = user_desc
        self.search_mode = search_mode
        self.min_year = min_year
        self.single_core_only = bool(single_core_only)
        self.query = deepcopy(query)
        self.filters = deepcopy(query.get("filters"))
        self.tag_roles = dict(query.get("tag_roles") or {})
        self.tag_zh_rows = list(tag_zh_rows)
        self.candidates = list(candidates)
        self.items = list(items)
        self.seen_ids = [it.get("id") for it in items if it.get("id")]
        self.page = 1
        self.meta = dict(meta)

    def update_after_refresh(self, items: list[dict[str, Any]], page: int, meta: dict[str, Any]) -> None:
        self.items = list(items)
        self.page = page
        for it in items:
            vid = it.get("id")
            if vid and vid not in self.seen_ids:
                self.seen_ids.append(vid)
        self.meta = dict(meta)

    def update_after_optimize(
        self,
        *,
        raw_input: str,
        user_desc: str,
        query: dict[str, Any],
        items: list[dict[str, Any]],
        tag_zh_rows: list[dict[str, Any]],
        meta: dict[str, Any],
        single_core_only: bool | None = None,
    ) -> None:
        self.raw_input = raw_input
        self.user_desc = user_desc
        self.query = deepcopy(query)
        self.filters = deepcopy(query.get("filters"))
        self.tag_roles = dict(query.get("tag_roles") or {})
        self.tag_zh_rows = list(tag_zh_rows)
        self.items = list(items)
        if single_core_only is not None:
            self.single_core_only = bool(single_core_only)
        for it in items:
            vid = it.get("id")
            if vid and vid not in self.seen_ids:
                self.seen_ids.append(vid)
        self.page = 1
        self.meta = dict(meta)
        self.active = True


SESSION = SearchSession()
