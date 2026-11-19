# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Isolation checks for the real Unreal E2E (Unreal foundation FR-T-8).

A farm render must leave the logged-in user's Unreal and Epic state alone.
``take_snapshot`` records, before the first launch and after the last:

- every file under the user's Unreal and Epic folders (relpath, size and
  ``st_mtime_ns``; this covers the artist's ``Common\\Zen\\Data``);
- an export of ``HKCU\\Software\\Epic Games`` minus the single value
  ``Zen\\HostAuth``, which every farm zenserver launch deletes (FR-UE-27,
  the open item OQ-31);
- whether something listens on the artist's Zen port 8558;
- the size and ``st_mtime_ns`` of the archive on the share.

Everything here only reads: the share, the registry and the user's
folders are never written. The registry export goes to a file in the
test's own temp dir.
"""

import os
import subprocess
from pathlib import Path

import psutil

ARTIST_ZEN_PORT = 8558
EPIC_REGISTRY_KEY = r"HKCU\Software\Epic Games"
_ZEN_SECTION = r"[HKEY_CURRENT_USER\Software\Epic Games\Zen]"
_HOSTAUTH_PREFIX = '"HostAuth"='
# Images that mean an Unreal editor (the artist's or a leftover) is running.
_EDITOR_PREFIXES = ("unrealeditor", "shadercompileworker")
# FR-T-8 "No orphans": the farm processes that must not outlive a job.
_ORPHAN_IMAGES = ("unrealeditor-cmd.exe", "shadercompileworker.exe")


def _user_dirs():
    """The per-user Unreal and Epic folders FR-T-8 snapshots."""
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    home = os.environ.get("USERPROFILE", "")
    return {
        "LOCALAPPDATA/UnrealEngine": Path(local, "UnrealEngine"),
        "LOCALAPPDATA/Unreal Engine": Path(local, "Unreal Engine"),
        "APPDATA/Unreal Engine": Path(roaming, "Unreal Engine"),
        "Documents/Unreal Engine": Path(home, "Documents", "Unreal Engine"),
        "LOCALAPPDATA/Epic": Path(local, "Epic"),
        "LOCALAPPDATA/CrashReportClient": Path(local, "CrashReportClient"),
        "LOCALAPPDATA/UnrealBuildTool": Path(local, "UnrealBuildTool"),
    }


def _walk(root):
    """``{relpath: (size, mtime_ns)}`` of every file under ``root``."""
    entries = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                entries[os.path.relpath(full, root)] = ("unreadable",)
                continue
            entries[os.path.relpath(full, root)] = (st.st_size, st.st_mtime_ns)
    return entries


def _registry_lines(export_path):
    """The ``HKCU\\Software\\Epic Games`` export without ``Zen\\HostAuth``.

    Returns ``None`` when the key does not exist (``reg`` fails).
    """
    result = subprocess.run(
        ["reg", "export", EPIC_REGISTRY_KEY, str(export_path), "/y"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return None
    text = Path(export_path).read_bytes().decode("utf-16")
    lines, section = [], ""
    for line in text.splitlines():
        if line.startswith("["):
            section = line.strip()
        if section == _ZEN_SECTION and line.startswith(_HOSTAUTH_PREFIX):
            continue
        lines.append(line)
    return lines


def _is_listener(conn):
    # A bound socket without a peer. The dev machine's Windows build reports a
    # listener's TCP state wrongly (psutil: NONE), so the state is not used;
    # see ``unreal_network_watch``.
    return bool(conn.laddr) and not conn.raddr


def artist_zen_listening():
    """True when a process listens on the artist's Zen port."""
    try:
        connections = psutil.net_connections(kind="tcp")
    except psutil.Error:
        return False
    return any(_is_listener(c) and c.laddr.port == ARTIST_ZEN_PORT for c in connections)


def _stat_archive(archive):
    st = os.stat(archive)
    return st.st_size, st.st_mtime_ns


def take_snapshot(export_path, archive):
    """Snapshot the user's Unreal/Epic state, the registry and the archive."""
    return {
        "dirs": {
            label: _walk(path) if path.is_dir() else None
            for label, path in _user_dirs().items()
        },
        "registry": _registry_lines(export_path),
        "zen_8558": artist_zen_listening(),
        "archive": _stat_archive(archive),
    }


def _dir_changes(before, after):
    problems = []
    for label, old in before.items():
        old, new = old or {}, after.get(label) or {}
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
        for kind, paths in (("added", added), ("removed", removed), ("changed", changed)):
            if paths:
                problems.append(f"{label}: {len(paths)} {kind}, e.g. {paths[:5]}")
    return problems


def snapshot_differences(before, after):
    """Human-readable list of every isolation break between two snapshots."""
    problems = _dir_changes(before["dirs"], after["dirs"])
    if before["registry"] != after["registry"]:
        problems.append(
            f"{EPIC_REGISTRY_KEY} changed (outside the excluded Zen\\HostAuth value)"
        )
    if after["zen_8558"] and not before["zen_8558"]:
        problems.append(f"something now listens on the artist's Zen port {ARTIST_ZEN_PORT}")
    if before["archive"] != after["archive"]:
        problems.append(
            f"the share archive changed: {before['archive']} -> {after['archive']}"
        )
    return problems


def _process_rows(attrs):
    # ``process_iter`` fills an attribute it may not read with ``None``.
    for proc in psutil.process_iter(attrs):
        yield proc.info


def running_unreal_processes():
    """``name (pid)`` of every Unreal editor or shader worker running now."""
    return [
        f"{info['name']} ({info['pid']})"
        for info in _process_rows(["name", "pid"])
        if (info.get("name") or "").lower().startswith(_EDITOR_PREFIXES)
    ]


def orphans_of(cache_dir):
    """FR-T-8: Unreal processes whose command line or cwd names ``cache_dir``."""
    needle = os.path.normcase(str(cache_dir))
    found = []
    for info in _process_rows(["name", "pid", "cmdline", "cwd"]):
        if (info.get("name") or "").lower() not in _ORPHAN_IMAGES:
            continue
        text = " ".join(info.get("cmdline") or []) + " " + (info.get("cwd") or "")
        if needle in os.path.normcase(text):
            found.append(f"{info['name']} ({info['pid']})")
    return found
