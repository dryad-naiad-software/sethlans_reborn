# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Downloading one Blender build with fallback over the sources (BS-DL, BS-SUM, #320).

Order: the source of the last verified download in this process (when still
listed), then the rest in list order, each at most once per call (BS-DL-1,
BS-DL-2). The next source is tried after any source-side failure: connection
refused or reset, DNS, TLS, any status other than 200, a timeout, the 30 min
budget, a non-HTTPS redirect hop, too many bytes or a checksum mismatch
(BS-DL-3). A local write error ends the call at once: it is not the source's
fault.

The expected SHA-256 is the entry's, from the release list, whichever source
serves the file (BS-SUM-1, D4). The archive is written to
``<dest_dir>/<file>.part``, hashed while written, and renamed to
``<dest_dir>/<file>`` only after the checksum matches; every failure deletes
the ``.part`` (BS-DL-5). Callers make sure no two calls write the same
``.part`` at once (the worker's per-version install lock, the manager's
single runner thread). No ``Range`` requests and no resume (BS-DL-8).
"""
import hashlib
import logging
import os
import time
from pathlib import Path

import requests

from shared.blender_sources import sources
from shared.blender_sources.errors import (
    AllSourcesFailedError, BlenderSourcesError, DownloadStoppedError, SourceFailure, host_of,
    request_failure_reason,
)
from shared.blender_sources.listing import is_sha256
from shared.blender_sources.platforms import build_file_name, series_of
from shared.blender_sources.release_cache import BuildEntry

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = (10, 120)  # (connect, read) seconds per attempt (#115)
STALL_BUDGET_SECONDS = 30 * 60  # wall-clock ceiling per attempt (#115)
MAX_BYTES = 2 * 1024 ** 3
CHUNK_BYTES = 1024 * 1024


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove the partial download %s: %s", path, exc)


def _check_entry(entry: BuildEntry) -> None:
    """Refuse an entry without a checksum or with a name not built from its version (BS-SUM-3, BS-SRC-5)."""
    try:
        expected_name = build_file_name(entry.version, entry.platform)
    except ValueError as exc:
        raise BlenderSourcesError(f"refusing the download: {exc}") from None
    if entry.file != expected_name:
        raise BlenderSourcesError(f"refusing the download: {entry.file!r} is not {expected_name}")
    if not is_sha256(entry.sha256):
        raise BlenderSourcesError(f"no SHA-256 published for {entry.file}")


def _write_body(response, part: Path, sha256: str, max_bytes: int, stop) -> None:
    """Stream the body into ``part``; raise ``SourceFailure`` for source faults."""
    declared = response.headers.get("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise SourceFailure("too large")
    deadline = time.monotonic() + STALL_BUDGET_SECONDS
    hasher, written = hashlib.sha256(), 0
    with open(part, "wb") as handle:
        chunks = response.iter_content(chunk_size=CHUNK_BYTES)
        while True:
            if stop is not None and stop.is_set():
                raise DownloadStoppedError("download stopped")
            try:
                chunk = next(chunks, None)
            except requests.exceptions.RequestException as exc:
                raise SourceFailure(request_failure_reason(exc, DOWNLOAD_TIMEOUT)) from None
            if chunk is None:
                break
            if time.monotonic() > deadline:
                raise SourceFailure(f"stall budget {STALL_BUDGET_SECONDS} s")
            written += len(chunk)
            if written > max_bytes:
                raise SourceFailure("too large")
            handle.write(chunk)  # an OSError here is local: it ends the call
            hasher.update(chunk)
    if hasher.hexdigest() != sha256.lower():
        raise SourceFailure("checksum mismatch")


def _attempt(session, source: str, entry: BuildEntry, part: Path, max_bytes: int, stop) -> None:
    url = f"{source}Blender{series_of(entry.version)}/{entry.file}"
    response = sources.get_checked(session, url, DOWNLOAD_TIMEOUT)
    try:
        _write_body(response, part, entry.sha256, max_bytes, stop)
    finally:
        response.close()


def download_build(entry: BuildEntry, dest_dir, session=None, stop=None, max_bytes: int = MAX_BYTES) -> Path:
    """Download and verify ``entry`` into ``dest_dir``; return the archive's path.

    Raises ``BlenderSourcesError`` (bad entry, ``verify=False`` session, bad
    source setting), ``DownloadStoppedError`` when ``stop`` is set,
    ``AllSourcesFailedError`` when every source failed, and ``OSError`` for a
    local write error.
    """
    _check_entry(entry)
    sources.check_session(session)
    order = sources.download_order(sources.configured_sources())
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final, part = dest_dir / entry.file, dest_dir / f"{entry.file}.part"
    _remove(part)  # a leftover of an earlier, interrupted call
    subject = f"Blender {entry.version} {entry.platform}"
    own = session is None
    session = requests.Session() if own else session
    failures = []
    try:
        for source in order:
            if stop is not None and stop.is_set():
                raise DownloadStoppedError("download stopped")
            try:
                _attempt(session, source, entry, part, max_bytes, stop)
                os.replace(part, final)
            except SourceFailure as exc:
                _remove(part)
                logger.warning("%s: build from %s failed: %s", subject, host_of(source), exc.reason)
                failures.append((source, exc.reason))
                continue
            except BaseException:
                _remove(part)
                raise
            sources.set_last_worked(source)
            logger.info("%s: downloaded from %s; checksum from %s.", subject, host_of(source),
                        host_of(entry.source) if entry.source else "the release cache")
            return final
    finally:
        if own:
            session.close()
    raise AllSourcesFailedError(subject, failures)
