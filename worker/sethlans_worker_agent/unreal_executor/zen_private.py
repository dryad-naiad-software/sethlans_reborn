# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Farm-private Zen/DDC on its own port (Unreal foundation FR-UE-26, D11).

Every farm UE launch passes ``zen_flags(CACHE_DIR, ZEN_PORT)``::

    -ZenDataPath=<CACHE_DIR>\\zen\\data
    -LocalDataCachePath=<CACHE_DIR>\\zen\\fs
    -SharedDataCachePath=None
    -ini:Engine:[Zen.AutoLaunch]:DesiredPort=<port>,[Zen.AutoLaunch]:RemoteNetworkService=None

``-ZenDataPath=`` outranks every env, registry and ini data path
(``ZenServerInterface.cpp DetermineDataPath``, S2).

**Shared DDC (review D3), checked against the 5.8.4 source.** The shared
file-system node is ``Shared=(Type=FileSystem, ..., EnvPathOverride=UE-SharedDataCachePath,
EditorOverrideSetting=SharedDerivedDataCache, CommandLineOverride=SharedDataCachePath)``
(``BaseEngine.ini:2868``). ``FFileSystemCacheStoreParams::Parse``
(``FileSystemCacheStore.cpp:3005-3057``) applies, in order: ``Path=``, the
environment variable, the **HKCU** value ``Epic Games\\GlobalDataCachePath\\
UE-SharedDataCachePath`` (``:3029``; the artist's Editor Preferences write
it and FR-UE-27 does not isolate HKCU), the command line (``:3036-3041``)
and the editor setting (``:3043-3057``). So ``-SharedDataCachePath=None``
outranks HKCU, and ``None`` disables the store (``:1037``). The editor
setting outranks the command line, which is why ``zen_guard`` refuses a
non-empty ``SharedDerivedDataCache`` / ``LocalDerivedDataCache`` ``Path`` in
project and farm-profile config. The Zen and Cloud shared hosts
(``BaseEngine.ini:2741, 2849``) default to ``Host=None`` and read no
registry value; their env overrides (``UE-ZenSharedDataCacheHost``,
``UE-CloudDataCacheHost``) are removed by the FR-UE-18 ``^UE[-_]`` scrub, so
no ``=None`` host switch is needed (``-ZenSharedDataCacheHost=``/
``-CloudDataCacheHost=`` do exist as command-line host overrides).

**Loopback setting (chunk 7 integration requirement 13), checked against
the UE 5.8.4 source on theta:** the spec's ``AllowRemoteNetworkService=false``
does not exist in 5.8. ``[Zen.AutoLaunch]`` is read by
``ZenServerInterface.cpp:1562-1566`` as ``RemoteNetworkService`` (a string:
``None``, ``Unsecured`` or ``GeneratedStaticKey``, parsed at ``:1299-1318``),
and ``DetermineCmdLineWithoutTransientComponents`` (``:2085-2090``) adds
``--http-forceloopback`` to zenserver's command line only for ``None``.
``BaseEngine.ini:2725`` sets ``RemoteNetworkService=GeneratedStaticKey``,
so without the override the farm zenserver would accept remote clients.
The comma form is supported: ``FConfigFile::OverrideFromCommandline``
(``ConfigCacheIni.cpp:2317-2408``) splits a ``-ini:<Name>:`` token on
``,`` (``PropertySeperator``, ``:1721``) and applies every pair; it also
applies **every** ``-ini:Engine:`` token, not only the first. The
per-phase listener check (``zen_port.check_loopback``) stays in any case.

``is_farm_zenserver(proc, cache_dir)`` (FR-UE-23, Sec R3-W1) is True only
when the image name is ``zenserver.exe``, the owner SID equals the
worker's and the parsed ``--data-dir`` **equals** ``CACHE_DIR\\zen\\data``
(both forms, quotes stripped, ``GetLongPathNameW``, ``normcase``). Anything
unreadable is not a farm zenserver.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

import psutil

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import win32_proc, winpath
from shared import owner_only_acl

logger = logging.getLogger(__name__)

ZEN_DIR = 'zen'
ZEN_DATA = 'data'
ZEN_FS = 'fs'
UE_DEFAULT_ZEN_PORT = 8558
ZEN_IMAGE_WINDOWS = 'zenserver.exe'
ZEN_IMAGE_POSIX = 'zenserver'
ZEN_LOOPBACK_SETTING = '[Zen.AutoLaunch]:RemoteNetworkService=None'
# Review D3: disables the file-system shared DDC (FileSystemCacheStore.cpp:1037 "None").
SHARED_DDC_DISABLED = '-SharedDataCachePath=None'
DATA_DIR_OPTION = '--data-dir'
# Where zenserver runs from: the engine's binaries or the per-user Zen
# install (S2), which FR-UE-27 moves into the farm profile.
ENGINE_ZEN_RELPATH = ('Engine', 'Binaries', 'Win64', ZEN_IMAGE_WINDOWS)
PROFILE_ZEN_RELPATH = ('Profile', 'AppData', 'Local', 'UnrealEngine', 'Common', 'Zen', 'Install',
                       ZEN_IMAGE_WINDOWS)


