# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Environment cleanup for spawning Blender from a frozen PyInstaller bundle.

PyInstaller injects environment variables (LD_LIBRARY_PATH, DYLD_LIBRARY_PATH,
DYLD_FALLBACK_LIBRARY_PATH, PYTHONPATH, PYTHONHOME, TCL_LIBRARY, TK_LIBRARY,
etc.) that interfere with Blender's own libraries and embedded Python. This
module strips those variables so Blender runs with a clean environment.

When NOT running in frozen mode (i.e., development), ``clean_env_for_blender``
returns ``None`` so callers can pass ``env=None`` to ``Popen``, inheriting the
parent environment as-is.

``clean_env_for_unreal`` (Unreal foundation FR-UE-18) always scrubs, frozen
or not; see its docstring.
"""

import logging
import os
import platform
import re
import sys

logger = logging.getLogger(__name__)

# Keys whose presence anywhere in the variable name marks a
# PyInstaller-injected variable that should be removed entirely.
_PYINSTALLER_KEY_MARKERS = ('_MEIPASS', '_MEI', 'PYINSTALLER')

# Keys removed unconditionally in frozen mode because they break
# Blender's embedded Python.
_KEYS_TO_REMOVE = frozenset({
    'PYTHONPATH',
    'PYTHONHOME',
    'TCL_LIBRARY',
    'TK_LIBRARY',
})

# Prefix for Sethlans-specific secrets that Blender does not need.
# Prevents malicious .blend scripts from exfiltrating enrollment keys
# or API tokens via the subprocess environment.
_SETHLANS_PREFIX = 'SETHLANS_'

# Library path variables that need PyInstaller entries stripped (not
# removed entirely — system entries are preserved).
_LIB_PATH_VARS_BY_PLATFORM = {
    'Linux': ('LD_LIBRARY_PATH',),
    'Darwin': ('DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'),
}


def _is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle."""
    return getattr(sys, 'frozen', False)


def _should_remove_key(key: str) -> bool:
    """Return True if *key* contains a PyInstaller marker."""
    upper = key.upper()
    return any(marker in upper for marker in _PYINSTALLER_KEY_MARKERS)


def _strip_internal_paths(path_string: str) -> str:
    """Remove entries containing ``_internal`` from a PATH-style string."""
    sep = os.pathsep
    entries = path_string.split(sep)
    cleaned = [e for e in entries if '_internal' not in e]
    return sep.join(cleaned)


def _strip_lib_path_vars(env: dict) -> None:
    """Strip ``_internal`` entries from platform library path variables."""
    lib_vars = _LIB_PATH_VARS_BY_PLATFORM.get(platform.system(), ())
    for var in lib_vars:
        if var in env:
            original = env[var]
            cleaned = _strip_internal_paths(original)
            if cleaned:
                env[var] = cleaned
            else:
                del env[var]
            if env.get(var) != original:
                logger.debug("Stripped _internal paths from %s", var)


def _remove_keys_by_prefix(env: dict, prefix: str) -> None:
    """Remove all keys starting with *prefix*."""
    keys = [k for k in env if k.startswith(prefix)]
    for k in keys:
        del env[k]
        logger.debug("Removed env var: %s", k)


def clean_env_for_blender(blend_file_path: str = None) -> dict | None:
    """Return a cleaned ``os.environ`` copy for spawning Blender.

    Strips PyInstaller-injected variables so Blender can locate its own
    libraries and embedded Python correctly.

    Returns:
        A dict suitable for ``subprocess.Popen(env=...)``, or ``None``
        when running in development mode (not frozen).
    """
    if not _is_frozen():
        return None

    env = dict(os.environ)

    # 1. Remove keys containing PyInstaller markers.
    for k in [k for k in env if _should_remove_key(k)]:
        del env[k]
        logger.debug("Removed PyInstaller env var: %s", k)

    # 2. Remove keys that break Blender's embedded Python.
    for k in _KEYS_TO_REMOVE & env.keys():
        del env[k]

    # 3. Strip _internal paths from library search variables.
    _strip_lib_path_vars(env)

    # 4. Remove Sethlans secrets that Blender does not need.
    _remove_keys_by_prefix(env, _SETHLANS_PREFIX)

    # 5. Strip _internal paths from PATH.
    if 'PATH' in env:
        env['PATH'] = _strip_internal_paths(env['PATH'])

    return env


# --- Unreal (Unreal foundation FR-UE-18) -----------------------------------

# Python variables UE's embedded Python (and any child Python) honors.
_UNREAL_PYTHON_KEYS = frozenset({
    'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE', 'PYTHONNOUSERSITE',
})
# Every variable UE or its tools read by name starts with ``UE-`` or ``UE_``:
# ``UE-CmdLineArgs`` (appended to the command line **after** -DPCVars,
# LaunchEngineLoop.cpp:1815-1827), the Zen/DDC data paths and hosts
# (``UE-ZenDataPath``, ``UE-ZenSubprocessDataPath``, ``UE-*DataCachePath``,
# ``UE-ZenSharedDataCacheHost``, ``UE-CloudDataCacheHost``), ``UE_PYTHONPATH``,
# ``UE_HORDE_*``, telemetry, profiler and dump paths. All are removed (Sec S1).
_UNREAL_PREFIX_RE = re.compile(r'^UE[-_]', re.IGNORECASE)
# FR-UE-27: the launch builder sets these; a mixed-case copy must never survive.
UNREAL_ISOLATION_VARS = ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'TEMP', 'TMP')
UNREAL_PARAMS_VAR = 'SETHLANS_UE_JOB_PARAMS'


def _unreal_key_removed(key: str) -> bool:
    upper = key.upper()
    return (_should_remove_key(key) or upper in _UNREAL_PYTHON_KEYS or upper in _KEYS_TO_REMOVE
            or _UNREAL_PREFIX_RE.match(key) is not None or upper.startswith(_SETHLANS_PREFIX)
            or upper in UNREAL_ISOLATION_VARS)


def clean_env_for_unreal(params_path=None, base_env=None) -> dict:
    """Return a scrubbed environment for a UE launch, frozen or not (FR-UE-18).

    Removes PyInstaller markers, the Python variables, **every** key that
    starts with ``UE-`` or ``UE_`` (any case; includes ``UE-CmdLineArgs``,
    ``UE_PYTHONPATH`` and every Zen/DDC override), every ``SETHLANS_*`` key
    and the seven FR-UE-27 isolation variables in any spelling; strips
    ``_internal`` entries from ``PATH`` and the library variables; then sets
    ``SETHLANS_UE_JOB_PARAMS`` when ``params_path`` is given. The isolation
    variables are added afterwards by the launch builder
    (``user_state.isolation_env(P)``).
    """
    source = dict(os.environ if base_env is None else base_env)
    env = {key: value for key, value in source.items() if not _unreal_key_removed(key)}
    _strip_lib_path_vars(env)
    for key in [k for k in env if k.upper() == 'PATH']:
        cleaned = _strip_internal_paths(env[key])
        if cleaned:
            env[key] = cleaned
        else:
            del env[key]
    if params_path is not None:
        env[UNREAL_PARAMS_VAR] = str(params_path)
    return env
