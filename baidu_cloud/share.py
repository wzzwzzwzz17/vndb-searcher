# -*- coding: utf-8 -*-
"""分享链接转存（B 路线）：解析分享页 → 转存到自己的网盘目录。

流程（Web 端同款）：
1. GET https://pan.baidu.com/s/<surl>?pwd=xxxx，页面里的 yunData.setData({...}) 含
   SHAREID / SHARE_UK / FILEINFO / 文件列表；
2. 带提取码的分享，先 POST /share/verify?surl=..&t=..&channel=chunlei&web=1 提交 pwd，
   服务端会下发 BDCLND Cookie（这就是「已通过提取码」的凭据）；
3. POST /share/transfer?shareid=..&from=<SHARE_UK>&bdstoken=..&sekey=<BDCLND>
   body: fsidlist=[...]&path=/目标目录。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .client import PAN, BaiduError, BaiduClient, join_posix

_YDATA = re.compile(r"yunData\.setData\(\s*(\{.*?\})\s*\)\s*;", re.S)
_SURL = re.compile(r"(?:pan|yun)\.baidu\.com/s/([\w\-]+)")


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

    页面结构变过好几次，所以这里尽量宽容：先找 yunData.setData，再退回到
    零散的正则；找不到就返回空字典由调用方报错。
    """
    info: dict[str, Any] = {}
    match = _YDATA.search(html or "")
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

    if not info.get("shareid"):
        found = re.search(r'"?shareid"?\s*[:=]\s*"?(\d+)', html or "")
        if found:
            info["shareid"] = found.group(1)
    if not info.get("share_uk"):
        found = re.search(r'"?share_uk"?\s*[:=]\s*"?(\d+)', html or "")
        if found:
            info["share_uk"] = found.group(1)
    if not info.get("files"):
        info["files"] = []
    return info


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

    def parse(self, link: str) -> dict[str, Any]:
        """打开分享页并解析出 shareid / share_uk / 文件列表。"""
        surl = share_surl(link)
        if not surl:
            raise BaiduError(105, f"链接里没有 /s/<surl>: {link}", "百度网盘转存")
        pwd = share_pwd(link)

        url = f"{PAN}/s/{surl}"
        if pwd:
            url = f"{url}?pwd={pwd}"
        resp = self.client.session.get(url, timeout=self.client.timeout,
                                      params={"pwd": pwd} if pwd else None)
        html = resp.text
        info = parse_share_page(html)

        if not info.get("shareid") and pwd:
            # 需要提取码时页面只有表单，先校验再重新打开
            self.verify(surl, pwd)
            resp = self.client.session.get(f"{PAN}/s/{surl}", timeout=self.client.timeout)
            info = parse_share_page(resp.text)

        if not info.get("shareid") or not info.get("share_uk"):
            if "验证" in html or "verify" in html.lower() or pwd:
                raise BaiduError(105, "分享页没解析出 shareid（可能需要提取码或链接已失效）", "百度网盘转存")
            raise BaiduError(105, "分享页结构变化，没解析出 shareid", "百度网盘转存")
        info["surl"] = surl
        info["pwd"] = pwd
        return info

    def transfer(self, link: str, save_dir: str) -> dict[str, Any]:
        """把分享里的全部文件转存到 save_dir，返回 {path, files, shareid}。"""
        info = self.parse(link)
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
            params["sekey"] = sekey

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
        }

    def transfer_into(self, link: str, root_dir: str, title: str) -> dict[str, Any]:
        """转存到 root_dir/title（标题会做文件名安全处理）。"""
        target = join_posix(root_dir, safe_name(title))
        result = self.transfer(link, target)
        result["cloud_path"] = target
        return result


def safe_name(name: str, max_len: int = 80) -> str:
    """去掉文件名里百度不接受/容易出问题的字符。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:max_len] or "untitled").strip()
