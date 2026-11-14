# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Validators for Unreal job and project data (Unreal foundation FR-MU-5, FR-MU-7).

Used by the manager (API validation) and re-applied by the worker before
anything reaches a UE command line or script. Every function raises
``ValueError`` naming the problem; nothing here touches the filesystem.

Patterns are matched with ``re.fullmatch`` and ``re.ASCII``, so a trailing
newline never passes (``$`` alone would allow one).
"""
import hashlib
import json
import re

OBJECT_PATH_RE = r'^/[A-Za-z0-9_]+(?:/[A-Za-z0-9_\-]+)+(?:\.[A-Za-z0-9_\-]+)?$'
OBJECT_PATH_MAX = 512

# At least one ``.``: a CVar name can never equal a bare command-line switch.
CVAR_NAME_RE = r'^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$'
CVAR_NAME_MAX = 128
CVAR_VALUE_RE = r'^[A-Za-z0-9_.+\-]{1,64}$'
MAX_CVARS = 64

# UE command-line switches. ``FParse::Value`` searches the command line by
# substring, so a CVar name is rejected when any ``.``-separated component,
# case-folded, is one of these (``-DPCVars=x.abslog=...`` was read as
# ``-abslog=``; v4.1 spike S1).
UE_SWITCH_DENYLIST = frozenset({
    'execcmds', 'abslog', 'log', 'ini', 'dpcvars', 'moviepipelineconfig',
    'game', 'unattended', 'nosplash', 'windowed', 'resx', 'resy', 'run',
    'script', 'executepythonscript', 'graphicsadapter', 'zendatapath',
    'localdatacachepath', 'shareddatacachepath', 'ddc', 'cvarsini',
    'logfilename', 'zenlogpath', 'zencfgpath',
    # CSV profiler switches (v4.4, Sec R3-W7): the pilot passes the first
    # two itself; the others could redirect the capture or run commands.
    'csvcaptureframes', 'csvgpustats', 'csvexeccmds', 'csvfile',
    'csvfilename', 'csvmetadata', 'csvcategories',
})
# Suffix rule: a ``FParse::Value`` match counts only when the preceding
# character is not a letter or digit (``.`` and ``_`` are boundaries), so
# ``r.x_abslog=`` is read as ``-abslog=`` and ``r.xabslog=`` is not (S1).
# A final component ending in ``_<switch>`` is rejected for **every**
# denylisted switch, not only the ones S1 showed are read in ``switch=``
# form: ``r.x_log=`` would be read as ``-LOG=`` (UE looks for it before
# ``-ABSLOG=``) and redirect the log the worker validates.

UPROJECT_MAX = 260
_UPROJECT_FILE = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}\.uproject', re.ASCII)
_UPROJECT_DIR = re.compile(r'[A-Za-z0-9_][A-Za-z0-9 _.\-]{0,127}', re.ASCII)
_SHA256 = re.compile(r'[0-9a-f]{64}', re.ASCII)


def _fullmatch(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value, re.ASCII) is not None


def validate_object_path(value) -> str:
    """Validate a UE object path such as ``/Game/Maps/Main.Main``."""
    if not isinstance(value, str) or len(value) > OBJECT_PATH_MAX:
        raise ValueError(f"object path must be a string of at most {OBJECT_PATH_MAX} characters")
    if not _fullmatch(OBJECT_PATH_RE, value):
        raise ValueError(f"{value!r} is not a valid Unreal object path (e.g. /Game/Maps/Main.Main)")
    return value


def _check_cvar_name(name) -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= CVAR_NAME_MAX:
        raise ValueError(f"CVar names must be strings of 1-{CVAR_NAME_MAX} characters")
    if not _fullmatch(CVAR_NAME_RE, name):
        raise ValueError(f"CVar name {name!r} is invalid (letters, digits, '_', at least one '.')")
    components = name.casefold().split('.')
    denied = [c for c in components if c in UE_SWITCH_DENYLIST]
    if denied:
        raise ValueError(f"CVar name {name!r} contains the command-line switch {denied[0]!r}")
    final = components[-1]
    for switch in UE_SWITCH_DENYLIST:
        if final.endswith('_' + switch):
            raise ValueError(f"CVar name {name!r} ends with the command-line switch '_{switch}'")


def _normalize_cvar_value(name: str, value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"CVar {name!r}: values must be strings or integers")
    text = str(value)
    if not _fullmatch(CVAR_VALUE_RE, text):
        raise ValueError(f"CVar {name!r}: value {text[:80]!r} is invalid (1-64 of A-Z a-z 0-9 _ . + -)")
    return text


def validate_cvars(value) -> dict:
    """Validate a CVar override map; return it with values normalized to str.

    At most ``MAX_CVARS`` entries. Names follow ``CVAR_NAME_RE``, contain
    no denylisted switch component and no ``_<switch>`` suffix for any
    denylisted switch; two names
    that differ only in letter case are rejected (UE treats them as one).

    Raises:
        ValueError: naming the offending entry.
    """
    if not isinstance(value, dict):
        raise ValueError("CVar overrides must be an object mapping names to values")
    if len(value) > MAX_CVARS:
        raise ValueError(f"at most {MAX_CVARS} CVar overrides are allowed")
    result = {}
    seen = set()
    for name, raw in value.items():
        _check_cvar_name(name)
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"CVar {name!r} is listed twice (names are case-insensitive)")
        seen.add(folded)
        result[name] = _normalize_cvar_value(name, raw)
    return result


def validate_uproject_relpath(value) -> str:
    """Validate the ``.uproject`` path inside an archive (FR-MU-7).

    1-3 ``/``-separated components, at most ``UPROJECT_MAX`` characters,
    no backslash or leading ``/``; the last component is
    ``<Name>.uproject``.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= UPROJECT_MAX:
        raise ValueError(f"uproject_path must be 1-{UPROJECT_MAX} characters")
    if '\\' in value or value.startswith('/'):
        raise ValueError("uproject_path must be relative and '/'-separated")
    parts = value.split('/')
    if len(parts) > 3:
        raise ValueError("uproject_path may have at most 3 components")
    for part in parts[:-1]:
        if part in ('.', '..') or part.endswith((' ', '.')) or not _UPROJECT_DIR.fullmatch(part):
            raise ValueError(f"uproject_path directory {part!r} is invalid")
    if not _UPROJECT_FILE.fullmatch(parts[-1]):
        raise ValueError("uproject_path must end with <Name>.uproject (letters, digits, '_')")
    return value


