# -*- coding: utf-8 -*-
"""自检：不联网、不动真实网盘，验证解析/匹配/加密/数据库这些纯逻辑。

用法：python -m galsave selftest
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .crawler.base import parse_size_gb
from .crypto.enc import CryptoError, decrypt_file, encrypt_file, is_encrypted
from .db import TaskDB
from .title_match import best_match, normalize, search_terms, similarity


def _check(name: str, fn: Callable[[], bool]) -> tuple[str, bool, str]:
    try:
        ok = bool(fn())
        return name, ok, "" if ok else "返回 False"
    except Exception as exc:  # noqa: BLE001 - 自检就是要捕获所有异常
        return name, False, f"{type(exc).__name__}: {exc}"


def run_selftest() -> int:
    from baidu_cloud.offline import STATUS_TEXT
    from baidu_cloud.share import parse_share_page, share_pwd, share_surl

    checks: list[tuple[str, Callable[[], bool]]] = []

    # 体积解析
    checks.append(("parse_size_gb 10.5G", lambda: parse_size_gb("10.5G") == 10.5))
    checks.append(("parse_size_gb 872.11 MB", lambda: abs(parse_size_gb("872.11 MB") - 0.872) < 0.01))
    checks.append(("parse_size_gb 1.2 TB", lambda: parse_size_gb("1.2 TB") == 1200.0))
    checks.append(("parse_size_gb 空串", lambda: parse_size_gb("") == 0.0))

    # 标题匹配
    checks.append(("normalize 全角", lambda: normalize("Ｒａｎｃｅ　１０！") == "rance 10"))
    checks.append(("相似 完全相同", lambda: similarity("ランス10", "ランス10") == 100.0))
    checks.append(("相似 副标题", lambda: similarity("ランス10", "ランス10 -決戦-") >= 88))
    checks.append(("相似 不相关", lambda: similarity("クラナド", "智代アフター") < 60))
    checks.append(("best_match 选最像的", lambda: best_match(
        "ランス10", ["ランス01", "ランス10 -決戦-", "ランス9"])[1] == "ランス10 -決戦-"))
    checks.append(("search_terms 有主标题", lambda: "ランス10" in search_terms(["ランス10 -決戦-"])))

    # 分享链接解析
    checks.append(("share_surl 带 pwd", lambda: share_surl(
        "https://pan.baidu.com/s/1Dau8DVO5_HcMjOD4nFA4Qg?pwd=t7cp") == "1Dau8DVO5_HcMjOD4nFA4Qg"))
    checks.append(("share_pwd 取提取码", lambda: share_pwd(
        "https://pan.baidu.com/s/1abc?pwd=t7cp") == "t7cp"))
    checks.append(("parse_share_page yunData", lambda: parse_share_page(
        '<script>yunData.setData({"SHAREID":"123","SHARE_UK":"456",'
        '"FILEINFO":[{"fs_id":"9","server_filename":"a.zip"}]});</script>'
    )["shareid"] == "123"))
    checks.append(("离线状态文案", lambda: STATUS_TEXT[2] == "已完成"))

    # 数据库
    def db_roundtrip() -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            db = TaskDB(Path(tmp) / "t.db")
            db.mark_done("v1", 8.5, "A", "magnet", "ggbase", "magnet:?xt=1", "/a", 12.5)
            db.mark_done("v2", 7.0, "B", "share", "kungal", "link", "/b", 7.5)
            if not db.is_done("v1") or db.count_done() != 2:
                return False
            if abs(db.total_gb() - 20.0) > 0.01:
                return False
            db.add_c_gb(1.5)
            db.add_c_gb(2.0)
            if abs(db.c_quota_used_gb() - 3.5) > 0.01:
                return False
            db.reset_c_quota()
            if db.c_quota_used_gb() != 0:
                return False
            db.add_retry("v3", "magnet", "ggbase", "失败", 3)
            db.clear_retry("v3", "magnet")
            db.mark_seen("v1", 8.5, "A")
            clean = db.retry_count() == 0
            db.close()
            return clean

    checks.append(("TaskDB 入库/配额/重试", db_roundtrip))

    # 加密往返
    def crypto_roundtrip() -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.bin"
            payload = b"GALGAME" * 300_000  # ~2MB，跨多个 1MB 块
            src.write_bytes(payload)
            enc = encrypt_file(src, "pw-test")
            if not is_encrypted(enc) or enc.suffix != ".png":
                return False
            if enc.read_bytes()[: len(payload)] == payload:
                return False
            out = decrypt_file(enc, "pw-test")
            if out.read_bytes() != payload:
                return False
            try:
                decrypt_file(enc, "wrong-pw", dest=Path(tmp) / "bad.bin")
            except CryptoError:
                return True
            return False

    checks.append(("AES-GCM 加密往返 + 错密码拒绝", crypto_roundtrip))

    results = [_check(name, fn) for name, fn in checks]

    failures = 0
    for name, ok, error in results:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  ← {error}" if error else ""))
        failures += 0 if ok else 1

    total = len(results)
    print(f"\n{total - failures}/{total} 项通过")
    return 0 if failures == 0 else 1
