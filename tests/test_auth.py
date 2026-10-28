# -*- coding: utf-8 -*-
"""baidu_cloud.auth 的离线测试：只测不弹浏览器、不发网络请求的部分。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baidu_cloud import auth


class AuthCredentialTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cred = Path(self.tmpdir.name) / "credentials.json"
        self.patcher = mock.patch.object(auth, "CREDENTIAL_FILE", self.cred)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_save_and_load(self) -> None:
        self.assertIsNone(auth.load_credentials())
        auth._save([
            {"name": "BDUSS", "value": "abc", "domain": ".baidu.com"},
            {"name": "STOKEN", "value": "def", "domain": ".baidu.com"},
        ])
        data = auth.load_credentials()
        self.assertIsNotNone(data)
        self.assertIn("saved_at", data)
        self.assertEqual(auth.cookies_dict(), {"BDUSS": "abc", "STOKEN": "def"})
        self.assertEqual(auth.bduss(), "abc")
        self.assertTrue(auth.is_logged_in())

    def test_clear(self) -> None:
        auth._save([{"name": "BDUSS", "value": "abc", "domain": ".baidu.com"}])
        self.assertTrue(auth.clear_credentials())
        self.assertFalse(auth.clear_credentials())  # 第二次已经没有文件了
        self.assertFalse(auth.is_logged_in())

    def test_broken_file_is_ignored(self) -> None:
        self.cred.write_text("{not json", encoding="utf-8")
        self.assertIsNone(auth.load_credentials())
        self.assertEqual(auth.cookies_dict(), {})

    def test_cookie_text_parsing(self) -> None:
        cookies = auth._parse_cookie_text("BDUSS=abc; STOKEN=def; junk=; noequals")
        names = [c["name"] for c in cookies]
        self.assertEqual(names, ["BDUSS", "STOKEN"])  # 空值和无等号的碎片都丢掉
        self.assertTrue(all(c["domain"] == ".baidu.com" for c in cookies))


class AuthBrowserChoiceTest(unittest.TestCase):
    def test_system_browser_first(self) -> None:
        # 自带内核要下载，国内经常下不动，所以先试系统 Edge / Chrome
        self.assertEqual(auth.LAUNCH_CHANNELS[0], "msedge")
        self.assertIsNone(auth.LAUNCH_CHANNELS[-1])
        self.assertIn("chrome", auth.LAUNCH_CHANNELS)


if __name__ == "__main__":
    unittest.main()
