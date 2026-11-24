# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Back-off after an FFmpeg checksum failure.

When a downloaded archive fails its SHA-256 check, downloading the same
pinned URL again on the next start almost always fails the same way and
costs about 150 MB each time.  After a ``checksum_mismatch`` the install
pipeline writes a marker file next to the install dir::

    <install_dir>.checksum-failed        e.g. bin/ffmpeg/8.1.checksum-failed

holding JSON::

    {
      "url": "<pinned download URL>",
      "expected_sha256": "<pinned digest>",
      "actual_sha256": "<digest of the downloaded file>" | null,
      "failed_at": "<ISO 8601 UTC time, e.g. 2026-11-23T10:15:00+00:00>"
    }

A later install attempt skips the download (and fails with
``checksum_mismatch`` again) only while the marker is valid: it parses,
its ``url`` and ``expected_sha256`` equal the current pins for this
platform, and ``failed_at`` is no more than ``BACKOFF`` in the past and
not in the future.  Anything else (no marker, unreadable, malformed,
other pins, too old, future time) means a normal download.

This is a bandwidth back-off, not a security gate, so it fails open:
any doubt about the marker leads to a normal attempt, and the checksum
check still guards whatever is downloaded.  Writing or removing the
marker never changes the install result; errors are only logged.

The marker is read only when ``lstat`` shows a regular file of at most
``MAX_MARKER_BYTES`` (a symlink, FIFO or oversized file is ignored), and
any read or parse error counts as no marker.  It is written to a temp
file in the same folder and moved into place with ``os.replace``, so the
write is atomic and a symlink at the marker path is replaced, not
written through.

The marker name does not match the stale-partial sweep patterns in
``cleanup.py`` (``*.tmp``, ``*.partial``, ``*.tmp.*``), so the sweep
keeps it, and it sits outside ``install_dir`` so the binary ``rglob``
in ``locate.py`` never walks it.

The clock is ``_utcnow`` (patchable) and every public function also
takes an optional ``now`` for tests.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MARKER_SUFFIX = ".checksum-failed"

BACKOFF = timedelta(hours=24)

# A real marker is about 400 bytes; anything larger is not ours.
MAX_MARKER_BYTES = 4096


def _utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def marker_path(install_dir: Path) -> Path:
    """Return the marker path for ``install_dir`` (a sibling file)."""
    return install_dir.with_name(install_dir.name + MARKER_SUFFIX)


def record_failure(
    install_dir: Path, url: str, expected_sha: str,
    actual_sha: Optional[str], now: Optional[datetime] = None,
) -> None:
    """Write the marker after a checksum failure.  Never raises."""
    when = now or _utcnow()
    payload = {
        "url": url,
        "expected_sha256": expected_sha.lower(),
        "actual_sha256": actual_sha.lower() if actual_sha else None,
        "failed_at": when.astimezone(timezone.utc).isoformat(),
    }
    path = marker_path(install_dir)
    try:
        _write_atomic(path, json.dumps(payload, indent=2))
    except (OSError, ValueError):
        logger.warning(
            "FFmpeg checksum back-off: could not write marker %s; the "
            "next start will download again.", path, exc_info=True,
        )
        return
    logger.info("FFmpeg checksum back-off: wrote marker %s", path)


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to a temp file beside ``path``, then ``os.replace``.

    ``os.replace`` swaps the directory entry, so a symlink planted at
    ``path`` is replaced rather than written through.  The temp name
    (unique, from ``mkstemp``) ends in ``.tmp``, so a leftover from a
    crash is removed by the stale-partial sweep.  The temp file is
    removed on any failure.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug(
                "FFmpeg checksum back-off: could not remove temp %s",
                tmp_name, exc_info=True,
            )
        raise


def clear(install_dir: Path) -> None:
    """Remove the marker if present.  Never raises."""
    path = marker_path(install_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "FFmpeg checksum back-off: could not remove marker %s",
            path, exc_info=True,
        )


def _is_small_regular_file(st: os.stat_result) -> bool:
    """True for a regular file no larger than ``MAX_MARKER_BYTES``."""
    return stat.S_ISREG(st.st_mode) and st.st_size <= MAX_MARKER_BYTES


