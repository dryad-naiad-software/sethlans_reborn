# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Project scan (Unreal foundation FR-PK-1..4, FR-PK-9, FR-PK-10, FR-PK-13).

``scan()`` finds the single root ``.uproject``, walks the allowlisted
subtrees without following links and records each file's ``lstat``
identity for the open-then-verify check of ``package()``. Every directory
is listed through ``source_io.opened_dir``, which verifies on the open
directory itself that it is the real one inside the project, so a
directory swapped for a junction or symlink after its ``lstat`` cannot
redirect the walk. The scan reads only the ``.uproject`` (to detect C++
modules), with the same containment check. Root directories are matched
case-insensitively (``content`` is ``Content``); the on-disk spelling is
kept in the archive.
"""
import json
import os
import stat
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple

from shared.unreal.unc import fold_component

from . import rules, source_io
from .errors import MAX_LISTED_PATHS, PackagerError
from .model import PackagerOptions, ScanResult, SourceFile

DEFAULT_OPTIONS = PackagerOptions()


def project_root(project_dir) -> str:
    """The resolved project directory, which must be a real directory."""
    root = str(Path(project_dir).resolve())
    try:
        st = os.stat(source_io.fs_path(root))
    except OSError as exc:
        raise PackagerError(f"cannot open the project directory {root!r}: {exc.strerror or exc}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise PackagerError(f"{root!r} is not a directory")
    return root


def _find_uproject(names: List[str]) -> str:
    found = [name for name in names if name.lower().endswith(rules.UPROJECT_SUFFIX)]
    if len(found) != 1:
        listing = ', '.join(found) if found else 'none'
        raise PackagerError(f"the project folder must contain exactly one .uproject file at its root "
                            f"(found {len(found)}: {listing})", found)
    return found[0]


def _read_uproject(root_fs: str, name: str, st: os.stat_result) -> bool:
    """Parse the ``.uproject`` (read only, contained); True when it lists C++ ``Modules``."""
    if not stat.S_ISREG(st.st_mode):
        raise PackagerError(f"{name!r} is not a regular file", (name,))
    if st.st_size > rules.UPROJECT_MAX_BYTES:
        raise PackagerError(f"{name!r} is larger than {rules.UPROJECT_MAX_BYTES} bytes", (name,))
    parts: List[bytes] = []
    source_io.stream_file(root_fs, name, source_io.identity(st), parts.append)
    try:
        doc = json.loads(b''.join(parts).decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackagerError(f"{name!r} is not valid JSON ({exc})", (name,)) from exc
    if not isinstance(doc, dict):
        raise PackagerError(f"{name!r} is not a JSON object", (name,))
    modules = doc.get('Modules')
    if modules is not None and not isinstance(modules, list):
        raise PackagerError(f"{name!r}: 'Modules' is not an array", (name,))
    return bool(modules)


def _has_content(root_fs: str, top: source_io.DirView, name: str) -> bool:
    """True when a ``Source``/``Plugins`` root entry is a non-empty directory, or anything else (fail closed)."""
    st = top.raw_lstat(name)
    if not stat.S_ISDIR(st.st_mode) or getattr(st, 'st_file_attributes', 0) & source_io.FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    try:
        with source_io.opened_dir(root_fs, name) as view:
            return bool(view.names)
    except PackagerError:
        return True


class _Walk:
    """Accumulates the files and directories of the included subtrees."""

    def __init__(self, root_fs: str, max_total_bytes: int):
        self.root_fs = root_fs
        self.files: List[SourceFile] = []
        self.dirs: List[str] = []
        self.total = 0
        self.max_total_bytes = max_total_bytes

    def add_file(self, relpath: str, st: os.stat_result) -> None:
        if len(self.files) >= rules.MAX_FILES:
            raise PackagerError(f"the project has more than {rules.MAX_FILES} files")
        self.total += st.st_size
        if self.total > self.max_total_bytes:
            raise PackagerError(f"the included files exceed {self.max_total_bytes} bytes")
        self.files.append(SourceFile(relpath=relpath, size=st.st_size, ino=st.st_ino, dev=st.st_dev,
                                     mtime_ns=st.st_mtime_ns))

    def subtree(self, top_rel: str) -> None:
        """Walk ``top_rel``; every directory is listed only through a verified view."""
        stack: List[Tuple[str, int]] = [(top_rel, 0)]
        while stack:
            rel, depth = stack.pop()
            if depth > rules.MAX_DEPTH:
                raise PackagerError(f"{rel!r} is nested deeper than {rules.MAX_DEPTH} directories", (rel,))
            self.dirs.append(rel)
            with source_io.opened_dir(self.root_fs, rel) as view:
                for name in view.names:
                    if rules.is_skipped_child(name):
                        continue
                    child_rel = f"{rel}/{name}"
                    st = view.lstat(name, child_rel)
                    if stat.S_ISDIR(st.st_mode):
                        stack.append((child_rel, depth + 1))
                    else:
                        self.add_file(child_rel, st)


def _classify(names: List[str], uproject: str, extras: Tuple[str, ...]):
    """Split the root names: included ``(disk name, usual name)`` pairs, excluded names, unsupported dirs."""
    wanted: Dict[str, str] = {fold_component(name): name for name in rules.INCLUDED_DIRS + extras}
    unsupported = {fold_component(name): name.lower() for name in rules.UNSUPPORTED_DIRS}
    included, excluded, present = [], [], {}
    for name in names:
        folded = fold_component(name)
        if name == uproject:
            continue
        if folded in wanted:
            included.append((name, wanted[folded]))
        else:
            excluded.append(name)
            if folded in unsupported:
                present.setdefault(unsupported[folded], []).append(name)
    return included, excluded, present


class _Root(NamedTuple):
    uproject: str
    uproject_st: os.stat_result
    included: List[Tuple[str, str]]
    excluded: List[str]
    unsupported_present: Dict[str, bool]


def _inspect_root(root_fs: str, extras: Tuple[str, ...]) -> _Root:
    """Everything the scan needs from the root listing, read through one verified view."""
    with source_io.opened_dir(root_fs, '') as top:
        uproject = _find_uproject(top.names)
        rules.validate_project_name(uproject)
        uproject_st = top.lstat(uproject, uproject)
        included, excluded, present = _classify(top.names, uproject, extras)
        for name, _usual in included:
            if not stat.S_ISDIR(top.lstat(name, name).st_mode):
                raise PackagerError(f"{name!r} at the project root is not a directory", (name,))
        found = {kind: any(_has_content(root_fs, top, name) for name in names) for kind, names in present.items()}
    return _Root(uproject, uproject_st, included, excluded, found)


def _walk_included(included, walk: _Walk) -> List[str]:
    """Walk each included root directory; return spelling and missing-directory warnings."""
    warnings = []
    for name, usual in included:
        if name != usual:
            warnings.append(f"{name}/ is spelled differently from the usual {usual}/")
        walk.subtree(name)
    found = {usual for _name, usual in included}
    warnings.extend(f"the project has no {name}/ directory" for name in rules.INCLUDED_DIRS if name not in found)
    return warnings


def _check_names(result_dirs: List[str], files: List[SourceFile], uproject: str) -> None:
    paths = [uproject] + result_dirs + [item.relpath for item in files]
    problems = rules.name_problems(paths) + rules.collisions(paths)
    if problems:
        shown = problems[:MAX_LISTED_PATHS]
        more = f" (and {len(problems) - len(shown)} more)" if len(problems) > len(shown) else ''
        raise PackagerError("these paths cannot be packaged for Windows workers: "
                            + '; '.join(f"{path}: {reason}" for path, reason in shown) + more,
                            [path for path, _reason in shown])


def _feature_warnings(features: Dict[str, bool], uproject: str) -> List[str]:
    notes = {'source': "Source/ is present and is not packaged (C++ support is parked, OQ-2)",
             'plugins': "Plugins/ is present and is not packaged (project plugins are parked, OQ-2)",
             'cpp_modules': f"{uproject} lists C++ Modules, which are not packaged (parked, OQ-2)"}
    return [notes[key] for key in ('source', 'plugins', 'cpp_modules') if features[key]]


def _limit_warnings(result: ScanResult) -> List[str]:
    warnings = []
    if result.archive_entry_count > rules.WORKER_MAX_ENTRIES:
        warnings.append(f"the archive would have {result.archive_entry_count} entries; workers accept at most "
                        f"{rules.WORKER_MAX_ENTRIES}, so package() will refuse it")
    if estimated_cd_bytes(result) > rules.WORKER_MAX_CD_BYTES:
        warnings.append("the archive's file list would exceed the workers' 64 MiB limit; package() will refuse it")
    return warnings


def estimated_cd_bytes(result: ScanResult) -> int:
    """An upper bound of the archive's central-directory size."""
    names = [result.project_name + '/', rules.MANIFEST_NAME]
    names += [f"{result.project_name}/{rel}/" for rel in result.dirs]
    names += [f"{result.project_name}/{rel}" for rel, _size in result.files]
    return sum(rules.CD_HEADER_MAX_OVERHEAD + len(name.encode('utf-8', 'replace')) for name in names)


