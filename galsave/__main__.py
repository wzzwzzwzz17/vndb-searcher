# -*- coding: utf-8 -*-
"""命令行入口。

    python -m galsave login      # 打开浏览器窗口登录百度网盘（用户本人操作）
    python -m galsave status     # 看登录态、入库量、批次配额
    python -m galsave plan       # 各分数档还有多少部可以捞
    python -m galsave dryrun X   # 只搜索不下载，看某部作品能找到什么
    python -m galsave run        # 正式跑：分级 → 搜索 → 落地 → 入库
    python -m galsave quota      # 查网盘容量
    python -m galsave magnets    # 导出没能自动入库的磁力
    python -m galsave reorg      # 把旧目录结构整理成 v<编号> <日文名>
    python -m galsave prune      # 体检入库目录：诱饵分享删掉、多平台包只留 PC
    python -m galsave decrypt F  # 把 C 路线加密文件还原到本地
    python -m galsave selftest   # 纯本地自检，不联网
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, cpath, load_config
from .db import TaskDB
from .logger import get_logger, setup_logging


def _bootstrap(args: argparse.Namespace) -> dict:
    cfg = load_config(getattr(args, "config", None))
    setup_logging(cpath(cfg, "paths.log_dir"))
    return cfg


def cmd_login(args: argparse.Namespace) -> int:
    _bootstrap(args)
    from baidu_cloud import auth

    if args.clear:
        print("已删除本机凭据。" if auth.clear_credentials() else "没有可删除的凭据。")
        return 0
    if args.text:
        from baidu_cloud.client import BaiduClient

        client = BaiduClient.from_cookie_text(args.text, save=True)
        if not client.check_login():
            print("粘贴的 Cookie 验证没通过（可能复制不完整或已经过期）。")
            return 1
        info = client.user_info()
        print(f"已用粘贴的 Cookie 登录：{info.get('name') or '(未取到昵称)'}")
        return 0
    ok = auth.login(force=bool(getattr(args, "force", False)))
    if ok:
        print("登录态验证通过。" if auth.verify() else "凭据已保存，但验证没通过。")
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from baidu_cloud import auth

    db = TaskDB(cpath(cfg, "paths.db"))
    stats = db.stats()
    print(f"配置文件 : {cfg.get('_path', 'config.yaml')}")
    print(f"数据库   : {stats['db']}")
    print(f"已入库   : {stats['entries']} 部 / {stats['total_gb']:.2f}GB "
          f"（上限 {float(cfg.get('max_total_gb') or 0):.0f}GB）")
    print(f"来源分布 : {stats['by_source'] or '空'}")
    print(f"待重试   : {stats['retry']} 条")
    if stats.get("pending_magnet"):
        print(f"待处理磁力: {stats['pending_magnet']} 条（网盘不接受磁力离线，用 python -m galsave magnets 导出）")
    if stats.get("missing"):
        print(f"未收录   : {stats['missing']} 部（三个渠道都没成功，python -m galsave missing 查看）")
    print(f"C 源本批 : {stats['c_quota_gb']:.2f}GB / {float(cfg.get('c_quota_per_batch_gb') or 0):.0f}GB")

    from .downloader.torrent import Aria2Downloader

    magnet_ok, magnet_why = Aria2Downloader(cfg).available()
    if magnet_ok:
        print(f"磁力下载 : {magnet_why}（GGBase 磁力会先下到本地再加密上传）")
    else:
        print(f"磁力下载 : 不可用（{magnet_why}）")

    if not auth.is_logged_in():
        print("网盘登录 : 未登录（执行 python -m galsave login）")
        return 1
    from baidu_cloud import BaiduClient

    try:
        valid = BaiduClient().check_login()
    except Exception as exc:  # noqa: BLE001 - 只是展示信息
        print(f"网盘登录 : 检查失败（{exc}）")
        return 1
    if not valid:
        print("网盘登录 : 凭据已过期（执行 python -m galsave login 重新登录）")
        return 1
    print("网盘登录 : 已登录")
    if args.quota:
        try:
            client = BaiduClient()
            info = client.user_info()
            quota = client.quota()
            print(f"账号     : {info.get('name') or '(未知)'}")
            print(f"网盘容量 : 总 {quota['total_gb']:.1f}GB / 已用 {quota['used_gb']:.1f}GB / "
                  f"剩余 {quota['free_gb']:.1f}GB")
        except Exception as exc:  # noqa: BLE001 - 只是展示信息
            print(f"查询网盘信息失败: {exc}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .scheduler import Scheduler

    scheduler = Scheduler(cfg)
    rows = scheduler.plan(max_pages=args.pages)
    print(f"{'分数档':>6}  {'该档作品数':>10}  {'未入库':>8}")
    for row in rows:
        if row.get("count", 0) < 0:
            print(f"{row['bucket']:>6.1f}  {'查询失败':>10}  {row.get('error', '')}")
            continue
        print(f"{row['bucket']:>6.1f}  {row['count']:>10}  {row['todo']:>8}")
    print(f"\n已入库 {scheduler.db.count_done()} 部 / {scheduler.total_gb():.2f}GB "
          f"/ 上限 {scheduler.max_total_gb:.0f}GB")
    return 0


def cmd_dryrun(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .crawler import SearchEngine
    from .vndb import VndbClient

    vndb = VndbClient(cfg)
    if args.query.lower().startswith("v") and args.query[1:].isdigit():
        vn = vndb.get_vn(args.query.lower())
        vns = [vn] if vn else []
    else:
        vns = vndb.search(args.query, limit=args.limit)
    if not vns:
        print(f"VNDB 上没找到: {args.query}")
        return 1

    engine = SearchEngine(cfg)
    saver = None
    for vn in vns:
        print(f"\n{vn.id}  {vn.preferred_name}  ({vn.display_rating})  olang={vn.olang}")
        print(f"  别名: {', '.join(vn.names[1:4])}")
        if vn.olang and vn.olang != "ja":
            print("  ⚠ 官方原始语言不是日语，正式跑会被跳过")
        from .cloud import CloudSaver

        saver = saver or CloudSaver(cfg, TaskDB(cpath(cfg, "paths.db")))
        print(f"  入库目录: {saver.folder_for(vn.id, vn.preferred_name)}")
        found = engine.find(vn.names)
        if not found:
            print("  三站都没有匹配资源")
            continue
        for res in found[:8]:
            hint = "" if res.link or res.source_type != "share" else "（真链接要正式跑时才解析）"
            print(f"  - {res}{hint}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .scheduler import Scheduler

    buckets = None
    if args.buckets:
        buckets = [float(x) for x in str(args.buckets).replace(" ", "").split(",") if x]
    scheduler = Scheduler(cfg)
    if args.skip:
        skip = {x.strip() for x in str(args.skip).replace("，", ",").split(",") if x.strip()}
        bad = skip - {"share", "direct", "magnet"}
        if bad:
            print(f"未知来源: {'、'.join(sorted(bad))}（只能是 share / direct / magnet）")
            return 2
        scheduler.skip_types = skip
        print(f"本轮跳过来源: {'、'.join(sorted(skip))}")
    summary = scheduler.run(buckets=buckets, max_vns=args.max_vns, skip_retries=args.no_retries)
    print(f"\n本次成功 {summary['success']} 部，失败 {summary['failed']} 部，"
          f"累计 {summary['total_gb']:.2f}GB / {summary['entries']} 部")
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from baidu_cloud import BaiduClient
    from baidu_cloud.client import BaiduError

    db = TaskDB(cpath(cfg, "paths.db"))
    if args.reset:
        used = db.c_quota_used_gb()
        db.reset_c_quota()
        print(f"已把 C 源配额清零（上一批用了 {used:.2f}GB），可以开始下载新一批直链了。")
        return 0

    client = BaiduClient()
    if not client.check_login():
        print("百度网盘登录信息无效或过期，请执行: python -m galsave login")
        return 1
    try:
        info = client.user_info()
        quota = client.quota()
    except BaiduError as exc:
        print(f"查询失败: {exc}")
        return 1
    print(f"账号   : {info.get('name') or '(未知)'}  会员: {'是' if info.get('vip') else '未知/否'}")
    print(f"总空间 : {quota['total_gb']:.1f}GB")
    print(f"已用   : {quota['used_gb']:.1f}GB")
    print(f"剩余   : {quota['free_gb']:.1f}GB")
    limit = float(cfg.get("c_quota_per_batch_gb") or 500)
    print(f"C 源本批 : {db.c_quota_used_gb():.2f}GB / {limit:.0f}GB"
          f"（换批: python -m galsave quota --reset）")
    root = str((cfg.get("baidu") or {}).get("root_dir") or "/gal_save")
    try:
        used = client.dir_size_gb(root)
        print(f"{root} 占 {used:.2f}GB")
    except Exception as exc:  # noqa: BLE001
        print(f"统计 {root} 失败: {exc}")
    return 0


def cmd_magnets(args: argparse.Namespace) -> int:
    """导出没能自动入库的磁力（百度网盘已不接受磁力离线）。"""
    cfg = _bootstrap(args)
    db = TaskDB(cpath(cfg, "paths.db"))
    rows = db.pending_magnets(limit=args.limit or 0)
    if not rows:
        print("没有待处理的磁力。")
        return 0
    lines = []
    for row in rows:
        size = f"{row['size_gb']:.2f}GB" if row.get("size_gb") else "体积未知"
        lines.append(f"{row['title']}  [{row['site']}]  {size}  {row['magnet']}")
    if args.out:
        out = Path(args.out)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写出 {len(lines)} 条到 {out}")
    else:
        for line in lines:
            print(line)
    print(f"\n共 {len(rows)} 条待处理（磁力离线已不可用，需要用本地 BT 客户端或其它方式自行处理）")
    return 0


def cmd_missing(args: argparse.Namespace) -> int:
    """三个渠道都没捞到的作品：导出或查看根目录文档。"""
    cfg = _bootstrap(args)
    from .notebook import sync, write_missing

    db = TaskDB(cpath(cfg, "paths.db"))
    rows = db.missing(limit=args.limit or 0)
    path = sync(cfg, db) if not args.limit else write_missing(cfg, rows)
    if not rows:
        print("没有未收录作品。")
        return 0
    for row in rows:
        print(f"{row['vndb_id']}  {row['title']}  {float(row['rating'] or 0):.2f}  {row['reason'] or ''}")
    print(f"\n共 {len(rows)} 部，文档：{path}")
    return 0


def cmd_reorg(args: argparse.Namespace) -> int:
    """把已有入库记录整理成 /gal_save/v<编号> <日文名> 的统一结构。"""
    cfg = _bootstrap(args)
    from baidu_cloud import BaiduClient

    from .cloud import CloudSaver
    from .reorg import apply_reorg, plan_reorg

    db = TaskDB(cpath(cfg, "paths.db"))
    client = BaiduClient()
    if not client.check_login():
        print("百度网盘登录信息无效或过期，请执行: python -m galsave login")
        return 1
    saver = CloudSaver(cfg, db, client=client)
    from .vndb import VndbClient

    items = plan_reorg(saver, db, client, vndb=VndbClient(cfg))
    if not items:
        print("没有入库记录，无需整理。")
        return 0
    for item in items:
        print(item)
    todo = [i for i in items if i.kind != "skip"]
    if not todo:
        print("\n目录结构已经全部合规。")
        return 0
    if not args.apply:
        print(f"\n共 {len(todo)} 条待整理。确认无误后加 --apply 真动云端。")
        return 0
    done = apply_reorg(saver, db, client, todo)
    print(f"\n已整理 {done}/{len(todo)} 条。")
    return 0 if done == len(todo) else 1


def cmd_prune(args: argparse.Namespace) -> int:
    """检查已入库目录：诱饵分享整目录删掉，多平台包只留 PC。"""
    cfg = _bootstrap(args)
    from baidu_cloud import BaiduClient

    from .prune import apply_report, plan_prune, walk_cloud

    db = TaskDB(cpath(cfg, "paths.db"))
    rows = db.all_done()
    if args.id:
        want = {args.id.lstrip("vV")}
        rows = [r for r in rows if str(r.get("vndb_id") or "").lstrip("vV") in want]
    if not rows:
        print("没有入库记录。")
        return 0

    threshold = float(args.threshold or (cfg.get("prune") or {}).get("threshold_gb") or 9.0)
    client = BaiduClient()
    if not client.check_login():
        print("百度网盘登录信息无效或过期，请执行: python -m galsave login")
        return 1

    junk: list[tuple[dict[str, Any], Any]] = []
    for row in rows:
        path = str(row.get("cloud_path") or "")
        if not path:
            continue
        report = plan_prune(walk_cloud(client, path), threshold_gb=threshold)
        print(f"\n{row.get('vndb_id')} {row.get('title')}")
        print(f"  {report.summary()}")
        if not report.ok:
            junk.append((row, report))
            continue
        for entry in report.drop:
            print(f"  - 删除 {entry.rel}  {entry.size_gb:.2f}GB")
        for note in report.warnings:
            print(f"  ! {note}")
        if report.drop and args.apply:
            removed = apply_report(client, path, report)
            print(f"  → 已删除 {removed} 项，省下 {report.drop_gb:.2f}GB")

    if junk:
        total = sum(r.total_gb for _, r in junk)
        print(f"\n以上 {len(junk)} 部共 {total:.2f}GB 不是游戏本体。")
        if args.apply:
            for row, _report in junk:
                client.delete([str(row.get("cloud_path"))])
                db.forget_done(row.get("vndb_id"))
                print(f"  → 已删除 {row.get('cloud_path')}，并从 done 表移除，可重新捞取")
        else:
            print("加 --apply 会删掉这些目录并把它们从 done 表移除（之后可以重新捞取）。")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    cfg = _bootstrap(args)
    from .crypto.enc import CryptoError, decrypt_file

    password = args.password or str((cfg.get("encrypt") or {}).get("password") or "")
    src = Path(args.file)
    if not src.exists():
        print(f"文件不存在: {src}")
        return 1
    dest = Path(args.out) if args.out else None
    try:
        out = decrypt_file(src, password, dest=dest)
    except CryptoError as exc:
        print(f"解密失败: {exc}")
        return 1
    print(f"已还原到 {out}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from .selftest import run_selftest

    return run_selftest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m galsave",
        description="VNDB 分级评分 → 三站搜索 → 百度网盘归档（仅个人收藏，不传播）",
    )
    parser.add_argument("--config", help="指定配置文件（默认仓库根 config.yaml）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="登录百度网盘（会打开浏览器窗口）")
    p_login.add_argument("--text", help="直接粘贴浏览器里的一整行 Cookie（无浏览器时用）")
    p_login.add_argument("--clear", action="store_true", help="删除本机保存的凭据")
    p_login.add_argument("--force", action="store_true", help="忽略已保存的凭据，强制重新登录")
    p_login.set_defaults(func=cmd_login)

    p_status = sub.add_parser("status", help="查看登录态与入库统计")
    p_status.add_argument("--quota", action="store_true", help="同时查网盘容量（会联网）")
    p_status.set_defaults(func=cmd_status)

    p_plan = sub.add_parser("plan", help="查看各分数档还有多少部")
    p_plan.add_argument("--pages", type=int, default=10, help="每档最多翻几页（默认 10）")
    p_plan.set_defaults(func=cmd_plan)

    p_dry = sub.add_parser("dryrun", help="只搜索不下载")
    p_dry.add_argument("query", help="VNDB id（如 v17）或作品名")
    p_dry.add_argument("--limit", type=int, default=3, help="按名字搜索时最多看几部")
    p_dry.set_defaults(func=cmd_dryrun)

    p_run = sub.add_parser("run", help="正式运行")
    p_run.add_argument("--buckets", help="只跑指定分数档，如 8.0,7.5")
    p_run.add_argument("--max-vns", type=int, default=0, help="本次最多处理几部（0=不限）")
    p_run.add_argument("--no-retries", action="store_true", help="跳过重试队列")
    p_run.add_argument("--skip", help="本轮跳过的来源，逗号分隔：share,direct,magnet（--skip direct 就完全不占校园流量）")
    p_run.set_defaults(func=cmd_run)

    p_quota = sub.add_parser("quota", help="查网盘容量")
    p_quota.add_argument("--reset", action="store_true", help="把 C 源本批配额清零，开始新一批")
    p_quota.set_defaults(func=cmd_quota)

    p_mag = sub.add_parser("magnets", help="导出待处理的磁力（网盘不接受磁力离线）")
    p_mag.add_argument("--out", help="写到文件（每行一条）")
    p_mag.add_argument("--limit", type=int, default=0, help="只导出最近 N 条")
    p_mag.set_defaults(func=cmd_magnets)

    p_reorg = sub.add_parser("reorg", help="整理已入库条目的云端目录（v<编号> <日文名>）")
    p_reorg.add_argument("--apply", action="store_true", help="真的移动/改名（默认只看计划）")
    p_reorg.set_defaults(func=cmd_reorg)

    p_missing = sub.add_parser("missing", help="三个渠道都没捞到的作品（导出成工作区根目录文档）")
    p_missing.add_argument("--limit", type=int, default=0, help="只看最近 N 条")
    p_missing.set_defaults(func=cmd_missing)

    p_prune = sub.add_parser("prune", help="体检已入库目录：诱饵分享删掉，多平台包只留 PC")
    p_prune.add_argument("--apply", action="store_true", help="真的删除（默认只报告）")
    p_prune.add_argument("--id", help="只处理某一部，如 v19073")
    p_prune.add_argument("--threshold", type=float, help="超过多少 GB 才剪裁（默认取配置）")
    p_prune.set_defaults(func=cmd_prune)

    p_dec = sub.add_parser("decrypt", help="还原 C 路线加密文件")
    p_dec.add_argument("file")
    p_dec.add_argument("--out", help="输出路径")
    p_dec.add_argument("--password", help="覆盖配置里的密码")
    p_dec.set_defaults(func=cmd_decrypt)

    sub.add_parser("selftest", help="本地自检（不联网）").set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"配置错误: {exc}")
        return 2
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底
        get_logger().exception("运行失败: %s", exc)
        print(f"运行失败: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
