# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker agent logging setup.

Extracted from ``agent.py`` to keep that module under the 300-line
ceiling. Invoked once from ``agent.main`` at startup; writes to
``config.WORKER_LOG_DIR / 'worker.log'`` via a rotating file handler
plus stdout.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from sethlans_worker_agent import config


def configure_logging(level_name: str) -> None:
    """Configure root logging: console + 5 MB rotating file."""
    config.WORKER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file_path = config.WORKER_LOG_DIR / 'worker.log'

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level_name))

    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
