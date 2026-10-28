# -*- coding: utf-8 -*-
"""三站爬虫的解析逻辑测试（全部用离线 HTML/JSON，不联网）。"""

from __future__ import annotations

import unittest

from galsave.config import load_config
from galsave.crawler import SearchEngine, build_crawlers
from galsave.crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE, Resource
from galsave.crawler.ggbase import GgbaseCrawler, parse_rows
from galsave.crawler.kungal import KungalCrawler, _collect_links
from galsave.crawler.shinnku import ShinnkuCrawler, parse_cards, pick_direct_links

GG_HTML = """
<table>
  <tr class='dtr' >
    <td><a class="bo" href="//ggb.dlgal.com/view.so?id=12345">│2D.G.F.│ &#12521;&#12531;&#12473;10 <b style='color:red'>-決戦-</b></a></td>
    <td><span class='labelFK bc2 newf'>1.20G</span></td>
    <td class='d' v="1519384920000"><span class='labelFK bc5 newf'>2018-02-23</span></td>
  </tr>
  <tr class='dtr' id="row2">
    <td><a href="https://www.ggbases.com/view.so?id=999">Rance 9 &amp; More</a></td>
    <td><span class='labelaG newf bc2'>3.2 GB</span></td>
  </tr>
  <tr class='other'><td><a href="/view.so?id=1">噪声</a></td></tr>
</table>
"""

SHINNKU_HTML = """
<div class="p-4 border">
  <a class="text-lg text-blue-600 hover:underline" href="/files/shinnku/zd/1234/rance10.zip">[131231] ランス10 -決戦-.zip</a>
  <span class="text-sm">熟肉 872.11 MB</span>
</div>
<div class="p-4 border">
  <a class="text-lg text-blue-600 hover:underline" href="/files/shinnku/zd/5678/other.zip">[140101] 智代アフター.zip</a>
  <span class="text-sm">生肉 1.2 GB</span>
</div>
"""


class GgbaseParseTest(unittest.TestCase):
    def test_parse_rows(self) -> None:
        rows = parse_rows(GG_HTML)
        self.assertEqual(len(rows), 2)  # 只认 class='dtr' 的行
        title, host, vid, size = rows[0]
        self.assertEqual(title, "│2D.G.F.│ ランス10 -決戦-")  # 实体反转义 + 去高亮标签
        self.assertEqual(host, "ggb.dlgal.com")
        self.assertEqual(vid, "12345")
        self.assertEqual(size, "1.20G")
        self.assertEqual(rows[1][0], "Rance 9 & More")
        self.assertEqual(rows[1][1], "www.ggbases.com")
        self.assertEqual(rows[1][3], "3.2 GB")

    def test_noise_filter(self) -> None:
        from galsave.crawler.ggbase import _is_noise

        self.assertTrue(_is_noise("アリスサウンドアルバム Vol.31 ランス10 -決戦-"))
        self.assertTrue(_is_noise("Rance 10 OST"))
        self.assertFalse(_is_noise("ランス10 -決戦- パッケージ版"))

    def test_parse_empty(self) -> None:
        self.assertEqual(parse_rows("<html><body>没有结果</body></html>"), [])

    def test_find_downid(self) -> None:
        page = '<script>var setData = {"downid":"a1b2-c3d4"};</script>'
        self.assertEqual(GgbaseCrawler._find_downid(page), "a1b2-c3d4")
        # 真实 magnet.so 页面是内联 JS 里的 ajax url
        real = '$.ajax({ url: "/magnet.so?downid=3d81789f-ade3-4a0c-9ec5-e92653c43880&json=1&id=86784"'
        self.assertEqual(
            GgbaseCrawler._find_downid(real), "3d81789f-ade3-4a0c-9ec5-e92653c43880"
        )
        # 找不到时返回空串（绝不能返回随机 uuid，那会拼出无效请求）
        self.assertEqual(GgbaseCrawler._find_downid("<html></html>"), "")

    def test_crawler_config(self) -> None:
        crawler = GgbaseCrawler(load_config())
        self.assertEqual(crawler.site, "ggbase")
        self.assertEqual(crawler.source_type, SOURCE_MAGNET)
        self.assertTrue(crawler.base.startswith("https://"))


