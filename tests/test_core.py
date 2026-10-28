# -*- coding: utf-8 -*-
"""配置、数据库、标题匹配、体积解析、加密的离线测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from galsave.config import ConfigError, cget, cpath, load_config
from galsave.crawler.base import parse_size_gb
from galsave.crypto.enc import (
    CryptoError,
    decrypt_file,
    encrypt_file,
    encrypt_parts,
    is_encrypted,
)
from galsave.db import TaskDB
from galsave.title_match import best_match, is_match, normalize, search_terms, similarity


class ConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = load_config()
        self.assertEqual(cget(cfg, "max_total_gb"), 4000.0)
        self.assertEqual(cget(cfg, "source_priority"), ["share", "direct", "magnet"])
        self.assertGreaterEqual(cget(cfg, "fuzzy_match_threshold"), 0.0)

    def test_yaml_override_and_bucket_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text(
                "rating_buckets: [6.0, 8.0, 8.0, 7.0]\nmax_total_gb: 12.5\n",
                encoding="utf-8",
            )
            cfg = load_config(path)
            self.assertEqual(cget(cfg, "rating_buckets"), [8.0, 7.0, 6.0])
            self.assertEqual(cget(cfg, "max_total_gb"), 12.5)

    def test_fuzzy_accepts_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text("fuzzy_match_threshold: 0.9\n", encoding="utf-8")
            self.assertEqual(cget(load_config(path), "fuzzy_match_threshold"), 90.0)

    def test_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"GALSAVE_MAX_GB": "256"}):
            self.assertEqual(cget(load_config(), "max_total_gb"), 256.0)

    def test_env_invalid(self) -> None:
        with mock.patch.dict(os.environ, {"GALSAVE_MAX_GB": "abc"}):
            with self.assertRaises(ConfigError):
                load_config()

    def test_bad_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text("a: [1, 2\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_cpath_is_absolute(self) -> None:
        self.assertTrue(cpath(load_config(), "paths.db").is_absolute())


class DbTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = TaskDB(Path(self._tmp.name) / "t.db")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_bad_link_remembered(self) -> None:
        link = "https://pan.baidu.com/s/1bad?pwd=x"
        self.assertFalse(self.db.is_bad_link(link))
        self.db.add_bad_link(link, "课程/资料")
        self.assertTrue(self.db.is_bad_link(link))
        self.assertEqual(self.db.count_bad_links(), 1)
        # 重复记录不新增行
        self.db.add_bad_link(link, "又一次")
        self.assertEqual(self.db.count_bad_links(), 1)
        self.assertEqual(self.db.bad_links()[0]["reason"], "又一次")

    def test_forget_done_removes_row(self) -> None:
        self.db.mark_done("v9", 8.0, "A", "share", "kungal", "l", "/p", 1.0)
        self.assertTrue(self.db.forget_done("v9"))
        self.assertFalse(self.db.is_done("v9"))
        self.assertFalse(self.db.forget_done("v9"))

    def test_forget_done_persists(self) -> None:
        """删了要真落盘——曾经漏了 commit，删完重启又冒出来。"""
        path = Path(self._tmp.name) / "p.db"
        db = TaskDB(path)
        db.mark_done("v9", 8.0, "A", "share", "kungal", "l", "/p", 1.0)
        self.assertTrue(db.forget_done("v9"))
        db.close()
        again = TaskDB(path)
        try:
            self.assertEqual(again.count_done(), 0)
        finally:
            again.close()

    def test_mark_done_and_total(self) -> None:
        self.db.mark_done("v1", 8.5, "A", "magnet", "ggbase", "magnet:?xt=1", "/a", 12.5)
        self.db.mark_done("v2", 7.0, "B", "share", "kungal", "link", "/b", 7.5)
        self.assertTrue(self.db.is_done("v1"))
        self.assertFalse(self.db.is_done("v3"))
        self.assertEqual(self.db.count_done(), 2)
        self.assertAlmostEqual(self.db.total_gb(), 20.0, places=3)
        self.assertEqual(self.db.count_by_source()["magnet"], 1)

    def test_upsert_keeps_single_row(self) -> None:
        self.db.mark_done("v1", 8.0, "A", "magnet", "ggbase", "m", "/a", 1.0)
        self.db.mark_done("v1", 8.0, "A", "share", "kungal", "s", "/b", 2.0)
        self.assertEqual(self.db.count_done(), 1)
        self.assertEqual(self.db.get_done("v1")["cloud_path"], "/b")

    def test_c_quota(self) -> None:
        self.db.add_c_gb(1.5)
        self.db.add_c_gb(2.0)
        self.assertAlmostEqual(self.db.c_quota_used_gb(), 3.5, places=3)
        self.db.reset_c_quota()
        self.assertEqual(self.db.c_quota_used_gb(), 0.0)

    def test_retry_flow(self) -> None:
        first = self.db.add_retry("v9", "magnet", "ggbase", "boom", max_retry=3)
        self.assertEqual(first, 1)
        self.assertEqual(self.db.retry_count(), 1)
        self.assertEqual(self.db.due_retries(), [])  # 还没到时间
        self.assertEqual(len(self.db.due_retries(now=2**31)), 1)  # 时间到了就能取到
        second = self.db.add_retry("v9", "magnet", "ggbase", "boom", max_retry=3)
        self.assertEqual(second, 2)
        self.db.clear_retry("v9", "magnet")
        self.assertEqual(self.db.retry_count(), 0)

    def test_retry_gives_up_after_max(self) -> None:
        attempts = 0
        for _ in range(4):
            attempts = self.db.add_retry("v9", "magnet", "ggbase", "boom", max_retry=3)
        self.assertEqual(attempts, 4)
        self.assertEqual(self.db.retry_count(), 0)  # 超过上限即清除，不再重试

    def test_mark_done_clears_retry(self) -> None:
        self.db.add_retry("v9", "magnet", "ggbase", "boom", max_retry=3)
        self.db.mark_done("v9", 8.0, "A", "magnet", "ggbase", "m", "/a", 1.0)
        self.assertEqual(self.db.retry_count(), 0)

    def test_recompute_total(self) -> None:
        self.db.mark_done("v1", 8.0, "A", "magnet", "ggbase", "m", "/a", 3.25)
        self.db.meta_set("total_gb", "999")
        self.assertAlmostEqual(self.db.recompute_total(), 3.25, places=3)
        self.assertAlmostEqual(float(self.db.meta_get("total_gb")), 3.25, places=3)

    def test_stats(self) -> None:
        self.db.mark_done("v1", 8.0, "A", "magnet", "ggbase", "m", "/a", 3.0)
        stats = self.db.stats()
        self.assertEqual(stats["entries"], 1)
        self.assertAlmostEqual(stats["total_gb"], 3.0, places=3)
        self.assertIn("db", stats)


class TitleMatchTest(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize("Ｒａｎｃｅ　１０！"), "rance 10")
        self.assertEqual(normalize("  "), "")

    def test_similarity_bounds(self) -> None:
        self.assertEqual(similarity("ランス10", "ランス10"), 100.0)
        self.assertGreaterEqual(similarity("ランス10", "ランス10 -決戦-"), 88)
        self.assertLess(similarity("クラナド", "智代アフター"), 60)

    def test_similarity_strips_brackets(self) -> None:
        self.assertGreaterEqual(similarity("CLANNAD", "CLANNAD (日本語版)"), 88)

    def test_soundtrack_is_filtered_by_crawler_not_by_score(self) -> None:
        # 原声碟标题里包含作品名，分数会很高；拦它的是爬虫侧的噪声词过滤
        title = "アリスサウンドアルバム Vol.31 ランス10 -決戦- + ジャケット&レーベル"
        self.assertGreaterEqual(similarity("ランス10－決戦－", title), 85)
        from galsave.crawler import ggbase, shinnku

        self.assertTrue(ggbase._is_noise(title))
        self.assertTrue(shinnku._is_noise(title))
        # 正版游戏包带「同梱特典/アレンジ」不能被误杀
        game = "│2D.G.F.│ [180223] [ALICESOFT] ランス10 -決戦- パッケージ版 + 同梱特典 アレンジコレクション"
        self.assertFalse(ggbase._is_noise(game))
        self.assertFalse(shinnku._is_noise(game))
        self.assertGreaterEqual(similarity("ランス10－決戦－", game), 85)

    def test_tight_match_ignores_spaces(self) -> None:
        # 日文标题的空格位置不可靠：`ランス10 -決戦-` 必须能对上带前缀元数据的全名
        game = "│2D.G.F.│ [180223] [ALICESOFT] ランス10 -決戦- パッケージ版 + 同梱特典"
        self.assertGreaterEqual(similarity("ランス10－決戦－", game), 85)
        self.assertGreaterEqual(similarity("ランス10 -決戦-", "ランス10決戦"), 88)

    def test_best_match_and_is_match(self) -> None:
        score, name = best_match("ランス10", ["ランス01", "ランス10 -決戦-", "ランス9"])
        self.assertGreaterEqual(score, 88)
        self.assertEqual(name, "ランス10 -決戦-")
        self.assertTrue(is_match("ランス10", ["ランス10 -決戦-"]))
        self.assertFalse(is_match("ランス10", ["智代アフター"]))

    def test_search_terms(self) -> None:
        terms = search_terms(["ランス10 -決戦-"])
        self.assertIn("ランス10", terms)
        # 日文原名优先：镜像站只认日文标题
        self.assertTrue(terms[0].startswith("ランス10"))
        self.assertIn("かまいたちの夜", search_terms(["かまいたちの夜 ～真説～"]))
        self.assertEqual(search_terms([""]), [])
        # VNDB 的 alttitle 是罗马字时，不能排在前面浪费请求
        mixed = search_terms(["Rance Ⅹ -決戦-", "ランス10－決戦－"])
        self.assertTrue(mixed[0].startswith("ランス10"))
        self.assertIn("ランス10", mixed)
        # 纯罗马字一定排在所有含假名/汉字的关键词之后
        self.assertLess(mixed.index("ランス10"), mixed.index("Rance Ⅹ -決戦-"))


class SizeParseTest(unittest.TestCase):
    def test_units(self) -> None:
        self.assertAlmostEqual(parse_size_gb("10.5G"), 10.5)
        self.assertAlmostEqual(parse_size_gb("10.5 GB"), 10.5)
        self.assertAlmostEqual(parse_size_gb("872.11 MB"), 0.872, places=3)
        self.assertAlmostEqual(parse_size_gb("1.2 TB"), 1200.0)
        self.assertEqual(parse_size_gb(""), 0.0)
        self.assertEqual(parse_size_gb("没有体积"), 0.0)


class CryptoTest(unittest.TestCase):
    def test_roundtrip_multiblock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.bin"
            payload = b"GALGAME" * 300_000
            src.write_bytes(payload)
            enc = encrypt_file(src, "pw")
            self.assertTrue(is_encrypted(enc))
            self.assertEqual(enc.suffix, ".png")
            self.assertNotEqual(enc.read_bytes()[: len(payload)], payload)
            out = decrypt_file(enc, "pw")
            self.assertEqual(out.read_bytes(), payload)

    def test_parts_roundtrip(self) -> None:
        # 大文件切成多个容器：每份单独能解开，拼回来要一模一样
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "big.rar"
            payload = bytes(range(256)) * 4000  # 1MB
            src.write_bytes(payload)
            parts = encrypt_parts(src, "pw", part_bytes=300_000)
            self.assertEqual(len(parts), 4)
            self.assertTrue(all(p.suffix == ".png" for p in parts))
            joined = b""
            for part in parts:
                out = decrypt_file(part, "pw", dest=part.with_suffix(".dec"))
                joined += out.read_bytes()
            self.assertEqual(joined, payload)

    def test_small_file_stays_one_part(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "small.bin"
            src.write_bytes(b"x" * 1000)
            parts = encrypt_parts(src, "pw", part_bytes=1_000_000)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0].name, "small.png")

    def test_wrong_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.bin"
            src.write_bytes(b"x" * 5000)
            enc = encrypt_file(src, "right")
            with self.assertRaises(CryptoError):
                decrypt_file(enc, "wrong", dest=Path(tmp) / "bad.bin")
            self.assertFalse((Path(tmp) / "bad.bin").exists())

    def test_not_encrypted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "plain.png"
            src.write_bytes(b"\x89PNG not ours")
            self.assertFalse(is_encrypted(src))
            with self.assertRaises(CryptoError):
                decrypt_file(src, "pw")


if __name__ == "__main__":
    unittest.main()
