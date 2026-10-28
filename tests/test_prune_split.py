# -*- coding: utf-8 -*-
"""分卷压缩包（.7z.001 / .part1.rar / .r00）的识别。

实测：Rance Ⅹ 汉化版分享的根目录只有一个 `10` 文件夹，里面是
`Rance10_CN.7z.001` ~ `.004` 四卷共 3.85GB。早期按 `Path.suffix` 只看到
`.001`，判定「没认出任何游戏文件」把整目录删了，属于误杀。
"""

from __future__ import annotations

import unittest

from galsave.prune import (
    Entry,
    check_game_like,
    is_archive_segment,
    make_entries,
    plan_prune,
)


def _f(rel: str, gb: float) -> Entry:
    return Entry(rel=rel, name=rel.rsplit("/", 1)[-1], isdir=False, size=int(gb * 1e9))


class IsArchiveSegmentTest(unittest.TestCase):
    def test_split_volumes(self) -> None:
        for name in (
            "Rance10_CN.7z.001",
            "Rance10_CN.7z.004",
            "game.zip.002",
            "game.part1.rar",
            "game.part12.rar",
            "game.r00",
            "game.r99",
            "game.z01",
            "disk.001",
            "disk.002",
        ):
            self.assertTrue(is_archive_segment(name), name)

    def test_ordinary_names_are_not_segments(self) -> None:
        for name in (
            "Rance10_CN.7z",
            "game.rar",
            "movie.mp4",
            "Rance Ⅹ -決戦-",
            "2026",
            "第19讲 课后作业",
            "game.exe",
            "hdtv.1080",
        ):
            self.assertFalse(is_archive_segment(name), name)


class SplitArchiveIsGameTest(unittest.TestCase):
    def test_four_volumes_count_as_game(self) -> None:
        rep = check_game_like([
            _f("10/Rance10_CN.7z.001", 1.05),
            _f("10/Rance10_CN.7z.002", 1.05),
            _f("10/Rance10_CN.7z.003", 1.05),
            _f("10/Rance10_CN.7z.004", 0.71),
        ])
        self.assertTrue(rep.ok, rep.reason)
        self.assertAlmostEqual(rep.game_gb, 3.86, places=2)
        self.assertNotIn("未知格式", rep.reason)

    def test_split_volumes_with_videos_still_game(self) -> None:
        rep = check_game_like([
            _f("game.7z.001", 3.0),
            _f("game.7z.002", 3.0),
            _f("opening.mp4", 0.4),
        ])
        self.assertTrue(rep.ok, rep.reason)

    def test_plan_prune_keeps_split_archive_under_threshold(self) -> None:
        entries = [
            Entry(rel="10", name="10", isdir=True, size=0),
            _f("10/Rance10_CN.7z.001", 1.05),
            _f("10/Rance10_CN.7z.004", 0.71),
        ]
        rep = plan_prune(entries, threshold_gb=9.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertEqual(rep.drop, [])

    def test_make_entries_accepts_entries(self) -> None:
        entry = _f("a/b.7z.001", 1.0)
        self.assertEqual(make_entries([entry]), [entry])


if __name__ == "__main__":
    unittest.main()
