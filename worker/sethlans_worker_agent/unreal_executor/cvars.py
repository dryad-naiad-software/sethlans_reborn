# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
CVar merge and the effective-CVar check (Unreal foundation FR-UE-5,
FR-UE-8 step 4a; R10; v4.1: S1).

* ``merge(farm, overrides, critical, draft)``: farm defaults (``farm_tier``: an audio job adds
  ``AUDIO_CVARS``, FR-SA-4) < project overrides < a piece's draft CVars
  (sequence D11, R10; a draft value only ever lowers, ``draft_tier``) < farm-critical, names compared
  case-insensitively (UE treats them as one). Farm keys first in declared order, then the rest sorted; every
  name and value re-validated with ``shared.unreal.validation``.
* ``cvars_sha256(merged)``: SHA-256 of the ``-DPCVars`` string; ``warmup_cvars_sha256``, the warm-up marker
  key (FR-UE-9), leaves out the shader-neutral CVars.
* ``check_effective(effective, merged)``: the prep's ``effective_cvars`` are untrusted (names must pass
  ``CVAR_NAME_RE`` and be in the merged set; values truncated to 64 characters, control characters stripped).
  Values are normalized (``true``/``false`` -> ``1``/``0``; two numbers compare numerically). A differing
  ``POOL_CVARS`` or ``FARM_CVARS`` value fails the job; any other is a ``cvar_overridden=<name>`` warning.
"""
import hashlib
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sethlans_worker_agent.unreal_executor import farm_defaults
from shared.unreal import validation, vram_gate

MAX_VALUE = 64
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
# D11: the only CVars a draft sends, by draft key (motion blur: also temporal samples 1 on the preset copy).
DRAFT_CVARS = {'screen_percentage': 'r.ScreenPercentage', 'ray_traced_shadows': 'r.RayTracing.Shadows',
               'ray_traced_reflections': 'r.Lumen.Reflections.HardwareRayTracing', 'motion_blur': 'r.MotionBlurQuality'}
# Shader-neutral, so left out of the warm-up key; the ray-tracing and Lumen switches change shaders and stay in.
# The audio CVars only keep the non-realtime mixer audible, so an audio job shares its pieces' warm-up.
WARMUP_NEUTRAL = frozenset({'r.screenpercentage', 'r.motionblurquality'}
                           | {name.casefold() for name in farm_defaults.AUDIO_CVARS})
_NAME = re.compile(validation.CVAR_NAME_RE, re.ASCII)


def farm_tier(audio: bool = False) -> Optional[dict]:
    """The farm tier of a job: ``None`` (the farm defaults), or with ``AUDIO_CVARS`` for an audio job (R10)."""
    return {**farm_defaults.FARM_CVARS, **farm_defaults.AUDIO_CVARS} if audio else None


def merge(farm: Optional[dict] = None, overrides: Optional[dict] = None,
          critical: Optional[dict] = None, draft: Optional[dict] = None) -> List[Tuple[str, str]]:
    """The merged ``(name, value)`` list; raises ``ValueError`` on an invalid entry."""
    farm = dict(farm_defaults.FARM_CVARS if farm is None else farm)
    below = {name.casefold(): value for tier in (farm, overrides or {}) for name, value in tier.items()}
    tiers = [farm, dict(overrides or {}), draft_tier(draft or {}, below),
             dict(farm_defaults.FARM_CRITICAL_CVARS if critical is None else critical)]
    values: Dict[str, Tuple[str, str]] = {}  # folded name -> (name as first declared, value)
    for tier in tiers:
        for name, value in validation.validate_cvars(tier).items():
            folded = name.casefold()
            first = values[folded][0] if folded in values else name
            values[folded] = (first, value)
    farm_folded = [name.casefold() for name in farm]
    ordered = [values[folded] for folded in farm_folded]
    rest = sorted((pair for folded, pair in values.items() if folded not in farm_folded),
                  key=lambda pair: pair[0].casefold())
    merged = ordered + rest
    validation.validate_cvars(dict(merged))
    return merged


def draft_tier(draft: dict, below: Dict[str, str]) -> dict:
    """D11: a draft value a lower tier sets too (``below``, folded) stays only when both are numbers and it is lower."""
    kept = {}
    for name, value in draft.items():
        current = below.get(str(name).casefold())
        low, high = _numeric(normalize(str(value))), _numeric(normalize(current)) if current is not None else None
        if current is None or (low is not None and high is not None and low < high):
            kept[name] = value
    return kept


def dpcvars_string(merged: Sequence[Tuple[str, str]]) -> str:
    return ','.join(f"{name}={value}" for name, value in merged)


def cvars_sha256(merged: Sequence[Tuple[str, str]]) -> str:
    return hashlib.sha256(dpcvars_string(merged).encode('ascii')).hexdigest()


def warmup_cvars_sha256(merged: Sequence[Tuple[str, str]]) -> str:
    """The warm-up marker key (FR-UE-9): ``cvars_sha256`` without the ``WARMUP_NEUTRAL`` CVars."""
    return cvars_sha256([(name, value) for name, value in merged if name.casefold() not in WARMUP_NEUTRAL])


def sanitize_value(value) -> str:
    return _CONTROL.sub('', str(value))[:MAX_VALUE]


def normalize(value: str) -> str:
    text = str(value).strip()
    lowered = text.lower()
    if lowered == 'true':
        return '1'
    if lowered == 'false':
        return '0'
    return text


def _numeric(value: str) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return None


def same_value(requested: str, effective: str) -> bool:
    a, b = normalize(requested), normalize(effective)
    if a == b:
        return True
    na, nb = _numeric(a), _numeric(b)
    return na is not None and nb is not None and na == nb


def critical_names() -> frozenset:
    names = set(vram_gate.POOL_CVARS) | set(farm_defaults.FARM_CVARS)
    return frozenset(name.casefold() for name in names)


def check_effective(effective, merged: Iterable[Tuple[str, str]]) -> Tuple[str, List[str]]:
    """``(error_message, overridden_names)`` for the prep's untrusted ``effective_cvars``."""
    if not isinstance(effective, dict):
        return '', []
    requested = {name.casefold(): (name, value) for name, value in merged}
    critical = critical_names()
    overridden: List[str] = []
    for raw_name, raw_value in effective.items():
        if not isinstance(raw_name, str) or not _NAME.fullmatch(raw_name):
            continue
        folded = raw_name.casefold()
        if folded not in requested:
            continue
        name, want = requested[folded]
        got = sanitize_value(raw_value)
        if same_value(want, got):
            continue
        if folded in critical:
            return (f"CVar {name} is {got}, not {want}; an engine-level override "
                    "(Engine/Config/ConsoleVariables.ini or -cvarsini) is in effect."), overridden
        overridden.append(name)
    return '', sorted(overridden)
