# -*- coding: utf-8 -*-
"""百度网盘封装的离线测试：链接/页面解析、Cookie 解析、错误文案、路径工具。"""

from __future__ import annotations

import unittest

from baidu_cloud.client import (
    BaiduClient,
    BaiduError,
    ERRNO_TEXT,
    join_posix,
    parse_cookie_text,
    posix_name,
    posix_parent,
)
from baidu_cloud.offline import STATUS_TEXT
from baidu_cloud.share import parse_share_page, safe_name, share_pwd, share_surl
from baidu_cloud.size import fmt_gb, parse_human_gb


class ShareParseTest(unittest.TestCase):
    def test_surl_forms(self) -> None:
        self.assertEqual(share_surl("https://pan.baidu.com/s/1Dau8DVO5_HcMjOD4nFA4Qg?pwd=t7cp"),
                         "1Dau8DVO5_HcMjOD4nFA4Qg")
        self.assertEqual(share_surl("https://pan.baidu.com/s/1abcd#list/path=%2F"), "1abcd")
        self.assertEqual(share_surl("https://yun.baidu.com/s/1xyz"), "1xyz")
        self.assertEqual(share_surl("https://pan.baidu.com/share/init?surl=1abc"), "1abc")
        self.assertEqual(share_surl(""), "")
        self.assertEqual(share_surl("https://example.com/foo"), "")

    def test_pwd_forms(self) -> None:
        self.assertEqual(share_pwd("https://pan.baidu.com/s/1abc?pwd=t7cp"), "t7cp")
        self.assertEqual(share_pwd("https://pan.baidu.com/s/1abc?password=ab12"), "ab12")
        self.assertEqual(share_pwd("https://pan.baidu.com/s/1abc"), "")

    def test_parse_share_page_yundata(self) -> None:
        html = (
            '<script>window.yunData = {};yunData.setData({"SHAREID":"123456",'
            '"SHARE_UK":"987654","TITLE":"ランス10",'
            '"FILEINFO":[{"fs_id":"11","server_filename":"rance10.zip",'
            '"isdir":0,"size":"1234"}]});</script>'
        )
        info = parse_share_page(html)
        self.assertEqual(info["shareid"], "123456")
        self.assertEqual(info["share_uk"], "987654")
        self.assertEqual(info["title"], "ランス10")
        self.assertEqual(len(info["files"]), 1)
        self.assertEqual(info["files"][0]["server_filename"], "rance10.zip")

    def test_parse_share_page_ssr_file_list(self) -> None:
        # 现在的分享页是服务端渲染的一坨 JSON，前面还有 base64 图片字符串干扰
        html = (
            '<script>var ctx = {"img":"data:image/png;base64,AAA\\"file_list\\":[1]","share_uk":"3749931402",'
            '"shareid":46566184821,"title":"千恋万花","file_list":['
            '{"fs_id":537214261051438,"server_filename":"game.rar","isdir":0,"size":7460000000},'
            '{"fs_id":537214261051439,"server_filename":"patch.zip","isdir":0,"size":1000}'
            '],"tail":1};</script>'
        )
        info = parse_share_page(html)
        self.assertEqual(info["shareid"], "46566184821")
        self.assertEqual(info["share_uk"], "3749931402")
        self.assertEqual(len(info["files"]), 2)
        self.assertEqual(info["files"][0]["server_filename"], "game.rar")
        self.assertEqual(int(info["files"][0]["size"]), 7460000000)

    def test_parse_share_page_loose(self) -> None:
        html = '<div data-shareid="555" data-share_uk="666">加载中</div>'
        info = parse_share_page(html)
        self.assertEqual(info["shareid"], "555")
        self.assertEqual(info["share_uk"], "666")
        self.assertEqual(info["files"], [])

    def test_parse_share_page_garbage(self) -> None:
        self.assertEqual(parse_share_page("")["files"], [])

    def test_safe_name(self) -> None:
        self.assertEqual(safe_name("ランス10 -決戦-"), "ランス10 -決戦-")
        self.assertNotIn("/", safe_name("a/b\\c:d*e?f"))
        self.assertLessEqual(len(safe_name("x" * 300)), 80)


