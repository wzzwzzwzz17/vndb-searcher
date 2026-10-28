# -*- coding: utf-8 -*-
"""C 路线的本地加密（AES-256-GCM）。"""

from .enc import CryptoError, decrypt_file, encrypt_file, is_encrypted

__all__ = ["CryptoError", "decrypt_file", "encrypt_file", "is_encrypted"]
