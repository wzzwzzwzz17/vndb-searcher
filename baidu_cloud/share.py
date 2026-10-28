# -*- coding: utf-8 -*-
"""分享链接转存（B 路线）：解析分享页 → 转存到自己的网盘目录。

流程（2026 实测，Web 端同款）：
1. GET https://pan.baidu.com/s/<surl>?pwd=xxxx —— 有提取码时只会重定向到
   /share/init?surl=..&pwd=.. 的表单页（十几 KB，没有文件列表）；
2. POST /share/verify?surl=<surl>&t=..&channel=chunlei&web=1，body 带 pwd，
   errno=0 时服务端下发 BDCLND Cookie（「已通过提取码」的凭据），并返回 randsk；
3. 再 GET /share/init?surl=..&pwd=.. —— 这时才是 70 KB 左右的服务端渲染页，
   里面有 `"file_list":[{...}]`、`"shareid":46566184821`、`"share_uk":"3749931402"`；
4. POST /share/transfer?shareid=..&from=<share_uk>&bdstoken=..&sekey=<BDCLND>，
   body: fsidlist=[...]&path=/目标目录。

注意：`surl` 有两种写法。URL 里的 `/s/1PmxqKtZ...`，其中前导的 `1` 是路径前缀，
`/share/init` 和 `/share/verify` 要的是不带它的 `PmxqKtZ...`。这里统一用重定向后
`/share/init?surl=` 里的值当权威值，避免自己猜。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .client import PAN, BaiduError, BaiduClient, join_posix

_YDATA = re.compile(r"yunData\.setData\(\s*(\{.*?\})\s*\)\s*;", re.S)
_SURL = re.compile(r"(?:pan|yun)\.baidu\.com/s/([\w\-]+)")
_FILE_LIST = re.compile(r'"file_list"\s*:\s*\[')
_SHAREID = re.compile(r"""shareid['"]?\s*[:=]\s*['"]?(\d+)""")
_SHARE_UK = re.compile(r"""share_uk['"]?\s*[:=]\s*['"]?(\d+)""")
_TITLE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.){1,200})"')


def share_surl(link: str) -> str:
    """从各种形态的分享链接里取出 surl（/s/ 后面那串）。"""
    raw = str(link or "")
    match = _SURL.search(raw)
    if match:
        return match.group(1)
    # 老式链接：/share/init?surl=xxxx
    query = parse_qs(urlparse(raw).query)
    for key in ("surl", "shareid"):
        if query.get(key):
            return str(query[key][0]).strip()
    parsed = urlparse(raw)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "s":
        return parts[1]
    return ""


def share_pwd(link: str) -> str:
    query = parse_qs(urlparse(str(link or "")).query)
    for key in ("pwd", "password", "passwd"):
        if query.get(key):
            return str(query[key][0]).strip()
    return ""


def parse_share_page(html: str) -> dict[str, Any]:
    """从分享页 HTML 里抽出 shareid / share_uk / 文件列表。

    页面结构变过好几次，所以这里尽量宽容：
    1. 老页面是 `yunData.setData({...})`，直接整体解析；
    2. 现在的页面是服务端渲染的一坨 JSON，只取其中 `"file_list":[ ... ]`
       （用 JSONDecoder.raw_decode 按括号平衡截取，避免被前面 base64 图片里的
       字符串干扰）；
    3. shareid / share_uk / title 用最小正则兜底。
    """
    info: dict[str, Any] = {}
    text = html or ""

    match = _YDATA.search(text)
    if match:
        raw = match.group(1)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _loose_json(raw)
        if isinstance(data, dict):
            info["shareid"] = data.get("SHAREID") or data.get("shareid")
            info["share_uk"] = data.get("SHARE_UK") or data.get("share_uk") or data.get("uk")
            file_list = data.get("FILEINFO") or data.get("file_list") or []
            if isinstance(file_list, dict):
                file_list = list(file_list.values())
            info["files"] = [f for f in file_list if isinstance(f, dict)]
            info["title"] = data.get("TITLE") or data.get("title") or ""

    if not info.get("files"):
        info["files"] = _file_list_from_html(text)

    if not info.get("shareid"):
        found = _SHAREID.search(text)
        if found:
            info["shareid"] = found.group(1)
    if not info.get("share_uk"):
        found = _SHARE_UK.search(text)
        if found:
            info["share_uk"] = found.group(1)
    if not info.get("title"):
        found = _TITLE.search(text)
        if found:
            try:
                info["title"] = json.loads(f'"{found.group(1)}"')
            except json.JSONDecodeError:
                info["title"] = found.group(1)
    if not info.get("files"):
        info["files"] = []
    return info


def _file_list_from_html(text: str) -> list[dict[str, Any]]:
    """截出 `"file_list":[...]` 这个数组并解析。"""
    match = _FILE_LIST.search(text)
    if not match:
        return []
    try:
        data, _end = json.JSONDecoder().raw_decode(text, match.end() - 1)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _loose_json(raw: str) -> dict[str, Any]:
    """有些字段带单引号或未转义换行，JSON 解析失败时做最小修补。"""
    fixed = raw.replace("'", '"')
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return {}