def zen_data_dir(cache_dir) -> str:
    return winpath.join(cache_dir, ZEN_DIR, ZEN_DATA)


def zen_fs_dir(cache_dir) -> str:
    return winpath.join(cache_dir, ZEN_DIR, ZEN_FS)


def zen_ini_token(port: int) -> str:
    """The single ``-ini:Engine:`` token every launch carries (FR-UE-6)."""
    return f"-ini:Engine:[Zen.AutoLaunch]:DesiredPort={int(port)},{ZEN_LOOPBACK_SETTING}"


def zen_flags(cache_dir, port: int) -> Tuple[str, str, str, str]:
    """``ZEN_FLAGS`` for ``cache_dir`` and ``port`` (FR-UE-6, FR-UE-26, review D3)."""
    if int(port) == UE_DEFAULT_ZEN_PORT:
        raise ValueError(f"the farm Zen port must not be {UE_DEFAULT_ZEN_PORT}")
    return (f"-ZenDataPath={zen_data_dir(cache_dir)}",
            f"-LocalDataCachePath={zen_fs_dir(cache_dir)}",
            SHARED_DDC_DISABLED,
            zen_ini_token(port))


def ensure_zen_dirs(cache_dir) -> None:
    """Create ``zen/``, ``zen/data`` and ``zen/fs`` owner-only, or check them (no links)."""
    for path in (winpath.join(cache_dir, ZEN_DIR), zen_data_dir(cache_dir), zen_fs_dir(cache_dir)):
        fs_safe.ensure_owned_dir(Path(path))


def _strip_quotes(text: str) -> str:
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        text = text[1:-1].strip()
    return text


def normalize_path(path: str) -> str:
    """Quotes stripped, ``GetLongPathNameW``-expanded, ``normcase``-d, no trailing separator."""
    text = _strip_quotes(str(path))
    if not text:
        return ''
    text = winpath.normpath(text)
    text = win32_proc.long_path_name(text)
    text = winpath.normcase(winpath.normpath(text))
    stripped = text.rstrip('\\/')
    return stripped if stripped and not stripped.endswith(':') else text


def parse_data_dir(cmdline: Sequence[str]) -> Optional[str]:
    """The ``--data-dir`` value (``--data-dir <p>`` or ``--data-dir=<p>``), quotes stripped."""
    args = list(cmdline)
    for index, arg in enumerate(args):
        if arg == DATA_DIR_OPTION:
            return _strip_quotes(args[index + 1]) if index + 1 < len(args) else None
        if arg.startswith(DATA_DIR_OPTION + '='):
            return _strip_quotes(arg[len(DATA_DIR_OPTION) + 1:])
    return None


def _image_ok(proc) -> bool:
    expected = ZEN_IMAGE_WINDOWS if sys.platform == 'win32' else ZEN_IMAGE_POSIX
    return proc.name().lower() == expected


def owned_by_worker(proc, worker_sid: Optional[str] = None) -> bool:
    """True when ``proc``'s owner is the worker's account; raises ``OSError``/``psutil.Error`` if unreadable."""
    if sys.platform == 'win32':
        sid = worker_sid or owner_only_acl.current_user_sid()
        return win32_proc.process_owner_sid(proc.pid) == sid
    return proc.uids().real == os.geteuid()


def is_farm_zenserver(proc, cache_dir, worker_sid: Optional[str] = None) -> bool:
    """True only for this worker's own farm zenserver (FR-UE-23, FR-UE-26)."""
    try:
        if not _image_ok(proc):
            return False
        data_dir = parse_data_dir(proc.cmdline())
        if not data_dir or normalize_path(data_dir) != normalize_path(zen_data_dir(cache_dir)):
            return False
        return owned_by_worker(proc, worker_sid)
    except (psutil.Error, OSError, ValueError, IndexError):
        return False


def zenserver_images(engine_roots: Iterable, profile_dir=None) -> frozenset:
    """``normcase``-d exact image paths a farm zenserver runs from (hang-CPU exclusion)."""
    images = {winpath.normcase(winpath.join(root, *ENGINE_ZEN_RELPATH)) for root in engine_roots}
    if profile_dir is not None:
        images.add(winpath.normcase(winpath.join(profile_dir, *PROFILE_ZEN_RELPATH)))
    return frozenset(images)
