# -*- coding: utf-8 -*-
"""百度网盘相关能力：登录（auth）、Web 接口（client）、转存、离线下载、加密上传、体积统计。

本包只做「把资源存进自己的百度网盘」，不对外传播、不商用，仅用于个人收藏保存。
登录凭据只保存在本机 baidu_cloud/credentials.json。

登录（需要时打开真实浏览器窗口，由用户本人操作）：
    python -m baidu_cloud.auth login
"""

from __future__ import annotations

from .client import BaiduClient, BaiduError, join_posix, parse_cookie_text, posix_parent
from .offline import OfflineDownloader
from .share import ShareTransfer, safe_name, share_pwd, share_surl
from .size import fmt_gb, measure, parse_human_gb
from .upload import Uploader

__all__ = [
    "BaiduClient",
    "BaiduError",
    "OfflineDownloader",
    "ShareTransfer",
    "Uploader",
    "join_posix",
    "posix_parent",
    "parse_cookie_text",
    "safe_name",
    "share_pwd",
    "share_surl",
    "measure",
    "fmt_gb",
    "parse_human_gb",
]