class ShareTransfer:
    """负责「分享链接 → 我的网盘」这一段。"""

    def __init__(self, client: BaiduClient):
        self.client = client

    def verify(self, surl: str, pwd: str) -> bool:
        """提交提取码。成功时服务端下发 BDCLND Cookie。"""
        if not pwd:
            return True
        params = {"surl": surl, "t": str(int(time.time() * 1000)), "channel": "chunlei", "web": "1"}
        resp = self.client.session.post(
            f"{PAN}/share/verify",
            params=params,
            data={"pwd": pwd, "vcode": "", "vcode_str": ""},
            timeout=self.client.timeout,
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "提取码校验返回不是 JSON", "百度网盘转存") from exc
        if data.get("errno") not in (0, "0", None):
            raise BaiduError(data.get("errno"), str(data.get("show_msg") or "提取码错误"), "百度网盘转存")
        return True

    def _open(self, link: str) -> tuple[str, str, Any]:
        """打开分享链接，返回 (权威 surl, 提取码, 第一次响应的 raw)。

        有提取码时 `/s/<surl>` 会 302 到 `/share/init?surl=..&pwd=..`，那里的
        `surl` 才是 /share/verify 认可的值（不带路径前缀 `1`），所以以它为准。
        """
        surl = share_surl(link)
        if not surl:
            raise BaiduError(105, f"链接里没有 /s/<surl>: {link}", "百度网盘转存")
        pwd = share_pwd(link)
        resp = self.client.session.get(
            f"{PAN}/s/{surl}",
            timeout=self.client.timeout,
            params={"pwd": pwd} if pwd else None,
        )
        query = parse_qs(urlparse(resp.url).query)
        if query.get("surl"):
            surl = str(query["surl"][0]).strip()
        return surl, pwd, resp

    def parse(self, link: str) -> dict[str, Any]:
        """打开分享页并解析出 shareid / share_uk / 文件列表。"""
        surl, pwd, resp = self._open(link)
        info = parse_share_page(resp.text)

        if (not info.get("files") or not info.get("shareid")) and pwd:
            # 需要提取码时页面只有表单，先校验拿到 BDCLND，再重新打开
            self.verify(surl, pwd)
            resp = self.client.session.get(
                f"{PAN}/share/init",
                timeout=self.client.timeout,
                params={"surl": surl, "pwd": pwd},
            )
            info = parse_share_page(resp.text)

        if not info.get("shareid") or not info.get("share_uk"):
            if not pwd:
                raise BaiduError(105, "分享需要提取码，链接里没带 ?pwd=", "百度网盘转存")
            raise BaiduError(105, "分享页没解析出 shareid（链接已失效或已取消分享）", "百度网盘转存")
        info["surl"] = surl
        info["pwd"] = pwd
        return info

    def transfer(self, link: str, save_dir: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
        """把分享里的全部文件转存到 save_dir，返回 {path, files, shareid}。

        `info` 可传入已经 parse 过的结果，省一次页面请求（调用方常要先按体积筛）。
        """
        info = info or self.parse(link)
        files = info.get("files") or []
        if not files:
            raise BaiduError(31066, "分享里没有可转存的文件", "百度网盘转存")

        save_dir = self.client.ensure_dir(save_dir or "/")
        fsidlist = [str(f.get("fs_id") or f.get("fsid") or "") for f in files]
        fsidlist = [fid for fid in fsidlist if fid]

        params = {
            "shareid": info["shareid"],
            "from": info["share_uk"],
            "bdstoken": self.client.bdstoken,
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
            "app_id": "250528",
        }
        sekey = self.client.session.cookies.get("BDCLND")
        if sekey:
            # Cookie 里是 URL 编码过的，sekey 参数要原始值（requests 会再编码一次）
            params["sekey"] = unquote(sekey)

        body = {
            "fsidlist": json.dumps(fsidlist),
            "path": save_dir,
        }
        resp = self.client._post(f"{PAN}/share/transfer", params=params, data=body)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "转存返回不是 JSON", "百度网盘转存") from exc
        if data.get("errno") not in (0, "0", None):
            raise BaiduError(data.get("errno"), str(data.get("show_msg") or data.get("error_msg") or ""), "百度网盘转存")
        return {
            "path": save_dir,
            "shareid": info["shareid"],
            "files": [str(f.get("server_filename") or "") for f in files],
            "names": [str(f.get("server_filename") or "") for f in files],
            # 分享页里的体积，用于「量不到目录体积」时兜底
            "size_bytes": sum(int(f.get("size") or 0) for f in files if str(f.get("isdir") or 0) in ("0", "False", "")),
        }

    def transfer_into(self, link: str, root_dir: str, title: str,
                      info: dict[str, Any] | None = None) -> dict[str, Any]:
        """转存到 root_dir/title（标题会做文件名安全处理）。"""
        target = join_posix(root_dir, safe_name(title))
        result = self.transfer(link, target, info=info)
        result["cloud_path"] = target
        return result


def safe_name(name: str, max_len: int = 80) -> str:
    """去掉文件名里百度不接受/容易出问题的字符。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:max_len] or "untitled").strip()
