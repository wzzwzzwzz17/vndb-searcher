# -*- coding: utf-8 -*-
"""SQLite 持久化：已入库、重试队列、批次配额、总量统计。

设计要点（对应方案文档第三、五节）：
- total_size 只统计 done 表里登记的资源；每次启动用 SELECT SUM(size_gb) 兜底校准。
- 去重主键是 vndb_id：任意一个源成功就记 done，之后整部作品不再处理。
- C 源（直链下载）单独记批次流量，达到配额只暂停 C，不影响 A/B。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS done (
    vndb_id       TEXT PRIMARY KEY,
    rating        REAL,
    title         TEXT,
    source_type   TEXT NOT NULL,
    site          TEXT,
    resource_link TEXT,
    cloud_path    TEXT,
    size_gb       REAL NOT NULL DEFAULT 0,
    finish_ts     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS retry (
    vndb_id     TEXT NOT NULL,
    source_type TEXT NOT NULL,
    site        TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    next_ts     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (vndb_id, source_type)
);

CREATE TABLE IF NOT EXISTS runtime_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS vn_seen (
    vndb_id      TEXT PRIMARY KEY,
    rating       REAL,
    title        TEXT,
    first_seen_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS magnet_pending (
    vndb_id  TEXT PRIMARY KEY,
    title    TEXT,
    magnet   TEXT NOT NULL,
    size_gb  REAL,
    site     TEXT,
    note     TEXT,
    ts       INTEGER NOT NULL
);

-- 已知是诱饵/空壳的分享链接，别再浪费一次转存
CREATE TABLE IF NOT EXISTS bad_link (
    link   TEXT PRIMARY KEY,
    reason TEXT,
    ts     INTEGER NOT NULL
);

-- 三个渠道都没捞到的作品，导出成根目录的文档给人看
CREATE TABLE IF NOT EXISTS missing (
    vndb_id TEXT PRIMARY KEY,
    title   TEXT,
    rating  REAL,
    reason  TEXT,
    ts      INTEGER NOT NULL
);

-- 转存成功时登记的解压码（archive_password）与分享提取码
CREATE TABLE IF NOT EXISTS passwords (
    vndb_id           TEXT PRIMARY KEY,
    title             TEXT,
    archive_password  TEXT,
    share_pwd         TEXT,
    site              TEXT,
    detail_url        TEXT,
    resource_link     TEXT,
    ts                INTEGER NOT NULL
);
"""


