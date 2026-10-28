# -*- coding: utf-8 -*-
"""分片上传（C 路线的最后一跳）：本地加密后的文件传到百度网盘。

Web 端上传三步（2026-10 实测）：
1. POST /api/precreate  预上传，带每片 MD5 列表。
   关键：必须带 `autoinit=1`，否则不返回 uploadid（只有 return_type=1 的秒传标记）；
   return_type=1 表示网盘已有这些分片，可以跳过第 2 步直接合并；
2. POST d.pcs.baidu.com/rest/2.0/pcs/superfile2?method=upload&type=tmpfile&partseq=N
   逐片上传（multipart 字段名 file）；
3. POST /api/create?a=commit  合并分片，body 里的 block_list 要用真实的分片 MD5
   （不能填空串，否则 errno -7「该文件所在的文件夹命名不符合要求」这种误导性报错）。
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable

from .client import APP_ID, PCS, PAN, BaiduError, BaiduClient, posix_name

CHUNK_MB = 4
MAX_BLOCKS = 140  # block_list 要塞在 query 里，约 36 字符一个 MD5，超过 ~8KB 百度直接回 HTTP 414
CHUNK_RETRIES = 3


class _BoundedReader:
    """把文件的一段当成流交给 requests，避免大分片整个读进内存。"""

    def __init__(self, handle, length: int):
        self._handle = handle
        self._left = length

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        if size is None or size < 0:
            size = self._left
        size = min(size, self._left)
        data = self._handle.read(size)
        self._left -= len(data)
        return data

    def __len__(self) -> int:
        return self._left


def chunk_md5s(path: str | Path, chunk_size: int) -> list[str]:
    """算每个分片的 MD5（百度预上传要）。"""
    digests: list[str] = []
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digests.append(hashlib.md5(block).hexdigest())
    return digests


class Uploader:
    def __init__(self, client: BaiduClient, chunk_mb: int = CHUNK_MB):
        self.client = client
        self.chunk_size = max(1, int(chunk_mb)) * 1024 * 1024
        self.log = _logger()

    def upload(self, local_path: str | Path, remote_dir: str,
               progress: Callable[[int, int], None] | None = None) -> str:
        """上传单个文件到 remote_dir，返回网盘上的完整路径。"""
        path = Path(local_path)
        if not path.is_file():
            raise BaiduError(31066, f"本地文件不存在: {path}", "百度上传")
        size = path.stat().st_size
        if size <= 0:
            raise BaiduError(2, f"文件是空的: {path}", "百度上传")

        remote_dir = self.client.ensure_dir(remote_dir)
        remote_path = f"{remote_dir.rstrip('/')}/{posix_name(str(path))}"

        chunk_size = self.chunk_size
        chunks = max(1, (size + chunk_size - 1) // chunk_size)
        if chunks > MAX_BLOCKS:
            # 分片太大塞不进 query，只能把分片调大。实测：
            # block_list 在 query 里约 8KB 就 HTTP 414；放 body 则上传分片会被服务器掐断。
            need = -(-size // MAX_BLOCKS)
            chunk_size = -(-need // (1024 * 1024)) * (1024 * 1024)
            chunks = max(1, (size + chunk_size - 1) // chunk_size)
            self.log.info(
                "文件 %.2fGB，分片调到 %dMB（%d 片，block_list 才不会过长）",
                size / 1e9, chunk_size // (1024 * 1024), chunks,
            )
        block_list = chunk_md5s(path, chunk_size)
        upload_id, rapid = self._precreate(remote_path, size, block_list)
        if upload_id:
            self._upload_chunks(path, remote_path, upload_id, chunks, chunk_size, progress)
        elif not rapid:
            raise BaiduError(-1, "预上传没返回 uploadid，也不同意秒传", "百度上传")
        self._create(remote_path, size, upload_id, chunks, block_list)
        return remote_path

    # ---------- 三步 ----------
    def _precreate(self, remote_path: str, size: int, block_list: list[str]) -> tuple[str, bool]:
        params = {
            "path": remote_path,
            "size": str(size),
            "isdir": "0",
            "rtype": "3",
            "block_list": _dump(block_list),
            "autoinit": "1",
            **self.client.common_params(),
        }
        resp = self.client._post(f"{PAN}/api/precreate", params=params, data={"target_path": remote_path})
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "预上传返回不是 JSON", "百度上传") from exc
        errno = data.get("errno")
        if errno not in (0, "0", None):
            raise BaiduError(errno, str(data.get("show_msg") or ""), "百度上传")
        upload_id = str(data.get("uploadid") or "")
        return upload_id, int(data.get("return_type") or 0) == 1

    def _upload_chunks(self, path: Path, remote_path: str, upload_id: str, chunks: int,
                       chunk_size: int,
                       progress: Callable[[int, int], None] | None) -> None:
        url = f"{PCS}/rest/2.0/pcs/superfile2"
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        done = 0
        with open(path, "rb") as handle:
            for seq in range(chunks):
                offset = seq * chunk_size
                handle.seek(offset)
                length = min(chunk_size, path.stat().st_size - offset)
                if length <= 0:
                    break
                params = {
                    "method": "upload",
                    "type": "tmpfile",
                    "path": remote_path,
                    "uploadid": upload_id,
                    "partseq": str(seq),
                    "app_id": APP_ID,
                }
                last_error: Exception | None = None
                for attempt in range(1, CHUNK_RETRIES + 1):
                    handle.seek(offset)
                    files = {"file": ("blob", _BoundedReader(handle, length), mime)}
                    try:
                        resp = _post_chunk(self.client, url, params, files, attempt)
                    except Exception as exc:  # noqa: BLE001 - 传输层什么都可能抛
                        last_error = exc
                        self.log.warning("第 %d 片第 %d 次上传中断：%s", seq, attempt, exc)
                        time.sleep(min(30, 2 * attempt))
                        continue
                    if resp.status_code != 200:
                        last_error = BaiduError(-1, f"分片 {seq} 上传失败 HTTP {resp.status_code}", "百度上传")
                        time.sleep(2 * attempt)
                        continue
                    try:
                        data = resp.json()
                    except ValueError:
                        data = {}
                    if data.get("errno") not in (None, 0, "0"):
                        last_error = BaiduError(data.get("errno"), f"第 {seq} 片上传失败", "百度上传")
                        time.sleep(2 * attempt)
                        continue
                    last_error = None
                    break
                if last_error is not None:
                    raise last_error if isinstance(last_error, BaiduError) else BaiduError(
                        -1, f"第 {seq} 片上传失败：{last_error}", "百度上传")
                done += 1
                if progress:
                    progress(done, chunks)

    def _create(self, remote_path: str, size: int, upload_id: str, chunks: int,
                block_list: list[str]) -> None:
        params = {"a": "commit", **self.client.common_params()}
        body = {
            "path": remote_path,
            "size": str(size),
            "isdir": "0",
            "rtype": "3",
            "uploadid": upload_id,
            "block_list": _dump(block_list or [""] * chunks),
        }
        resp = self.client._post(f"{PAN}/api/create", params=params, data=body)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "合并分片返回不是 JSON", "百度上传") from exc
        errno = data.get("errno")
        if errno not in (0, "0", None):
            raise BaiduError(errno, str(data.get("show_msg") or ""), "百度上传")

    # ---------- 上传后校验 ----------
    def verify(self, remote_path: str, expect_bytes: int, tolerance: float = 0.01) -> bool:
        """确认网盘上的文件大小和本地一致。"""
        try:
            items = self.client.list_dir(_parent(remote_path))
        except BaiduError:
            return False
        name = posix_name(remote_path)
        for item in items:
            if str(item.get("server_filename") or "") == name:
                actual = int(item.get("size") or 0)
                if expect_bytes <= 0:
                    return actual > 0
                return abs(actual - expect_bytes) <= max(1024 * 1024, expect_bytes * tolerance)
        return False


def _dump(items: list[str]) -> str:
    import json

    return json.dumps(items)


def _loose_ssl_session(client: BaiduClient) -> "requests.Session":
    """降一级 TLS 安全等级的会话。

    实测 d.pcs.baidu.com 个别节点用的是 1024 位 RSA 证书，Python 3.13+ 的默认
    安全等级会直接回 `EE certificate key too weak`。证书链照样校验，只是允许弱密钥。
    """
    import ssl

    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.poolmanager import PoolManager

    class Adapter(HTTPAdapter):
        def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **kw: Any) -> None:
            ctx = ssl.create_default_context()
            try:
                ctx.set_ciphers("DEFAULT@SECLEVEL=1")
            except ssl.SSLError:  # pragma: no cover - 取决于本机 OpenSSL
                pass
            self.poolmanager = PoolManager(
                num_pools=connections, maxsize=maxsize, block=block,
                ssl_context=ctx, **kw,
            )

    session = requests.Session()
    session.trust_env = client.session.trust_env
    session.headers.update(client.session.headers)
    session.cookies.update(client.session.cookies)
    session.mount("https://", Adapter())
    return session


def _post_chunk(client: BaiduClient, url: str, params: dict, files: dict, attempt: int):
    """上传一个分片；第 2 次以后换一个新的宽松 TLS 会话再试。"""
    if attempt <= 1:
        return client.session.post(url, params=params, files=files, timeout=max(client.timeout, 120))
    session = _loose_ssl_session(client)
    try:
        return session.post(url, params=params, files=files, timeout=max(client.timeout, 120))
    finally:
        session.close()


def _parent(remote_path: str) -> str:
    from .client import posix_parent

    return posix_parent(remote_path)


def _logger() -> Any:
    import logging

    return logging.getLogger("galsave")


def local_file_mb(path: str | Path) -> float:
    try:
        return round(os.path.getsize(path) / (1024 ** 2), 2)
    except OSError:
        return 0.0
