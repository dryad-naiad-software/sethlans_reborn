# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Farm-profile config check and CVar layer counts (Unreal foundation
FR-UE-27 "Farm profile config check", FR-UE-22; Sec R3-F2, Dom R3-9).

``scan_farm_profile_config(P, trash_dir)`` runs immediately before every
UE launch. The farm profile is persistent and written by UE, and project
code runs as the worker user (D6), so a Zen/DDC key or a CVar entry
written there by one project would apply to every later launch. It reads
every ``*.ini`` under the three config layers a farm launch reads
(``config_layers``):

* ``UnrealEngine\\5.8\\Saved\\Config`` (UE writes it itself): ``zen_guard``'s
  ``ZEN_DDC_INI_RULES`` with the FR-UE-24 line rules (including a non-empty
  ``Local/SharedDerivedDataCache`` ``Path``, review D2) and any
  ``[ConsoleVariables]`` or ``[SystemSettings]`` entry are findings;
  ``[ScalabilityGroups]`` is not (the editor writes it);
* the two ``User*.ini`` layers ``Unreal Engine\\Engine\\Config`` (Local
  AppData and Documents): **any** ``*.ini`` there is a finding (review D4;
  UE never writes them, a farm launch must read none).

A finding, an undecodable file, a forbidden line break or a reparse point
**resets** the three directories: each is moved to ``CACHE_DIR\\trash\\`` (and deleted
there when possible) and re-created empty. The launch then proceeds.

``count_engine_cvars(engine_root, merged)`` counts the engine install's
``Engine\\Config\\ConsoleVariables.ini`` ``[Startup]`` and ``[Startup_Windows]``
entries (``ConfigCacheIni.cpp:6943-6951``), which are machine-wide, still read
and applied above ``-DPCVars`` (FR-UE-22). The stock 5.8 entries
(``STOCK_ENGINE_CVARS_5_8``) are counted but do not warn (review D5).

Both read files only; neither is called while Unreal is disabled (NF-6).
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import zen_guard

logger = logging.getLogger(__name__)

ENGINE_USER_VERSION = '5.8'
CVAR_SECTIONS = frozenset({'consolevariables', 'systemsettings'})
ENGINE_CVARS_RELPATH = ('Engine', 'Config', 'ConsoleVariables.ini')
ENGINE_CVARS_SECTIONS = frozenset({'startup', 'startup_windows'})
ANY_INI_LAYERS = (1, 2)  # indexes in config_layers(): the two "Unreal Engine\Engine\Config" layers
# (name, value) pairs of the stock UE 5.8 ConsoleVariables.ini [Startup] (theta, 5.8.4).
STOCK_ENGINE_CVARS_5_8 = frozenset({('p.chaos.allowcreatephysxbodies', '1'),
                                    ('fx.skipvectorvmbackendoptimizations', '1')})
_LINE_BREAK = re.compile(r'\r\n|\r|\n')
_FORBIDDEN = zen_guard._FORBIDDEN_CHARS  # the FR-UE-24 line-splitting rule, shared on purpose


def config_layers(profile, version: str = ENGINE_USER_VERSION) -> Tuple[Path, ...]:
    """The three farm-profile config directories a farm launch reads."""
    base = Path(profile) / 'Profile'
    return (base / 'AppData' / 'Local' / 'UnrealEngine' / version / 'Saved' / 'Config',
            base / 'AppData' / 'Local' / 'Unreal Engine' / 'Engine' / 'Config',
            base / 'Documents' / 'Unreal Engine' / 'Engine' / 'Config')


@dataclass
class ProfileConfigResult:
    reset: int = 0                       # 1 when this check reset the layers (user_profile_reset=<n>)
    found_cvars: int = 0                 # CVar entries found before a reset
    user_cvars: int = 0                  # entries a launch would read now (0 after a reset)
    collisions: Tuple[str, ...] = ()     # found names that are in the merged -DPCVars set
    findings: List[str] = field(default_factory=list)
    error: str = ''                      # a reset that could not be completed

    @property
    def ok(self) -> bool:
        return not self.error


def iter_entries(text: str) -> Iterator[Tuple[str, str, str]]:
    """``(section, key, value)`` per ini entry, split on CR/LF only (as UE does)."""
    section = ''
    for raw in _LINE_BREAK.split(text):
        line = raw.strip(' \t')
        if not line or line[0] in ';#':
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip(' \t')
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            yield section, key.strip(' \t').lstrip('+-.!').strip(' \t'), value


def _read_text(path: Path) -> Optional[str]:
    """Decoded text, or None when it is too large, undecodable or has a forbidden break."""
    try:
        if fs_safe.lstat(path).st_size > zen_guard.MAX_INI_BYTES:
            return None
        with open(fs_safe.long_path(path), 'rb') as handle:
            text = zen_guard.decode_ini(handle.read(zen_guard.MAX_INI_BYTES + 1))
    except (OSError, UnicodeDecodeError):
        return None
    return None if _FORBIDDEN.search(text) else text


