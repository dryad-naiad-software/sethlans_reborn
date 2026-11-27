# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Standard streams and logging of the one-shot setup commands (setup-rebuild FR-WK-1).

- ``binary_streams()``: the process's stdin and stdout as byte streams. A
  windowed frozen ``run_worker.exe`` may start with ``sys.stdin`` or
  ``sys.stdout`` set to ``None``; the file descriptors 0 and 1 are then tried
  directly (they hold the pipes a parent passed in).
- ``read_request(stream)``: exactly one JSON object of at most 64 KiB.
- ``write_answer(stream, answer)``: one JSON object and a newline.
- ``log_to_worker_log()``: the root logger writes only to the worker log
  (``<data folder>/logs/worker.log``), never to stdout or stderr.

``CliError`` carries the ``error`` code and the plain ``message`` of an exit-1 answer.
"""
import json
import logging
import os
import sys
from typing import Optional, Tuple

MAX_REQUEST_BYTES = 64 * 1024
LOG_FORMAT = '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'


class CliError(Exception):
    """An expected failure: exit 1 with ``{"error": code, "message": message}``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _stream(std, fd: int, mode: str):
    if std is not None and getattr(std, 'buffer', None) is not None:
        return std.buffer
    try:
        return os.fdopen(fd, mode, closefd=False)
    except (OSError, ValueError):
        return None


def binary_streams() -> Tuple[Optional[object], Optional[object]]:
    """``(stdin, stdout)`` as byte streams; either is ``None`` when the process has none."""
    return _stream(sys.stdin, 0, 'rb'), _stream(sys.stdout, 1, 'wb')


def read_request(stream) -> dict:
    """Read one JSON object (at most ``MAX_REQUEST_BYTES``); ``CliError('bad_input')`` otherwise."""
    if stream is None:
        raise CliError('bad_input', 'No input: the command reads one JSON object on stdin.')
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise CliError('bad_input', f'The input is larger than {MAX_REQUEST_BYTES} bytes.')
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise CliError('bad_input', 'The input must be one JSON object.')
    return data


def write_answer(stream, answer: dict) -> None:
    stream.write((json.dumps(answer, ensure_ascii=True, sort_keys=True) + '\n').encode('ascii'))
    stream.flush()


def log_to_worker_log() -> None:
    """Send every log record to the worker log only; without a writable log, drop them."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.INFO)
    try:
        from sethlans_worker_agent import config_store
        log_dir = config_store.get_data_dir() / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / 'worker.log', encoding='utf-8')
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt='%Y-%m-%d %H:%M:%S'))
    root.addHandler(handler)
    logging.captureWarnings(True)  # urllib3's InsecureRequestWarning goes to the log, not stderr


def check_fields(request: dict, allowed) -> None:
    """``CliError('bad_input')`` when ``request`` names a field the command does not take."""
    unknown = sorted(set(request) - set(allowed))
    if unknown:
        raise CliError('bad_input', f"Unknown field(s): {', '.join(unknown)}.")