def scan(project_dir, options: PackagerOptions = DEFAULT_OPTIONS) -> ScanResult:
    """Scan ``project_dir`` and describe what ``package()`` would include (FR-PK-1).

    Raises:
        PackagerError: no or several root ``.uproject`` files, a bad
            project name, a link or placeholder in an included subtree, a
            path a Windows worker would reject, or a limit exceeded.
        SourceChangedError: a directory or the ``.uproject`` was swapped
            or changed during the scan.
        UnsupportedProjectFeature: ``options.extra_top_level`` names a
            directory that is not in ``EXTENSION_DIRS``.
    """
    extras = rules.accepted_extensions(options.extra_top_level)
    root_fs = source_io.fs_path(project_root(project_dir))
    top = _inspect_root(root_fs, extras)
    project_name = rules.validate_project_name(top.uproject)
    cpp_modules = _read_uproject(root_fs, top.uproject, top.uproject_st)
    walk = _Walk(root_fs, options.max_total_bytes)
    walk.add_file(top.uproject, top.uproject_st)
    warnings = _walk_included(top.included, walk)
    _check_names(walk.dirs, walk.files[1:], top.uproject)
    features = {'source': top.unsupported_present.get('source', False),
                'plugins': top.unsupported_present.get('plugins', False), 'cpp_modules': cpp_modules}
    ordered = sorted(walk.files, key=lambda item: item.relpath)
    result = ScanResult(
        project_name=project_name, files=[(item.relpath, item.size) for item in ordered],
        total_bytes=walk.total, excluded_top_level=sorted(top.excluded), detected_features=features,
        warnings=warnings + _feature_warnings(features, top.uproject), dirs=sorted(walk.dirs),
        sources=tuple(ordered), root=root_fs,
    )
    result.warnings.extend(_limit_warnings(result))
    return result
