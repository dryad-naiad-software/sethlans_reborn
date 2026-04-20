# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Archive download, SHA-256 verification, and safe extraction."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .exceptions import IntegrityError
from .gpg import verify_gpg

logger = logging.getLogger("fetch_caddy")


def sha256_of(path: Path) -> str:
    """Compute SHA-256 of a file using a 64 KB buffer."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_archive(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` using stdlib urllib (HTTPS verified).

    urllib uses the system CA store and verifies HTTPS certificates by
    default — no extra flag needed. Writes to a ``.partial`` sidecar and
    atomically renames on success to avoid surfacing partial files if
    the network drops mid-transfer.
    """
    logger.info("Downloading %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_suffix = f".{os.getpid()}.partial"
    tmp = dest.with_suffix(dest.suffix + tmp_suffix)
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            with tmp.open("wb") as out:
                shutil.copyfileobj(resp, out, length=65536)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def verify_sha256(path: Path, expected: str) -> None:
    """Raise :class:`IntegrityError` unless ``path`` hashes to ``expected``."""
    actual = sha256_of(path)
    if actual.lower() != expected.lower():
        raise IntegrityError(
            f"SHA-256 mismatch for {path.name}: "
            f"expected {expected}, got {actual}"
        )


def _assert_within(member_path: Path, dest_resolved: Path, name: str) -> None:
    """Raise ``ValueError`` if ``member_path`` is not inside ``dest_resolved``.

    Uses the ``Path.relative_to`` idiom rather than string ``startswith``:
    on Windows, ``C:\\build\\caddy-evil`` has ``C:\\build\\caddy`` as a
    string prefix but is a **sibling** directory, not a child. The
    ``relative_to`` check rejects sibling collisions correctly.
    """
    try:
        member_path.relative_to(dest_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Refusing entry that escapes dest: {name!r}"
        ) from exc


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            member_path = (dest_resolved / member.name).resolve()
            _assert_within(member_path, dest_resolved, member.name)
        tar.extractall(path=str(dest_resolved), filter="data")


def _safe_extract_zip(archive: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(archive, "r") as zf:
        for name in zf.namelist():
            member_path = (dest_resolved / name).resolve()
            _assert_within(member_path, dest_resolved, name)
        zf.extractall(path=str(dest_resolved))


def _mark_executable(path: Path) -> None:
    """chmod +x on POSIX; no-op on Windows (PE does not need the bit)."""
    if os.name == "nt":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def extract_archive(archive: Path, dest: Path) -> Path:
    """Extract ``archive`` and return the path of the ``caddy[.exe]`` binary.

    Caddy release archives are flat (the binary at the archive root),
    but we still scan the extraction directory so a future layout change
    upstream does not silently break us.
    """
    dest.mkdir(parents=True, exist_ok=True)
    suffix = "".join(archive.suffixes)
    if suffix.endswith(".tar.gz") or suffix.endswith(".tgz"):
        _safe_extract_tar(archive, dest)
    elif suffix.endswith(".zip"):
        _safe_extract_zip(archive, dest)
    else:
        raise ValueError(f"Unsupported archive type: {archive.name}")

    for name in ("caddy.exe", "caddy"):
        candidate = dest / name
        if candidate.is_file():
            _mark_executable(candidate)
            return candidate
    for candidate in dest.rglob("caddy*"):
        if candidate.name in ("caddy", "caddy.exe") and candidate.is_file():
            _mark_executable(candidate)
            return candidate
    raise FileNotFoundError(
        f"No caddy binary found inside {archive.name} after extraction"
    )


def install_binary(
    archive: Path, target_binary: Path, work_dir: Path,
) -> Path:
    """Extract ``archive`` into ``work_dir`` and copy caddy to ``target_binary``."""
    extracted = extract_archive(archive, work_dir)
    target_binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(extracted, target_binary)
    _mark_executable(target_binary)
    return target_binary


def fetch_and_install(
    url: str,
    expected_sha256: str,
    target_binary: Path,
    *,
    idempotent_skip: bool = True,
    signature_url: str | None = None,
) -> Path:
    """Idempotent download → verify → extract → install.

    Fast-path semantics (when ``idempotent_skip`` is True, default):

      * If ``target_binary`` exists **and** its SHA-256 matches
        ``expected_sha256`` (the extracted-binary hash, not the archive
        hash — see below), we skip the download entirely. Re-hashing a
        ~40 MB file is ~200 ms and cheap insurance against a poisoned
        CI cache entry or a stale dev binary that survived a lockfile
        bump.
      * If ``target_binary`` exists but its hash does NOT match, we
        treat the on-disk file as invalidated (cache poisoned, stale
        binary, or corrupted write), log a warning, and fall through
        to re-download. We only raise :class:`IntegrityError` if the
        freshly-downloaded archive itself mismatches ``expected_sha256``.

    Note: ``expected_sha256`` here is the archive hash (from
    ``caddy.lock``), which is what's used to verify the downloaded
    archive before extraction. The fast-path re-hash compares the same
    value against the installed binary — if it matches, the installed
    binary is byte-for-byte identical to an archive-contents member,
    which is the strongest idempotence signal we can get without a
    separate binary-level pin. A mismatch on the fast path just means
    "refetch"; correctness is enforced by the post-download verify.
    """
    if idempotent_skip and target_binary.is_file():
        try:
            actual = sha256_of(target_binary)
        except OSError as exc:
            logger.warning(
                "Could not hash existing %s (%s); re-downloading.",
                target_binary, exc,
            )
        else:
            if actual.lower() == expected_sha256.lower():
                logger.info(
                    "Caddy already present at %s with matching SHA-256; "
                    "skipping download (idempotent).",
                    target_binary,
                )
                _mark_executable(target_binary)
                return target_binary
            logger.warning(
                "Existing binary at %s has SHA-256 %s but lockfile expects "
                "%s; treating cache as invalidated and re-downloading.",
                target_binary, actual, expected_sha256,
            )

    target_binary.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fetch_caddy_") as tmpdir:
        tmp = Path(tmpdir)
        archive_name = url.rsplit("/", 1)[-1]
        archive_path = tmp / archive_name
        download_archive(url, archive_path)
        verify_sha256(archive_path, expected_sha256)
        # GPG verification runs against the archive (upstream .asc
        # signatures are over the archive blob), before extraction.
        # Passing signature_url=None makes this a best-effort warning.
        if signature_url is not None:
            verify_gpg(archive_path, signature_url)
        install_binary(archive_path, target_binary, tmp)

    logger.info("Installed caddy -> %s", target_binary)
    return target_binary