class ShinnkuParseTest(unittest.TestCase):
    def test_parse_cards(self) -> None:
        cards = parse_cards(SHINNKU_HTML)
        self.assertEqual(len(cards), 2)
        title, path, size = cards[0]
        self.assertIn("ランス10", title)
        self.assertEqual(path, "/files/shinnku/zd/1234/rance10.zip")
        self.assertEqual(size, "872.11 MB")
        self.assertEqual(cards[1][2], "1.2 GB")

    def test_parse_fallback_without_class(self) -> None:
        html = '<a href="/files/shinnku/zd/x.zip">某作 &amp; 版本</a> 3G'
        cards = parse_cards(html)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0][0], "某作 & 版本")

    def test_crawler_config(self) -> None:
        crawler = ShinnkuCrawler(load_config())
        self.assertEqual(crawler.source_type, SOURCE_DIRECT)

    def test_pick_direct_links_zd_cdn(self) -> None:
        detail = "https://www.shinnku.com/files/shinnku/zd/0001-0500/%5B080411%5D%20a.rar"
        html = (
            '<a href="/files/shinnku/zd">导航</a>'
            '<a href="https://www.shinnku.com/files/shinnku/zd/0001-0500/%5B080411%5D%20a.rar">自身</a>'
            '<a href="https://zd.shinnku.top/file/shinnku/zd/0001-0500/%5B080411%5D%20a.rar">下载</a>'
        )
        links = pick_direct_links(html, detail)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("https://zd.shinnku.top/file/"))

    def test_pick_direct_links_collection_host(self) -> None:
        # 合集区（galgame0）的直链主机是 galgamedownload.date，不是 zd.shinnku.top
        detail = "https://www.shinnku.com/files/galgame0/3/2006/会社合集/%5B061215%5D%20b.rar"
        html = (
            '<a href="https://galgamedownload.date/%60%E5%90%88%E9%9B%86/%5B061215%5D%20b.rar">'
            "点击此处下载</a>"
        )
        links = pick_direct_links(html, detail)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("https://galgamedownload.date/"))

    def test_pick_direct_links_empty(self) -> None:
        self.assertEqual(pick_direct_links("<html></html>", "https://www.shinnku.com/files/x"), [])


class KungalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.crawler = KungalCrawler(load_config())
        self.work = {
            "id": 42,
            "display_name": "ランス10",
            "localized": {
                "ja": {"value": "ランス10"},
                "en": {"value": "Rance 10"},
                "zh-Hans": {"value": "兰斯10"},
            },
        }

    def test_score_work(self) -> None:
        self.assertGreaterEqual(self.crawler._score_work(self.work, ["ランス10"]), 99)
        self.assertGreaterEqual(self.crawler._score_work(self.work, ["Rance 10"]), 99)
        self.assertLess(self.crawler._score_work(self.work, ["智代アフター"]), 60)

    def test_collect_links_nested(self) -> None:
        content = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "资源"},
                ]},
                {"type": "link", "attrs": {
                    "href": "https://pan.baidu.com/s/1Dau8DVO5_HcMjOD4nFA4Qg?pwd=t7cp",
                    "text": "百度网盘",
                }},
                {"type": "paragraph", "content": [{
                    "type": "text",
                    "text": "备用 https://pan.baidu.com/s/1abcdef 。",
                }]},
            ],
        }
        links = _collect_links(content)
        self.assertEqual(len(links), 2)
        self.assertIn("pwd=t7cp", links[0])
        self.assertEqual(links[1], "https://pan.baidu.com/s/1abcdef")  # 去掉尾部标点

    def test_collect_links_skips_tool_links(self) -> None:
        # 鲲的正文里「模拟器及解压工具：<链接>」那个链接不是游戏本体
        content = {
            "object": "document",
            "children": [
                {"object": "blockquote", "children": [
                    {"object": "paragraph", "children": [
                        {"object": "text", "value": "  模拟器及解压工具："},
                        {"object": "link", "url": "https://pan.baidu.com/s/1Dau8DVO5_HcMjOD4nFA4Qg?pwd=t7cp",
                         "children": [{"object": "text", "value": "https://pan.baidu.com/s/1Dau8DVO5_HcMjOD4nFA4Qg?pwd=t7cp"}]},
                    ]},
                ]},
                {"object": "paragraph", "children": [
                    {"object": "text", "value": "游戏本体 "},
                    {"object": "link", "url": "https://pan.baidu.com/s/1gameFILE?pwd=abcd",
                     "children": [{"object": "text", "value": "https://pan.baidu.com/s/1gameFILE?pwd=abcd"}]},
                ]},
            ],
        }
        links = _collect_links(content)
        self.assertEqual(links, ["https://pan.baidu.com/s/1gameFILE?pwd=abcd"])

    def test_resources_filtering(self) -> None:
        items = [
            {"id": 1, "resource_type": "game", "title": "ランス10 汉化版", "size": "11.11 GB",
             "provider_names": ["百度网盘"],
             "content": {"type": "link", "url": "https://pan.baidu.com/s/1good?pwd=ab12"}},
            {"id": 2, "resource_type": "collection", "title": "F社合集 161.6GB", "size": "161.6 GB",
             "provider_names": ["百度网盘"],
             "content": {"type": "link", "url": "https://pan.baidu.com/s/1coll"}},
            {"id": 3, "resource_type": "game", "title": "模拟器及解压工具", "size": "1.0 GB",
             "provider_names": ["百度网盘"],
             "content": {"type": "link", "url": "https://pan.baidu.com/s/1tool"}},
            {"id": 4, "resource_type": "game", "title": "另一份", "size": "2.0 GB",
             "provider_names": ["夸克网盘"],
             "content": {"type": "link", "url": "https://pan.baidu.com/s/1quark"}},
            {"id": 5, "resource_type": "game", "title": "没链接", "size": "3.0 GB",
             "provider_names": ["百度网盘"], "content": {"type": "doc"}},
            {"id": 6, "resource_type": "game", "title": "已失效", "size": "4.0 GB", "state": "expired",
             "provider_names": ["百度网盘"],
             "content": {"type": "link", "url": "https://pan.baidu.com/s/1gone"}},
        ]
        found = self.crawler._resources_from_items(items, self.work, 95.0, ["ランス10"])
        self.assertEqual(len(found), 1)
        res = found[0]
        self.assertEqual(res.source_type, SOURCE_SHARE)
        self.assertIn("pwd=ab12", res.link)
        self.assertEqual(res.extra["pwd"], "ab12")
        self.assertAlmostEqual(res.size_gb, 11.11, places=2)
        self.assertEqual(res.site, "kungal")

    def test_collection_allowed_when_configured(self) -> None:
        cfg = load_config()
        cfg["crawler"]["allow_collection"] = True
        crawler = KungalCrawler(cfg)
        items = [{"id": 2, "resource_type": "collection", "title": "F社合集", "size": "161.6 GB",
                  "provider_names": ["百度网盘"],
                  "content": {"type": "link", "url": "https://pan.baidu.com/s/1coll"}}]
        self.assertEqual(len(crawler._resources_from_items(items, self.work, 90.0, ["ランス10"])), 1)


class FakeCrawler:
    site = "fake"
    source_type = SOURCE_MAGNET

    def __init__(self, resources: list[Resource]):
        self._resources = resources

    def search(self, names, threshold=None):
        return list(self._resources)


class SearchEngineTest(unittest.TestCase):
    def test_priority_sorting(self) -> None:
        engine = SearchEngine(load_config())
        engine.crawlers = [
            FakeCrawler([Resource("fake", SOURCE_DIRECT, "c", "https://x/1", 1.0, match_score=99)]),
            FakeCrawler([Resource("fake", SOURCE_MAGNET, "a", "magnet:?xt=1", 2.0, match_score=90)]),
            FakeCrawler([Resource("fake", SOURCE_MAGNET, "b", "magnet:?xt=2", 5.0, match_score=95)]),
        ]
        found = engine.find(["x"])
        self.assertEqual([r.title for r in found], ["b", "a", "c"])

    def test_allow_types_filters(self) -> None:
        engine = SearchEngine(load_config())
        engine.crawlers = [FakeCrawler([Resource("fake", SOURCE_MAGNET, "a", "magnet:?xt=1", 2.0)])]
        self.assertEqual(engine.find(["x"], allow_types=[SOURCE_SHARE]), [])

    def test_build_crawlers(self) -> None:
        crawlers = build_crawlers(load_config())
        self.assertEqual([c.site for c in crawlers], ["ggbase", "kungal", "shinnku"])
        self.assertEqual(SearchEngine(load_config()).sites(), ["ggbase", "kungal", "shinnku"])


if __name__ == "__main__":
    unittest.main()
