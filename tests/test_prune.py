# -*- coding: utf-8 -*-
"""入库体检（诱饵分享识别 + 多平台只留 PC）的单元测试。"""

from __future__ import annotations

import unittest

from galsave.prune import (
    Entry,
    apply_report,
    check_game_like,
    classify_name,
    looks_like_bait,
    make_entries,
    plan_prune,
    walk_cloud,
)


def _f(rel: str, gb: float) -> Entry:
    return Entry(rel=rel, name=rel.rsplit("/", 1)[-1], isdir=False, size=int(gb * 1e9))


def _d(rel: str) -> Entry:
    return Entry(rel=rel, name=rel.rsplit("/", 1)[-1], isdir=True, size=0)


class ClassifyTest(unittest.TestCase):
    def test_platform_names(self) -> None:
        cases = {
            "PC版": "pc",
            "windows汉化硬盘版": "pc",
            "setup_1.0.exe": "pc",
            "安卓直装": "android",
            "Android_arm64": "android",
            "KRKR模拟器": "android",
            "Tyranor版": "android",
            "Winlator": "android",
            "iPhone版": "ios",
            "backup.ipa": "ios",
            "千恋＊万花": "other",
            "【PC+安卓】千恋万花": "mixed",
            "PC+iOS": "mixed",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_name(name), want)

    def test_case_insensitive(self) -> None:
        self.assertEqual(classify_name("ANDROID"), "android")
        self.assertEqual(classify_name("Pc版"), "pc")


class BaitTest(unittest.TestCase):
    def test_course_names_are_bait(self) -> None:
        for name in (
            "2026真实U3D脚本拆解实践班直播第19讲",
            "2026高等数学核心考点精讲第CABF讲",
            "2026中学物理实践班直播第185讲",
            "考研数学冲刺资料",
        ):
            with self.subTest(name=name):
                self.assertTrue(looks_like_bait(name))

    def test_game_names_are_not_bait(self) -> None:
        for name in ("千恋＊万花", "001千恋万花丛中笑", "（珠帘）千恋万花", "Se ren Ba ka", "02"):
            with self.subTest(name=name):
                self.assertFalse(looks_like_bait(name))


class GameLikeTest(unittest.TestCase):
    def test_archive_is_game(self) -> None:
        rep = check_game_like([_f("game.rar", 3.0)])
        self.assertTrue(rep.ok)
        self.assertAlmostEqual(rep.total_gb, 3.0)

    def test_videos_are_junk(self) -> None:
        rep = check_game_like([
            _f("第19讲/1【安卓】.mp4", 1.8),
            _f("第19讲/2【PC】.mp4", 9.0),
            _f("资料.pdf", 0.02),
        ])
        self.assertFalse(rep.ok)
        self.assertIn("视频/文档", rep.reason)

    def test_empty_share(self) -> None:
        rep = check_game_like([])
        self.assertFalse(rep.ok)
        self.assertIn("空", rep.reason)

    def test_mixed_bundle_with_iso_wins(self) -> None:
        rep = check_game_like([_f("game.iso", 4.0), _f("opening.mp4", 0.5)])
        self.assertTrue(rep.ok)


class PlanPruneTest(unittest.TestCase):
    def test_multiplatform_keeps_pc(self) -> None:
        entries = [
            _d("PC版"),
            _f("PC版/game.exe", 5.0),
            _f("PC版/data.xp3", 3.0),
            _d("安卓直装"),
            _f("安卓直装/game.apk", 2.0),
            _d("iOS"),
            _f("iOS/game.ipa", 2.5),
        ]
        rep = plan_prune(entries, threshold_gb=9.0)
        self.assertTrue(rep.ok)
        rels = sorted(e.rel for e in rep.drop)
        self.assertEqual(rels, ["iOS", "安卓直装"])
        self.assertAlmostEqual(rep.drop_gb, 4.5)
        self.assertIn("PC版", rep.keep)

    def test_below_threshold_untouched(self) -> None:
        entries = [_f("PC版/game.exe", 2.0), _f("安卓直装/game.apk", 1.0)]
        rep = plan_prune(entries, threshold_gb=9.0)
        self.assertEqual(rep.drop, [])
        self.assertIn("没超过", rep.reason)

    def test_mixed_parent_descends_one_level(self) -> None:
        entries = [
            _d("【PC+安卓】千恋万花"),
            _d("【PC+安卓】千恋万花/PC"),
            _f("【PC+安卓】千恋万花/PC/game.iso", 6.0),
            _d("【PC+安卓】千恋万花/安卓"),
            _f("【PC+安卓】千恋万花/安卓/game.apk", 4.0),
        ]
        rep = plan_prune(entries, threshold_gb=9.0)
        self.assertEqual([e.rel for e in rep.drop], ["【PC+安卓】千恋万花/安卓"])
        self.assertAlmostEqual(rep.drop_gb, 4.0)

    def test_junk_is_not_pruned(self) -> None:
        rep = plan_prune([_f("a.mp4", 9.5), _f("b.mp4", 5.0)], threshold_gb=9.0)
        self.assertFalse(rep.ok)
        self.assertEqual(rep.drop, [])

    def test_android_only_share_is_not_a_pc_game(self) -> None:
        entries = [_d("安卓直装"), _f("安卓直装/game.apk", 12.0)]
        rep = plan_prune(entries, threshold_gb=9.0)
        self.assertFalse(rep.ok)
        self.assertEqual(rep.drop, [])

    def test_pc_inside_android_name_is_kept(self) -> None:
        entries = [
            _d("安卓+PC合集"),
            _d("安卓+PC合集/PC"),
            _f("安卓+PC合集/PC/game.exe", 10.0),
            _d("安卓+PC合集/安卓"),
            _f("安卓+PC合集/安卓/game.apk", 2.0),
        ]
        rep = plan_prune(entries, threshold_gb=9.0)
        self.assertEqual([e.rel for e in rep.drop], ["安卓+PC合集/安卓"])


class FakeClient:
    def __init__(self, listing: dict[str, list[dict]]) -> None:
        self.listing = listing
        self.deleted: list[list[str]] = []

    def list_dir(self, path: str) -> list[dict]:
        return self.listing.get(path, [])

    def delete(self, paths: list[str]) -> bool:
        self.deleted.append(list(paths))
        return True


class WalkCloudTest(unittest.TestCase):
    def test_walk_builds_relative_paths(self) -> None:
        client = FakeClient({
            "/root": [
                {"server_filename": "PC版", "isdir": 1, "size": 0},
                {"server_filename": "readme.txt", "isdir": 0, "size": 10},
            ],
            "/root/PC版": [
                {"server_filename": "game.exe", "isdir": 0, "size": 1000},
            ],
        })
        entries = walk_cloud(client, "/root")
        rels = sorted(e.rel for e in entries)
        self.assertEqual(rels, ["PC版", "PC版/game.exe", "readme.txt"])

    def test_apply_report_deletes_relative_paths(self) -> None:
        client = FakeClient({})
        rep = plan_prune(
            [
                _d("PC"), _f("PC/game.exe", 5.0),
                _d("安卓"), _f("安卓/game.apk", 5.0),
            ],
            threshold_gb=9.0,
        )
        count = apply_report(client, "/root/v1 x", rep)
        self.assertEqual(count, 1)
        self.assertEqual(client.deleted, [["/root/v1 x/安卓"]])

    def test_make_entries_skips_nameless(self) -> None:
        entries = make_entries([{"server_filename": "", "isdir": 0}, {"server_filename": "a.bin"}])
        self.assertEqual([e.name for e in entries], ["a.bin"])


if __name__ == "__main__":
    unittest.main()