def _first_link(profile: Path, layer: Path) -> Optional[Path]:
    """The first existing component below ``profile`` on the way to ``layer`` that is a link."""
    current = profile
    for part in layer.relative_to(profile).parts:
        current = current / part
        if not fs_safe.lexists(current):
            return None
        if fs_safe.is_reparse(fs_safe.lstat(current)):
            return current
    return None


def _scan_layer(profile: Path, layer: Path, result: ProfileConfigResult, merged_folded: frozenset,
                any_ini: bool) -> None:
    link = _first_link(profile, layer)
    if link is not None:
        result.findings.append(f"{link} is a link")
        return
    if not fs_safe.lexists(layer):
        return
    try:
        listing = fs_safe.walk_tree(layer)
    except OSError as exc:
        result.findings.append(f"{layer}: {exc}")
        return
    collisions = set(result.collisions)
    for rel in sorted(r for r in listing.files if r.lower().endswith('.ini')):
        if any_ini:
            result.findings.append(f"{layer.name}/{rel}: no ini file belongs in this layer")
            continue
        text = _read_text(layer.joinpath(*rel.split('/')))
        if text is None:
            result.findings.append(f"{layer.name}/{rel} cannot be decoded")
            continue
        _scan_text(text, rel, result, merged_folded, collisions)
    result.collisions = tuple(sorted(collisions))


def _scan_text(text: str, rel: str, result: ProfileConfigResult, merged_folded: frozenset, collisions: set) -> None:
    for hit in zen_guard.scan_ini_text(text, rel):
        result.findings.append(f"{rel}: [{hit.section}] {hit.key}")
    for section, key, _value in iter_entries(text):
        if section.lower() in CVAR_SECTIONS:
            result.found_cvars += 1
            result.findings.append(f"{rel}: [{section}] {key}")
            if key.casefold() in merged_folded:
                collisions.add(key)


def _recreate(profile: Path, layer: Path) -> None:
    current = Path(profile)
    fs_safe.check_owned_dir(current)
    for part in layer.relative_to(profile).parts:
        current = current / part
        fs_safe.ensure_owned_dir(current)


def _reset(profile: Path, layers: Iterable[Path], trash_dir: Path, result: ProfileConfigResult) -> None:
    for layer in layers:
        target = _first_link(profile, layer) or layer  # a link is moved as a link, never followed
        if fs_safe.lexists(target):
            moved = fs_safe.move_aside(target, trash_dir)
            if moved is None:
                result.error = f"could not move {target} to trash"
                return
            fs_safe.remove_tree(moved)  # best effort; recovery clears trash/ otherwise
        try:
            _recreate(profile, layer)
        except OSError as exc:
            result.error = f"could not re-create {layer}: {exc}"
            return
    result.reset = 1


def scan_farm_profile_config(profile, trash_dir, merged_names: Iterable[str] = (),
                             version: str = ENGINE_USER_VERSION) -> ProfileConfigResult:
    """FR-UE-27 farm-profile config check, with the reset rule. Never raises."""
    result = ProfileConfigResult()
    profile = Path(profile)
    layers = config_layers(profile, version)
    merged_folded = frozenset(name.casefold() for name in merged_names)
    try:
        for index, layer in enumerate(layers):
            _scan_layer(profile, layer, result, merged_folded, index in ANY_INI_LAYERS)
    except OSError as exc:
        result.findings.append(f"config layers could not be read: {exc}")
    if not result.findings:
        result.user_cvars = 0
        return result
    for finding in result.findings[:20]:
        logger.warning("Farm profile config reset: %s", finding)
    _reset(profile, layers, Path(trash_dir), result)
    result.user_cvars = result.found_cvars if result.error else 0
    return result


@dataclass(frozen=True)
class EngineCvarCount:
    count: int = 0
    names: Tuple[str, ...] = ()
    non_stock: Tuple[str, ...] = ()
    collisions: Tuple[str, ...] = ()
    error: str = ''

    @property
    def warn(self) -> bool:
        """A warning is due for non-stock entries or a collision with the merged CVars."""
        return bool(self.non_stock or self.collisions or self.error)


def count_engine_cvars(engine_root, merged_names: Iterable[str] = ()) -> EngineCvarCount:
    """``[Startup]`` and ``[Startup_Windows]`` of the engine's ``ConsoleVariables.ini`` (read-only)."""
    path = Path(engine_root).joinpath(*ENGINE_CVARS_RELPATH)
    if not path.is_file():
        return EngineCvarCount()
    text = _read_text(path)
    if text is None:
        return EngineCvarCount(error=f"{path} could not be read")
    entries = [(key, value.strip()) for section, key, value in iter_entries(text)
               if section.lower() in ENGINE_CVARS_SECTIONS]
    merged_folded = {name.casefold() for name in merged_names}
    names = tuple(key for key, _value in entries)
    non_stock = tuple(key for key, value in entries if (key.casefold(), value) not in STOCK_ENGINE_CVARS_5_8)
    collisions = tuple(sorted({key for key in names if key.casefold() in merged_folded}))
    for key in names:
        level = logging.WARNING if key in non_stock or key in collisions else logging.INFO
        logger.log(level, "Engine ConsoleVariables.ini startup entry: %s", key)
    return EngineCvarCount(count=len(names), names=names, non_stock=non_stock, collisions=collisions)