class TaskDB:
    """线程安全的薄封装；调度器只有一个写者，但爬虫线程会并发读 done。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 基础 ----------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "TaskDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ---------- done ----------
    def is_done(self, vndb_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM done WHERE vndb_id = ?", (vndb_id,)))

    def done_ids(self) -> set[str]:
        return {row["vndb_id"] for row in self._query("SELECT vndb_id FROM done")}

    def get_done(self, vndb_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM done WHERE vndb_id = ?", (vndb_id,))
        return dict(rows[0]) if rows else None

    def mark_done(
        self,
        vndb_id: str,
        rating: float | None,
        title: str,
        source_type: str,
        site: str,
        resource_link: str,
        cloud_path: str,
        size_gb: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO done (vndb_id, rating, title, source_type, site, resource_link,
                                  cloud_path, size_gb, finish_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vndb_id) DO UPDATE SET
                    rating=excluded.rating, title=excluded.title,
                    source_type=excluded.source_type, site=excluded.site,
                    resource_link=excluded.resource_link, cloud_path=excluded.cloud_path,
                    size_gb=excluded.size_gb, finish_ts=excluded.finish_ts
                """,
                (vndb_id, rating, title, source_type, site, resource_link, cloud_path,
                 float(size_gb or 0), int(time.time())),
            )
            self._conn.execute("DELETE FROM retry WHERE vndb_id = ?", (vndb_id,))
            self._conn.execute("DELETE FROM magnet_pending WHERE vndb_id = ?", (vndb_id,))
            self._conn.execute("DELETE FROM missing WHERE vndb_id = ?", (vndb_id,))
            self._conn.commit()

    def total_gb(self) -> float:
        """只统计本程序登记的资源，不含网盘里其它文件（方案第七节第 4 条）。"""
        rows = self._query("SELECT COALESCE(SUM(size_gb), 0) AS total FROM done")
        return round(float(rows[0]["total"]), 3)

    def recompute_total(self) -> float:
        """启动时兜底：以 done 表求和为准，回写 runtime_meta 缓存。"""
        total = self.total_gb()
        self.meta_set("total_gb", f"{total:.3f}")
        return total

    def count_done(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM done")[0]["n"])

    def count_by_source(self) -> dict[str, int]:
        rows = self._query("SELECT source_type, COUNT(*) AS n FROM done GROUP BY source_type")
        return {row["source_type"]: int(row["n"]) for row in rows}

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM done ORDER BY finish_ts DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def all_done(self) -> list[dict[str, Any]]:
        """全部入库记录（整理云端目录结构时用）。"""
        return [dict(row) for row in self._query("SELECT * FROM done ORDER BY finish_ts")]

    def forget_done(self, vndb_id: str) -> bool:
        """删掉一条入库记录（内容判为诱饵、删了云端目录之后用），返回是否删到了。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM done WHERE vndb_id = ?", (str(vndb_id),))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------- 已知坏链接 ----------
    def add_bad_link(self, link: str, reason: str = "") -> None:
        """记下这个分享/直链是诱饵或空壳，下次直接跳过（同一条链接不再转存）。"""
        if not link:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO bad_link (link, reason, ts) VALUES (?,?,?) "
                "ON CONFLICT(link) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                (str(link), str(reason)[:200], int(time.time())),
            )
            self._conn.commit()

    def is_bad_link(self, link: str) -> bool:
        if not link:
            return False
        row = self._query("SELECT 1 FROM bad_link WHERE link = ?", (str(link),))
        return bool(row)

    def count_bad_links(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM bad_link")
        return int(rows[0]["n"]) if rows else 0

    def bad_links(self, limit: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM bad_link ORDER BY ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in self._query(sql)]

    # ---------- 三个渠道都没捞到 ----------
    def add_missing(self, vndb_id: str, title: str, rating: float = 0.0, reason: str = "") -> None:
        """记下这部作品三渠道都没成功，导出成根目录文档给人看。"""
        if not vndb_id:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO missing (vndb_id, title, rating, reason, ts) VALUES (?,?,?,?,?) "
                "ON CONFLICT(vndb_id) DO UPDATE SET title=excluded.title, rating=excluded.rating, "
                "reason=excluded.reason, ts=excluded.ts",
                (str(vndb_id), str(title)[:200], float(rating or 0), str(reason)[:400], int(time.time())),
            )
            self._conn.commit()

    def forget_missing(self, vndb_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM missing WHERE vndb_id = ?", (str(vndb_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def missing(self, limit: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM missing ORDER BY rating DESC, ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in self._query(sql)]

    def count_missing(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM missing")
        return int(rows[0]["n"]) if rows else 0

    # ---------- 解压码 ----------
    def set_password(
        self,
        vndb_id: str,
        title: str,
        archive_password: str = "",
        share_pwd: str = "",
        site: str = "kungal",
        detail_url: str = "",
        resource_link: str = "",
    ) -> None:
        """转存成功后登记解压码；同一部作品覆盖更新。"""
        if not vndb_id:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO passwords (vndb_id, title, archive_password, share_pwd, site,
                                      detail_url, resource_link, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vndb_id) DO UPDATE SET
                    title=excluded.title,
                    archive_password=excluded.archive_password,
                    share_pwd=excluded.share_pwd,
                    site=excluded.site,
                    detail_url=excluded.detail_url,
                    resource_link=excluded.resource_link,
                    ts=excluded.ts
                """,
                (
                    str(vndb_id),
                    str(title or "")[:200],
                    str(archive_password or "")[:120],
                    str(share_pwd or "")[:64],
                    str(site or "")[:40],
                    str(detail_url or "")[:400],
                    str(resource_link or "")[:500],
                    int(time.time()),
                ),
            )
            self._conn.commit()

    def passwords(self, limit: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM passwords ORDER BY ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in self._query(sql)]

    def count_passwords(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM passwords")
        return int(rows[0]["n"]) if rows else 0

    def get_password(self, vndb_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM passwords WHERE vndb_id = ?", (str(vndb_id),))
        return dict(rows[0]) if rows else None

    # ---------- retry ----------
    def add_retry(
        self,
        vndb_id: str,
        source_type: str,
        site: str,
        error: str,
        max_retry: int = 3,
    ) -> int:
        """记一次失败并安排下次重试；返回累计失败次数。

        超过 max_retry 次就放弃（删掉这条记录），避免永久失败的条目反复占用队列。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM retry WHERE vndb_id = ? AND source_type = ?",
                (vndb_id, source_type),
            ).fetchone()
            attempts = int(row["attempts"]) + 1 if row else 1
            if attempts > int(max_retry):
                self._conn.execute(
                    "DELETE FROM retry WHERE vndb_id = ? AND source_type = ?",
                    (vndb_id, source_type),
                )
                self._conn.commit()
                return attempts
            delay = min(3600, 60 * (2 ** max(0, attempts - 1)))
            self._conn.execute(
                """
                INSERT INTO retry (vndb_id, source_type, site, attempts, last_error, next_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(vndb_id, source_type) DO UPDATE SET
                    site=excluded.site, attempts=excluded.attempts,
                    last_error=excluded.last_error, next_ts=excluded.next_ts
                """,
                (vndb_id, source_type, site, attempts, str(error)[:500], int(time.time()) + delay),
            )
            self._conn.commit()
            return attempts

    def due_retries(self, now: int | None = None) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM retry WHERE next_ts <= ? ORDER BY attempts, next_ts",
                           (int(now or time.time()),))
        return [dict(row) for row in rows]

    def clear_retry(self, vndb_id: str, source_type: str) -> None:
        self._execute("DELETE FROM retry WHERE vndb_id = ? AND source_type = ?", (vndb_id, source_type))

    def retry_count(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM retry")[0]["n"])

    # ---------- meta / 配额 ----------
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM runtime_meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def meta_set(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO runtime_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def c_quota_used_gb(self) -> float:
        return float(self.meta_get("c_batch_gb", "0") or 0)

    def add_c_gb(self, gb: float) -> float:
        used = round(self.c_quota_used_gb() + max(0.0, float(gb)), 3)
        self.meta_set("c_batch_gb", f"{used:.3f}")
        if not self.meta_get("c_batch_started_ts"):
            self.meta_set("c_batch_started_ts", str(int(time.time())))
        return used

    def reset_c_quota(self) -> None:
        self.meta_set("c_batch_gb", "0")
        self.meta_set("c_batch_started_ts", str(int(time.time())))

    # ---------- 待处理磁力 ----------
    def add_pending_magnet(
        self,
        vndb_id: str,
        title: str,
        magnet: str,
        size_gb: float = 0.0,
        site: str = "",
        note: str = "",
    ) -> None:
        """记下没能自动落地的磁力（百度网盘已不接受磁力离线），供人工处理。"""
        if not magnet:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO magnet_pending (vndb_id, title, magnet, size_gb, site, note, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vndb_id) DO UPDATE SET
                    title=excluded.title, magnet=excluded.magnet, size_gb=excluded.size_gb,
                    site=excluded.site, note=excluded.note, ts=excluded.ts
                """,
                (vndb_id, title, magnet, float(size_gb or 0), site, str(note)[:300], int(time.time())),
            )
            self._conn.commit()

    def pending_magnets(self, limit: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM magnet_pending ORDER BY ts DESC"
        params: tuple[Any, ...] = ()
        if limit:
            sql += " LIMIT ?"
            params = (int(limit),)
        return [dict(row) for row in self._query(sql, params)]

    def count_pending_magnets(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM magnet_pending")[0]["n"])

    # ---------- 观察 ----------
    def mark_seen(self, vndb_id: str, rating: float | None, title: str) -> None:
        self._execute(
            "INSERT INTO vn_seen (vndb_id, rating, title, first_seen_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vndb_id) DO UPDATE SET rating=excluded.rating, title=excluded.title",
            (vndb_id, rating, title, int(time.time())),
        )

    def stats(self) -> dict[str, Any]:
        return {
            "entries": self.count_done(),
            "total_gb": self.total_gb(),
            "by_source": self.count_by_source(),
            "retry": self.retry_count(),
            "pending_magnet": self.count_pending_magnets(),
            "missing": self.count_missing(),
            "passwords": self.count_passwords(),
            "c_quota_gb": self.c_quota_used_gb(),
            "db": str(self.path),
        }
