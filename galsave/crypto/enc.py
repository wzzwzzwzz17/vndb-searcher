# -*- coding: utf-8 -*-
"""C 路线用的流式加密：AES-256-GCM + PBKDF2 派生密钥。

为什么要加密：直链下载的原始文件（压缩包/镜像）会被网盘的风控机制判成
「疑似侵权内容」而和谐，加密后再改名成 .png，网盘只看到一堆随机字节。

文件格式（自描述，方便以后换参数也能解开）：

    magic       8B   b"GALSAVG1"
    version     1B   0x01
    kdf_iter    4B   大端，PBKDF2-HMAC-SHA256 迭代次数
    salt       16B
    nonce_base 12B   每块 nonce 由它 + 块序号派生
    之后是连续的块：
      block_len 4B   明文长度（大端）
      nonce    12B   本块 GCM nonce
      sealed   block_len+16B  密文 + GCM tag

按块各自带 tag，是为了能边读边写、内存占用恒定（大文件不会爆内存）。

解密必须用同一个密码；密码丢了文件就废了，所以密码存在本机 config 里。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

MAGIC = b"GALSAVG1"
VERSION = 1
KDF_ITERATIONS = 240_000
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN = len(MAGIC) + 1 + 4 + SALT_LEN + NONCE_LEN
CHUNK = 1024 * 1024
ENCRYPTED_SUFFIX = ".png"


class CryptoError(RuntimeError):
    """密码错误、文件损坏、不是本程序的加密文件。"""


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    if not password:
        raise CryptoError("加密密码为空")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=int(iterations))
    return kdf.derive(password.encode("utf-8"))


def is_encrypted(path: str | Path) -> bool:
    """靠文件头判断是不是本程序加密出来的文件。"""
    try:
        with open(path, "rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_file(
    src: str | Path,
    password: str,
    dest: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """加密 src，写到 dest（默认同名 + .png），返回 dest。

    GCM 是流式加密：整段 ciphertext 是连续的，最后写 16 字节 tag。
    """
    src_path = Path(src)
    if not src_path.is_file():
        raise CryptoError(f"源文件不存在: {src_path}")
    total = src_path.stat().st_size
    if total <= 0:
        raise CryptoError(f"源文件是空的: {src_path}")

    out_path = Path(dest) if dest else src_path.with_name(src_path.stem + ENCRYPTED_SUFFIX)
    with open(src_path, "rb") as reader:
        _encrypt_stream(reader, out_path, password, None, progress, 0, total)
    return out_path


def encrypt_parts(
    src: str | Path,
    password: str,
    part_bytes: int,
    dest_dir: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """把明文切成若干份，每份单独加密成一个容器，返回容器列表。

    为什么要切：百度分片上传的 block_list 要塞进 URL query（超过约 8KB 就
    414），所以单片体积必须随文件变大而变大，而 30MB 以上的单片在校园网
    经常被重置。把每个容器控制在 `part_bytes` 以内，单片就一直是 4–8MB。
    """
    src_path = Path(src)
    if not src_path.is_file():
        raise CryptoError(f"源文件不存在: {src_path}")
    total = src_path.stat().st_size
    if total <= 0:
        raise CryptoError(f"源文件是空的: {src_path}")
    if part_bytes <= 0:
        part_bytes = total

    folder = Path(dest_dir) if dest_dir else src_path.parent
    parts: list[Path] = []
    with open(src_path, "rb") as reader:
        index = 0
        done = 0
        while done < total:
            index += 1
            if total <= part_bytes:
                out = src_path.with_name(src_path.stem + ENCRYPTED_SUFFIX)
            else:
                out = folder / f"{src_path.stem}.part{index}{ENCRYPTED_SUFFIX}"
            written = _encrypt_stream(reader, out, password, part_bytes, progress, done, total)
            if written <= 0:
                break
            done += written
            parts.append(out)
    return parts


def _encrypt_stream(
    reader,
    out_path: Path,
    password: str,
    limit: int | None,
    progress: Callable[[int, int], None] | None,
    base_processed: int,
    total: int,
) -> int:
    """从已打开的 reader 里读最多 limit 字节，写成一个完整容器；返回写进去的明文字节数。"""
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()

    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(password, salt, KDF_ITERATIONS)
    aead = AESGCM(key)

    processed = base_processed
    written = 0
    with open(out_path, "wb") as writer:
        writer.write(MAGIC)
        writer.write(bytes([VERSION]))
        writer.write(int(KDF_ITERATIONS).to_bytes(4, "big"))
        writer.write(salt)
        writer.write(nonce)
        block_index = 0
        while limit is None or written < limit:
            want = CHUNK if limit is None else min(CHUNK, limit - written)
            block = reader.read(want)
            if not block:
                break
            # 每块单独 encrypt 会产生多个 tag，所以这里用 GCM 的分段更新：
            # cryptography 没有公开流式 API，只对整段做 AEAD 内存也不够，
            # 因此改为「每块独立 nonce + tag」的容器格式。
            block_nonce = _block_nonce(nonce, block_index)
            sealed = aead.encrypt(block_nonce, block, None)
            writer.write(len(block).to_bytes(4, "big"))
            writer.write(block_nonce)
            writer.write(sealed)
            processed += len(block)
            written += len(block)
            block_index += 1
            if progress:
                progress(processed, total)
    if written <= 0:
        out_path.unlink(missing_ok=True)
    return written


def _block_nonce(base: bytes, index: int) -> bytes:
    """由基础 nonce 和块序号派生每块 nonce，保证唯一。"""
    idx = int(index).to_bytes(8, "big")
    return (base[:4] + idx)[:NONCE_LEN]


def decrypt_file(
    src: str | Path,
    password: str,
    dest: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """还原加密文件。密码错会抛 CryptoError（GCM 校验失败）。"""
    src_path = Path(src)
    if not src_path.is_file():
        raise CryptoError(f"加密文件不存在: {src_path}")

    with open(src_path, "rb") as handle:
        magic = handle.read(len(MAGIC))
        if magic != MAGIC:
            raise CryptoError(f"不是本程序加密的文件: {src_path}")
        version = handle.read(1)
        if not version or version[0] != VERSION:
            raise CryptoError(f"不支持的加密格式版本: {version!r}")
        iterations = int.from_bytes(handle.read(4), "big")
        salt = handle.read(SALT_LEN)
        nonce = handle.read(NONCE_LEN)
        if len(salt) != SALT_LEN or len(nonce) != NONCE_LEN:
            raise CryptoError("文件头被截断")

        key = _derive_key(password, salt, iterations)
        aead = AESGCM(key)
        total = src_path.stat().st_size - HEADER_LEN
        out_path = Path(dest) if dest else src_path.with_name(_strip_png(src_path.name))
        processed = 0
        error: Exception | None = None

        with open(out_path, "wb") as writer:
            while True:
                size_bytes = handle.read(4)
                if len(size_bytes) < 4:
                    break
                block_len = int.from_bytes(size_bytes, "big")
                block_nonce = handle.read(NONCE_LEN)
                sealed = handle.read(block_len + TAG_LEN)
                if len(block_nonce) != NONCE_LEN or len(sealed) != block_len + TAG_LEN:
                    error = CryptoError("加密文件被截断")
                    break
                try:
                    writer.write(aead.decrypt(block_nonce, sealed, None))
                except Exception as exc:  # noqa: BLE001 - InvalidTag 也是这个分支
                    error = exc
                    break
                processed += block_len
                if progress:
                    progress(min(processed, max(total, 0)), max(total, 0))

        # 异常路径必须在文件句柄关闭后再删，否则 Windows 会报 WinError 32
        if error is not None:
            out_path.unlink(missing_ok=True)
            if isinstance(error, CryptoError):
                raise error
            raise CryptoError("解密失败：密码错误或文件被改动") from error
        return out_path


def _strip_png(name: str) -> str:
    if name.endswith(ENCRYPTED_SUFFIX):
        return name[: -len(ENCRYPTED_SUFFIX)] + ".dec"
    return name + ".dec"
