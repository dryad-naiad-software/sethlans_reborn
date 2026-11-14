# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``package()``: scan, write, verify and publish an archive (Unreal
foundation FR-PK-1, FR-PK-4, FR-PK-5, FR-PK-11..14).

1. The project root is resolved once and used for both the output check
   and the scan. The output must lie outside it: lexically (``normcase`` +
   ``commonpath`` on resolved paths) and by identity (no ancestor of the
   output is the root by ``os.path.samestat``, which covers APFS case
   folding and SMB aliases). Without ``overwrite`` it must not exist.
2. A fresh ``scan()``; unsupported features fail closed by default, and an
   archive the worker could not accept (too many entries, too large a
   file list) is refused.
3. The archive is written to ``<out_path>.partial-<uuid>`` in the output
   directory and fsync'd. If a file compresses more than the worker's
   ratio limits allow, the archive is written once more with every file
   whose own ratio exceeds 100 stored uncompressed (deterministic, since
   the choice depends only on the first pass); ``progress`` then restarts
   from zero.
4. ``archive_sha256`` is computed by re-reading the finished partial, which
   is then renamed onto ``out_path``. Any error or cancellation deletes
   the partial.
"""
import hashlib
import os
import threading
import uuid
from pathlib import Path
from typing import FrozenSet, List, Optional

from . import rules, scanner, writer
from .errors import PackagerError, PackagingCancelled, UnsupportedProjectFeature
from .model import PackageResult, PackagerOptions, ScanResult

DEFAULT_OPTIONS = PackagerOptions()
_HASH_CHUNK = 8 * 1024 * 1024
_UNSUPPORTED_MESSAGES = {
    'source': "'Source/' packaging is not supported yet (parked decision)",
    'plugins': "'Plugins/' packaging is not supported yet (parked decision)",
    'cpp_modules': "C++ modules listed in the .uproject are not supported yet (parked decision)",
}


def _lexically_inside(root: str, out_path) -> bool:
    source = os.path.normcase(str(Path(root).resolve()))
    target = os.path.normcase(str(Path(out_path).resolve()))
    try:
        return os.path.commonpath([source, target]) == source
    except ValueError:  # different drives
        return False


def _ancestor_is_root(root: str, out_path) -> bool:
    """True when ``out_path`` or one of its ancestors is the root directory itself (by identity)."""
    root_st = os.stat(root)
    given = Path(os.path.abspath(out_path))
    candidates = [given, *given.parents]
    resolved = Path(out_path).resolve()
    candidates += [resolved, *resolved.parents]
    for candidate in candidates:
        try:
            st = os.stat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            continue
        if os.path.samestat(st, root_st):
            return True
    return False


def check_output_location(root, out_path) -> None:
    """Refuse an output inside the project directory ``root`` (FR-PK-5)."""
    if _lexically_inside(root, out_path) or _ancestor_is_root(root, out_path):
        raise PackagerError("the archive must be written outside the project directory")


def _manifest_warnings(result: ScanResult, options: PackagerOptions) -> List[str]:
    unsupported = result.unsupported
    if unsupported and options.on_unsupported == 'error':
        raise UnsupportedProjectFeature('; '.join(_UNSUPPORTED_MESSAGES[key] for key in unsupported))
    if result.archive_entry_count > rules.WORKER_MAX_ENTRIES:
        raise PackagerError(f"the archive would have {result.archive_entry_count} entries; "
                            f"workers accept at most {rules.WORKER_MAX_ENTRIES}")
    if scanner.estimated_cd_bytes(result) > rules.WORKER_MAX_CD_BYTES:
        raise PackagerError("the archive's file list would exceed the workers' 64 MiB limit")
    return list(result.warnings)


def _sha256_file(path: Path, cancel: Optional[threading.Event]) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(_HASH_CHUNK):
            if cancel is not None and cancel.is_set():
                raise PackagingCancelled()
            digest.update(block)
    return digest.hexdigest()


def _claim_and_replace(partial: Path, out_path: Path) -> None:
    """Claim ``out_path`` with ``O_EXCL`` (raises FileExistsError), then replace the placeholder."""
    fd = os.open(out_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_CLOEXEC', 0), 0o644)
    os.close(fd)
    try:
        os.replace(partial, out_path)
    except BaseException:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        raise


def _rename_no_clobber(partial: Path, out_path: Path) -> None:
    if os.name == 'nt':
        os.rename(partial, out_path)  # raises FileExistsError when out_path exists
        return
    try:
        os.link(partial, out_path)    # raises FileExistsError when out_path exists
    except FileExistsError:
        raise
    except OSError:                   # no hard links on this filesystem (e.g. some SMB mounts)
        _claim_and_replace(partial, out_path)
        return
    os.unlink(partial)


def _publish(partial: Path, out_path: Path, overwrite: bool) -> None:
    """Rename the finished partial onto ``out_path`` (no clobber unless ``overwrite``)."""
    if overwrite:
        os.replace(partial, out_path)
        return
    try:
        _rename_no_clobber(partial, out_path)
    except FileExistsError as exc:
        raise PackagerError(f"{str(out_path)!r} already exists (pass overwrite=True to replace it)") from exc


def _fsync_dir(directory: Path) -> None:
    if os.name == 'nt':
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _discard(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _write_pass(partial: Path, result: ScanResult, warnings: List[str], stored: FrozenSet[str],
                progress, cancel) -> writer.WriteResult:
    with open(partial, 'xb') as handle:
        written = writer.write_archive(handle, result, warnings, stored, progress, cancel)
        handle.flush()
        os.fsync(handle.fileno())
    return written


def package(project_dir, out_path, options: PackagerOptions = DEFAULT_OPTIONS, progress=None,
            cancel: Optional[threading.Event] = None) -> PackageResult:
    """Package ``project_dir`` into the zip ``out_path`` (FR-PK-1).

    Args:
        progress: Called as ``progress(bytes_done, bytes_total)`` after each
            chunk of source data.
        cancel: When set, packaging stops with ``PackagingCancelled``.

    Raises:
        PackagerError: see ``scan()``; also an output inside the project,
            an existing output without ``overwrite``, or an archive the
            worker could not accept.
        UnsupportedProjectFeature: ``Source/``, ``Plugins/`` or C++ modules
            with ``on_unsupported='error'``.
        SourceChangedError: a file changed or was swapped while packaged.
        PackagingCancelled: ``cancel`` was set.
    """
    out_path = Path(out_path)
    root = scanner.project_root(project_dir)
    check_output_location(root, out_path)
    if not out_path.parent.is_dir():
        raise PackagerError(f"the output directory {str(out_path.parent)!r} does not exist")
    if not options.overwrite and os.path.lexists(out_path):
        raise PackagerError(f"{str(out_path)!r} already exists (pass overwrite=True to replace it)")
    result = scanner.scan(root, options)
    warnings = _manifest_warnings(result, options)
    stored: FrozenSet[str] = frozenset()
    for attempt in (1, 2):
        partial = out_path.parent / f"{out_path.name}.partial-{uuid.uuid4().hex}"
        try:
            written = _write_pass(partial, result, warnings, stored, progress, cancel)
            if written.violates and attempt == 1:
                stored = written.too_compressible
                _discard(partial)
                continue
            if written.violates:
                raise PackagerError("the archive would exceed the workers' compression-ratio limits")
            sha = _sha256_file(partial, cancel)
            size = partial.stat().st_size
            _publish(partial, out_path, options.overwrite)
        except BaseException:
            _discard(partial)
            raise
        _fsync_dir(out_path.parent)
        return PackageResult(manifest=written.manifest, archive_sha256=sha, archive_size=size, out_path=out_path)
    raise AssertionError("unreachable")  # pragma: no cover