def _read_bytes(path: Path) -> Optional[bytes]:
    """Read a small regular marker file; ``None`` if absent or unusable.

    ``lstat`` first, so a symlink, FIFO, device or directory is ignored
    (a FIFO would block the boot thread).  The open does not follow a
    symlink and does not block where the OS supports it, and ``fstat``
    re-checks the opened file in case the entry changed meanwhile.
    Reads at most ``MAX_MARKER_BYTES + 1`` bytes.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if not _is_small_regular_file(st):
        logger.info(
            "FFmpeg checksum back-off: ignoring marker %s (not a regular "
            "file of at most %d bytes)", path, MAX_MARKER_BYTES,
        )
        return None
    flags = (
        os.O_RDONLY | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    fd = os.open(str(path), flags)
    with os.fdopen(fd, "rb") as fh:
        if not _is_small_regular_file(os.fstat(fh.fileno())):
            return None
        raw = fh.read(MAX_MARKER_BYTES + 1)
    if len(raw) > MAX_MARKER_BYTES:
        return None
    return raw


def _read_marker(path: Path) -> Optional[dict]:
    """Return the marker's JSON object, or ``None`` if absent/unusable.

    Fails open: any error while reading or parsing (including
    ``RecursionError`` from deeply nested JSON) means "no marker".
    """
    try:
        raw = _read_bytes(path)
        if raw is None:
            return None
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        logger.info(
            "FFmpeg checksum back-off: ignoring unreadable or malformed "
            "marker %s", path, exc_info=True,
        )
        return None
    if not isinstance(data, dict):
        logger.info(
            "FFmpeg checksum back-off: ignoring malformed marker %s", path,
        )
        return None
    return data


def _parse_failed_at(value: object) -> Optional[datetime]:
    """Parse an aware ISO 8601 time; ``None`` if missing, naive or bad."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    if when.tzinfo is None or when.utcoffset() is None:
        return None
    return when


def _matches_pins(data: dict, url: str, expected_sha: str) -> bool:
    """True when the marker was written for exactly these pins."""
    marker_url = data.get("url")
    marker_sha = data.get("expected_sha256")
    if not isinstance(marker_url, str) or not isinstance(marker_sha, str):
        return False
    return (
        marker_url == url
        and marker_sha.lower() == expected_sha.lower()
    )


def next_attempt_time(
    install_dir: Path, url: str, expected_sha: str,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return when the next download may run, or ``None`` to run now.

    A non-``None`` result means a valid marker for these pins says the
    download failed its checksum less than ``BACKOFF`` ago.
    """
    path = marker_path(install_dir)
    data = _read_marker(path)
    if data is None:
        return None
    if not _matches_pins(data, url, expected_sha):
        logger.info(
            "FFmpeg checksum back-off: marker %s is for other pins; "
            "ignoring it.", path,
        )
        return None
    failed_at = _parse_failed_at(data.get("failed_at"))
    if failed_at is None:
        logger.info(
            "FFmpeg checksum back-off: marker %s has no valid "
            "failed_at; ignoring it.", path,
        )
        return None
    age = (now or _utcnow()) - failed_at
    if age < timedelta(0):
        logger.info(
            "FFmpeg checksum back-off: marker %s has a failed_at in the "
            "future; ignoring it.", path,
        )
        return None
    if age >= BACKOFF:
        return None
    return failed_at + BACKOFF


def should_skip_download(
    install_dir: Path, url: str, expected_sha: str,
    now: Optional[datetime] = None,
) -> bool:
    """True (and one warning logged) while the back-off is active.

    Fails open: an unexpected error while checking the marker means a
    normal download, never a failed install.
    """
    try:
        next_at = next_attempt_time(install_dir, url, expected_sha, now=now)
    except Exception:
        logger.info(
            "FFmpeg checksum back-off: marker check failed; downloading "
            "as normal.", exc_info=True,
        )
        return False
    if next_at is None:
        return False
    logger.warning(
        "FFmpeg download skipped: %s failed its checksum less than %d "
        "hours ago. The next attempt will be at the first start after "
        "%s. Delete %s to allow an attempt now.",
        url, int(BACKOFF.total_seconds() // 3600),
        next_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        marker_path(install_dir),
    )
    return True
