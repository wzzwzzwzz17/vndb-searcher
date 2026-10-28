# -*- coding: utf-8 -*-
"""日志：控制台 + 按天滚动的文件，统一前缀便于 grep。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "galsave"


def setup_logging(log_dir: str | Path, level: str | int = "INFO") -> logging.Logger:
    """初始化根日志。重复调用不会叠加 handler。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level if isinstance(level, int) else getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        logger.addHandler(console)

        file_handler = RotatingFileHandler(
            log_dir / "galsave.log",
            maxBytes=4 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
