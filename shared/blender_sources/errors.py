# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Errors of the Blender sources package and the short reason text of a failed request.

Every error derives from :class:`BlenderSourcesError` (BS-LOG-5). A failed
source is described by one short reason (BS-LOG-1): ``connection refused``,
``DNS failure``, ``TLS error``, ``HTTP 403``, ``read timeout 120 s`` and so
on. Reasons never hold headers, tokens or response bodies (BS-LOG-4).
"""
import socket
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

Failure = Tuple[str, str]  # (source URL, reason)


class BlenderSourcesError(Exception):
    """Base of every error raised by ``shared.blender_sources``."""


class BlenderSourcesConfigError(BlenderSourcesError):
    """The configured source list is unreadable or holds an invalid entry (BS-CFG-2)."""


class DownloadStoppedError(BlenderSourcesError):
    """The caller's stop event was set during a download (BS-DL-10)."""


class SourceFailure(BlenderSourcesError):
    """One request to one source failed; ``reason`` is the BS-LOG-1 text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def host_of(url: str) -> str:
    """The host (and port, if any) of ``url``, for logs and error text."""
    return urlsplit(url).netloc or url


def _failures_text(failures: Iterable[Failure]) -> str:
    return "; ".join(f"{host_of(source)}: {reason}" for source, reason in failures)


class AllSourcesFailedError(BlenderSourcesError):
    """Every source failed one lookup or download (BS-LOG-2).

    ``failures`` is ``[(source, reason), ...]`` in the order the sources were
    asked; the message is one line naming each host and its reason.
    """

    def __init__(self, subject: str, failures: List[Failure]):
        self.subject = subject
        self.failures = list(failures)
        super().__init__(f"{subject}: every source failed: {_failures_text(self.failures)}")


class BuildNotPublishedError(BlenderSourcesError):
    """A source answered the series page fully, and none lists the build (BS-REL-3)."""

    def __init__(self, version: str, platform_id: str, failures: List[Failure]):
        self.version = version
        self.platform_id = platform_id
        self.failures = list(failures)
        super().__init__(f"Blender {version} has no {platform_id} build ({_failures_text(self.failures)})")


def _causes(exc: BaseException) -> Iterable[object]:
    """``exc`` and what it wraps: ``args[0]``, ``.reason``, ``__cause__``, ``__context__``."""
    seen, todo = set(), [exc]
    while todo and len(seen) < 16:
        item = todo.pop(0)
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        args = getattr(item, "args", ())
        nested = [args[0] if args else None, getattr(item, "reason", None),
                  getattr(item, "__cause__", None), getattr(item, "__context__", None)]
        todo.extend(n for n in nested if isinstance(n, BaseException))


def _connection_reason(exc: BaseException) -> str:
    for item in _causes(exc):
        if isinstance(item, socket.gaierror):
            return "DNS failure"
        if isinstance(item, ConnectionRefusedError):
            return "connection refused"
        if isinstance(item, ConnectionResetError):
            return "connection reset"
    return "connection error"


def _seconds(timeout, index: int) -> Optional[float]:
    if isinstance(timeout, tuple):
        return timeout[index]
    return timeout


def request_failure_reason(exc: requests.exceptions.RequestException, timeout=None) -> str:
    """The BS-LOG-1 reason for a ``requests`` error.

    ``ConnectTimeout`` and ``SSLError`` are subclasses of ``ConnectionError``,
    so they are checked first.
    """
    err = requests.exceptions
    if isinstance(exc, err.ConnectTimeout):
        return f"connect timeout {_seconds(timeout, 0)} s"
    if isinstance(exc, err.ReadTimeout):
        return f"read timeout {_seconds(timeout, 1)} s"
    if isinstance(exc, err.SSLError):
        return "TLS error"
    if isinstance(exc, err.ConnectionError):
        return _connection_reason(exc)
    return f"request failed ({type(exc).__name__})"