def validate_sha256(value) -> str:
    """Return a SHA-256 hex digest stripped and lowercased.

    ``Get-FileHash`` prints uppercase; both spellings are accepted.
    """
    if not isinstance(value, str):
        raise ValueError("SHA-256 must be a string of 64 hex characters")
    digest = value.strip().lower()
    if not _SHA256.fullmatch(digest):
        raise ValueError("SHA-256 must be 64 hex characters")
    return digest


def _frozen_sha256(value) -> str:
    """SHA-256 of the canonical JSON of ``value`` (sorted keys, compact, ASCII)."""
    text = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def overrides_sha256(overrides) -> str:
    """Return the frozen canonical hash of a project's CVar overrides.

    FROZEN: its output defines stored measurement keys (FR-PM-1), so
    changing it orphans every measurement.  Pure and never raising: keys
    are used as stored (``str(k)``), values as ``str(v)``; a non-dict
    hashes as ``{}``.  It deliberately does not call ``validate_cvars``.
    """
    if not isinstance(overrides, dict):
        overrides = {}
    return _frozen_sha256({str(k): str(v) for k, v in overrides.items()})


def target_sha256(engine_build, map_path, sequence, preset) -> str:
    """Return the frozen hash of a measurement target (FR-PM-1, OQ-26).

    The same frozen, pure, never-raising form as :func:`overrides_sha256`
    over ``[engine_build, map, sequence, preset]`` (each ``str(x)``,
    ``None`` as ``''``).
    """
    parts = [engine_build, map_path, sequence, preset]
    return _frozen_sha256(['' if part is None else str(part) for part in parts])
