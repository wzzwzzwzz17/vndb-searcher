# -*- coding: utf-8 -*-
"""本地/云服务器入口：只提供推荐网页和评分查询，不转发下载或磁力链接。"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.abspath(os.path.join(ROOT, "..", "desktop"))
if SKILL not in sys.path:
    sys.path.insert(0, SKILL)

import config  # noqa: E402
from backend_server import BackendError, recommend_new_round  # noqa: E402
from community_scores import fetch_work_profile  # noqa: E402
from name_lookup import lookup_name  # noqa: E402
from tag_precision import load_tree, search_weighted_tags  # noqa: E402
from tag_matcher import find_candidate_tags  # noqa: E402
from ggbase import search_ggbase  # noqa: E402

INDEX = os.path.join(ROOT, "index.html")
NAME_PAGE = os.path.join(ROOT, "name.html")
TAG_PAGE = os.path.join(ROOT, "tags.html")
HOST = os.environ.get("VNDB_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("VNDB_WEB_PORT", "8787"))


def _slim(item: dict) -> dict:
    community = item.get("community") or {}
    safe_sites = {}
    for key in ("kungal", "touchgal"):
        row = community.get(key) or {}
        safe_sites[key] = {
            "ok": bool(row.get("ok")),
            "score": row.get("score"),
            "votes": row.get("votes"),
            "reason": row.get("reason") or "",
            "name": row.get("name") or "",
        }
    return {
        "id": item.get("id") or "",
        "title": item.get("title") or "",
        "original": item.get("original") or "",
        "rating": item.get("rating"),
        "composite": item.get("composite"),
        "released": item.get("released") or "",
        "description": item.get("description") or "",
        "url": item.get("url") or "",
        "top_tags_zh": item.get("top_tags_zh") or "",
        "developers": item.get("developers") or [],
        "community": safe_sites,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        pages = {
            "/": INDEX,
            "/index.html": INDEX,
            "/name": NAME_PAGE,
            "/name.html": NAME_PAGE,
            "/tags": TAG_PAGE,
            "/tags.html": TAG_PAGE,
        }
        target = pages.get(path)
        if target is None:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        with open(target, "rb") as handle:
            self._send(200, handle.read(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "请求不是合法 JSON"})
            return
        if path == "/api/name-search":
            if (payload.get("api_key") or "").strip():
                config.DEEPSEEK_API_KEY = payload["api_key"].strip()
            try:
                found = lookup_name(payload.get("name") or "", bool(payload.get("must_find")))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
                return
            self._json(
                200,
                {
                    "elapsed": found["elapsed"],
                    "bing_ok": found["bing_ok"],
                    "analysis": found["analysis"],
                    "shinnku": found["shinnku"],
                    "items": [_slim(it) for it in found["items"]],
                },
            )
            return
        if path == "/api/tag-tree":
            try:
                self._json(200, {"roots": load_tree()})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
            return
        if path == "/api/tag-nl":
            if (payload.get("api_key") or "").strip():
                config.DEEPSEEK_API_KEY = payload["api_key"].strip()
            text = (payload.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "请输入想找的标签"})
                return
            hits = find_candidate_tags(text, top_k=12)
            from tag_precision import label_of, _zh_map

            names = _zh_map()
            self._json(
                200,
                {
                    "tags": [
                        {
                            "id": hit["id"],
                            "zh": label_of(hit["id"], hit.get("name") or "", names),
                            "en": hit.get("name") or "",
                        }
                        for hit in hits
                    ]
                },
            )
            return
        if path == "/api/tag-search":
            if (payload.get("api_key") or "").strip():
                config.DEEPSEEK_API_KEY = payload["api_key"].strip()
            try:
                items = search_weighted_tags(payload.get("tags") or [], payload.get("exclude") or [])
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
                return
            self._json(200, {"items": [_slim(it) for it in items]})
            return
        if path == "/api/ggbase":
            try:
                found = search_ggbase(
                    payload.get("original") or "",
                    payload.get("title") or "",
                    bool(payload.get("must_find")),
                )
            except Exception:  # noqa: BLE001
                found = {"ok": False, "reason": "GGBase 暂时连不上", "pages": []}
            self._json(200, found)
            return
        if path == "/api/detail":
            profile = fetch_work_profile(
                payload.get("title") or "",
                payload.get("original") or "",
                payload.get("touchgal_token") or "",
                bool(payload.get("must_find")),
            )
            try:
                profile["ggbase"] = search_ggbase(
                    payload.get("original") or "",
                    payload.get("title") or "",
                    bool(payload.get("must_find")),
                )
            except Exception:  # noqa: BLE001
                profile["ggbase"] = {"ok": False, "reason": "GGBase 暂时连不上", "pages": []}
            self._json(200, profile)
            return
        if path != "/api/recommend":
            self._send(404, b"{}", "application/json; charset=utf-8")
            return
        text = (payload.get("text") or "").strip()
        if not text.startswith(config.INPUT_PREFIX):
            text = config.INPUT_PREFIX + text
        key = (payload.get("api_key") or "").strip()
        if key:
            config.DEEPSEEK_API_KEY = key
        touch_token = (payload.get("touchgal_token") or "").strip()
        if touch_token:
            config.TOUCHGAL_API_TOKEN = touch_token
        try:
            result = recommend_new_round(
                text,
                search_mode=payload.get("mode") or "quality",
                min_year=int(payload.get("year") or config.DEFAULT_MIN_YEAR),
                single_core_only=bool(payload.get("single_core")),
            )
        except BackendError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})
            return
        meta = result.get("meta") or {}
        self._json(
            200,
            {
                "tag_line": meta.get("tag_zh_line") or "",
                "mode": meta.get("search_mode") or "",
                "items": [_slim(it) for it in result.get("items") or []],
            },
        )

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"VNDB搜索器网页 http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
