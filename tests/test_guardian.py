"""心跳与 DeepSeek 动作解析（不联网）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from galsave.config import load_config
from galsave.deepseek_ops import SAFE_ACTIONS, _parse_action
from galsave.heartbeat import age_seconds, is_stale, pulse, read_heartbeat


class HeartbeatCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cfg = {
            "paths": {"heartbeat": str(root / "hb.json"), "run_lock": str(root / "run.lock")},
            "guardian": {"stale_seconds": 120},
        }

    def test_pulse(self) -> None:
        pulse(self.cfg, phase="handling", vndb_id="v1")
        hb = read_heartbeat(self.cfg)
        self.assertIsNotNone(hb)
        assert hb is not None
        self.assertEqual(hb["phase"], "handling")
        self.assertEqual(hb["vndb_id"], "v1")
        self.assertFalse(is_stale(self.cfg))
        self.assertLess(age_seconds(self.cfg) or 99, 5)


class ParseActionCase(unittest.TestCase):
    def test_plain_json(self) -> None:
        out = _parse_action('{"action":"wait","reason":"断网","wait_seconds":90}')
        self.assertEqual(out["action"], "wait")
        self.assertEqual(out["wait_seconds"], 90)

    def test_fenced(self) -> None:
        out = _parse_action('```json\n{"action":"resume_run","reason":"续跑","run_args":"--buckets 8.0"}\n```')
        self.assertEqual(out["action"], "resume_run")
        self.assertIn("8.0", out["run_args"])

    def test_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            _parse_action('{"action":"rm_rf","reason":"x"}')

    def test_safe_set(self) -> None:
        self.assertIn("resume_run", SAFE_ACTIONS)
        self.assertIn("stop", SAFE_ACTIONS)


class LocalConfigMergeCase(unittest.TestCase):
    def test_defaults_have_guardian(self) -> None:
        cfg = load_config()
        self.assertIn("guardian", cfg)
        self.assertIn("deepseek", cfg)


if __name__ == "__main__":
    unittest.main()