class ClientHelperTest(unittest.TestCase):
    def test_parse_cookie_text(self) -> None:
        cookies = parse_cookie_text("BDUSS=abc123; STOKEN=def456; PANWEB=1")
        self.assertEqual(cookies["BDUSS"], "abc123")
        self.assertEqual(cookies["STOKEN"], "def456")
        # 真实浏览器复制出来常带 "BDUSS=..." 整行和换行
        cookies2 = parse_cookie_text("BDUSS=zzz;\n STOKEN=yyy;\n")
        self.assertEqual(cookies2["BDUSS"], "zzz")
        self.assertEqual(cookies2["STOKEN"], "yyy")

    def test_from_cookie_text_logged_in(self) -> None:
        client = BaiduClient.from_cookie_text("BDUSS=abc; STOKEN=def")
        self.assertTrue(client.logged_in)
        self.assertFalse(BaiduClient(cookies={}).logged_in)  # 显式空 cookie 不读本机凭据

    def test_require_login_raises(self) -> None:
        with self.assertRaises(BaiduError):
            BaiduClient(cookies={}).require_login()

    def test_posix_helpers(self) -> None:
        self.assertEqual(posix_parent("/gal_save/_offline/x"), "/gal_save/_offline")
        self.assertEqual(posix_parent("/gal_save"), "/")
        self.assertEqual(posix_name("/gal_save/a.zip"), "a.zip")
        # Windows 本机路径不能整串当文件名（百度会回 errno -7）
        self.assertEqual(posix_name(r"C:\gal_save\vndb-searcher\galsave\data\x.png"), "x.png")
        self.assertEqual(join_posix("/gal_save", "_offline", "a"), "/gal_save/_offline/a")
        self.assertEqual(join_posix("/", "a"), "/a")

    def test_errno_text(self) -> None:
        self.assertIn("SVIP", ERRNO_TEXT[31064])
        self.assertIn("登录", ERRNO_TEXT[-6])
        error = BaiduError(31064, context="离线下载")
        self.assertIn("SVIP", str(error))
        self.assertIn("离线下载", str(error))
        self.assertEqual(error.errno, 31064)

    def test_offline_status_text(self) -> None:
        self.assertEqual(STATUS_TEXT[0], "等待中")
        self.assertEqual(STATUS_TEXT[2], "已完成")
        self.assertEqual(STATUS_TEXT[3], "失败")

    def test_size_format(self) -> None:
        self.assertAlmostEqual(parse_human_gb("11.11 GB"), 11.11, places=2)
        self.assertAlmostEqual(parse_human_gb("872 MB"), 0.872, places=3)
        self.assertIn("GB", fmt_gb(12.345))

    def test_error_keeps_server_message(self) -> None:
        # 百度有时候用 HTTP 400 携带真正的 errno，原始信息不能丢
        error = BaiduError(-6, "用户未登录", "查询容量")
        self.assertIn("用户未登录", str(error))
        self.assertIn("登录", str(error))
        self.assertEqual(error.message, "用户未登录")

    def test_check_login_without_cookies_is_offline_safe(self) -> None:
        # 没有 BDUSS 时直接返回 False，不该发任何网络请求
        self.assertFalse(BaiduClient(cookies={}).check_login())

    def test_from_cookie_text_does_not_touch_local_credentials(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from baidu_cloud import auth

        with tempfile.TemporaryDirectory() as tmp:
            cred = Path(tmp) / "credentials.json"
            with mock.patch.object(auth, "CREDENTIAL_FILE", cred):
                client = BaiduClient.from_cookie_text("BDUSS=abc; STOKEN=def")
                self.assertTrue(client.logged_in)
                self.assertFalse(cred.exists())  # 默认不落盘，别把真实登录态覆盖掉

                BaiduClient.from_cookie_text("BDUSS=abc; STOKEN=def", save=True)
                self.assertTrue(cred.exists())


if __name__ == "__main__":
    unittest.main()
