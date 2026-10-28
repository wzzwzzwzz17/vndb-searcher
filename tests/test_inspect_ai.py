"""AI 目录质检：最底层摘要与 drop 合并（不联网）。"""

from __future__ import annotations

import unittest

from galsave.inspect_ai import AiInspect, deepest_summary, merge_ai_drops, _parse
from galsave.prune import Entry


def _e(rel: str, size: int = 0, isdir: bool = False) -> Entry:
    name = rel.rsplit("/", 1)[-1]
    return Entry(rel=rel, name=name, isdir=isdir, size=size)


class InspectAiCase(unittest.TestCase):
    def test_deepest_summary_prefers_deep_files(self) -> None:
        entries = [
            _e("课程伪装", isdir=True),
            _e("课程伪装/PC", isdir=True),
            _e("课程伪装/PC/game.iso", size=4_000_000_000),
            _e("安卓/a.apk", size=50_000_000),
        ]
        text = deepest_summary(entries)
        self.assertIn("game.iso", text)
        # iso 行应出现在靠前（更深）
        self.assertLess(text.find("game.iso"), text.find("a.apk") if "a.apk" in text else 10**9)

    def test_parse_and_merge_drops(self) -> None:
        ai = _parse('{"is_game":true,"drop":["安卓","KRKR"],"reason":"最底层有iso"}')
        self.assertTrue(ai.is_game)
        entries = [
            _e("PC", isdir=True),
            _e("PC/a.rar", size=1),
            _e("安卓", isdir=True),
            _e("安卓/x.apk", size=1),
            _e("KRKR", isdir=True),
        ]
        drops = merge_ai_drops(entries, ai)
        names = {d.name for d in drops}
        self.assertEqual(names, {"安卓", "KRKR"})

    def test_ai_says_not_game(self) -> None:
        ai = _parse('{"is_game":false,"drop":[],"reason":"全是mp4课件"}')
        self.assertFalse(ai.is_game)


if __name__ == "__main__":
    unittest.main()
