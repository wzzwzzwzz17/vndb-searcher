# -*- coding: utf-8 -*-
"""galsave：基于 VNDB 分级评分 + 百度网盘的个人收藏捞取流水线。

模块划分：
    config.py        配置（默认值 + config.yaml 覆盖）
    logger.py        日志
    db.py            SQLite：已入库、重试、配额、总量统计
    vndb.py          VNDB 分级拉取（仅 olang=ja）
    title_match.py   中日英文标题归一化与模糊匹配
    crawler/         三源爬虫：ggbase 磁力、鲲 分享、真红直链
    cloud.py         百度网盘动作（离线 / 转存 / 加密上传 / 容量）
    crypto/          AES-GCM 分块加密
    downloader/      直链下载与流量配额
    scheduler.py     分级循环调度、停机判断
    __main__.py      命令行入口

只用于个人收藏，不商用、不传播。
"""

__version__ = "0.1.0"
