# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The per-phase switches of a UE command line and their rules (Unreal
foundation FR-UE-7; #233; card selection FR-CU-2, B5, C-15).

``command._finish`` appends them and ``command.validate_args`` checks them
through ``check_phase_flags``; each must come before the Zen tokens:

* ``-RenderOffscreen``: warm-up and render exactly once; never the prep
  commandlet (it has no Slate app);
* ``-UDPMESSAGING_TRANSPORT_ENABLE=0``: every launch exactly once;
* ``-dx12`` and ``-graphicsadapter=<N>``: warm-up and render exactly once
  each, ``N`` an integer 0-63 made by the worker (the chosen card's DXGI
  index, ``adapter_choice``); never the prep. ``-dx12`` is always passed on
  Windows, so a project whose default RHI is DX11 or Vulkan renders with
  DX12 on the farm and logs the D3D12 adapter it chose;
* no other token may contain ``graphicsadapter=`` or ``gpupreference=``
  (UE finds both anywhere in its command line);
* ``farm_defaults.AUDIO_FLAGS`` (sequence rendering FR-SA-4): an audio
  job's render exactly once each, as given, before the Zen tokens; no other
  launch carries any of them (``check_audio_flags``).

``CommandError`` lives here so both modules raise the same type; ``command``
re-exports it.
"""
import re
from typing import Optional, Sequence

from sethlans_worker_agent.unreal_executor import farm_defaults

HEADLESS = farm_defaults.HEADLESS_FLAGS[0]
ADAPTER_PHASES = ('warmup', 'render')
DX12 = '-dx12'
ADAPTER_PREFIX = '-graphicsadapter'
MAX_ADAPTER = 63
_ADAPTER = re.compile(r'-graphicsadapter=(0|[1-9][0-9]?)', re.ASCII)
STEERING = ('graphicsadapter=', 'gpupreference=')
AUDIO_NAMES = tuple(flag.split('=', 1)[0].lower() for flag in farm_defaults.AUDIO_FLAGS)


class CommandError(ValueError):
    """A UE command line or launch environment breaks an FR-UE-7 rule."""


def positions(args: Sequence[str], predicate) -> list:
    return [index for index, token in enumerate(args) if predicate(token)]


def adapter_flags(index) -> list:
    """``-dx12 -graphicsadapter=<N>`` for a warm-up or render; ``N`` is a worker-made integer 0-63."""
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= MAX_ADAPTER:
        raise CommandError(f"a warm-up or render launch needs the chosen card's DXGI index 0-{MAX_ADAPTER}")
    return [DX12, f'{ADAPTER_PREFIX}={index}']


def _check_headless(args: Sequence[str], phase: str, first_zen: int) -> None:
    found = positions(args, lambda t: t.lower() == HEADLESS.lower())
    if phase in farm_defaults.HEADLESS_PHASES:
        if len(found) != 1 or args[found[0]] != HEADLESS or not found[0] < first_zen:
            raise CommandError(f"a {phase} launch must carry {HEADLESS} exactly once, before the Zen tokens")
    elif found:
        raise CommandError(f"a {phase} launch must not carry {HEADLESS}")


def _check_network(args: Sequence[str], first_zen: int) -> None:
    for flag in farm_defaults.NETWORK_FLAGS:
        name = flag.split('=', 1)[0].lower() + '='
        found = positions(args, lambda t: t.lower().startswith(name))
        if len(found) != 1 or args[found[0]] != flag or not found[0] < first_zen:
            raise CommandError(f"every launch must carry {flag} exactly once, before the Zen tokens")


def _check_adapter(args: Sequence[str], phase: str, first_zen: int) -> None:
    dx12 = positions(args, lambda t: t.lower() == DX12)
    adapter = positions(args, lambda t: t.lower().startswith(ADAPTER_PREFIX))
    if phase not in ADAPTER_PHASES:
        if dx12 or adapter:
            raise CommandError(f"a {phase} launch must not carry {DX12} or {ADAPTER_PREFIX}")
        return
    match = _ADAPTER.fullmatch(args[adapter[0]]) if len(adapter) == 1 else None
    if (len(dx12) != 1 or args[dx12[0]] != DX12 or match is None or int(match.group(1)) > MAX_ADAPTER
            or not max(dx12[0], adapter[0]) < first_zen):
        raise CommandError(f"a {phase} launch must carry {DX12} and {ADAPTER_PREFIX}=N (N 0-{MAX_ADAPTER}) "
                           "exactly once each, before the Zen tokens")


def _check_steering(args: Sequence[str]) -> None:
    """UE finds ``graphicsadapter=`` and ``gpupreference=`` anywhere in its command line (case-insensitive
    substring search), so no token but the worker's own ``-graphicsadapter=N`` may contain either:
    ``-gpupreference=`` would reorder the list and the logged index would still equal ``N`` on another card."""
    for index, token in enumerate(args):
        lowered = token.lower() if isinstance(token, str) else ''
        if any(word in lowered for word in STEERING) and not _ADAPTER.fullmatch(token):
            raise CommandError(f"token {index} must not choose a graphics adapter")


def check_audio_flags(args: Sequence[str], audio: bool, first_zen: int) -> None:
    """FR-SA-4: the audio flags on an audio render only, each exactly once, before the Zen tokens."""
    found = positions(args, lambda t: isinstance(t, str) and t.split('=', 1)[0].lower() in AUDIO_NAMES)
    if not audio:
        if found:
            raise CommandError("only an audio job's render may carry the audio flags")
        return
    for flag in farm_defaults.AUDIO_FLAGS:
        name = flag.split('=', 1)[0].lower()
        hits = [i for i in found if args[i].split('=', 1)[0].lower() == name]
        if len(hits) != 1 or args[hits[0]] != flag or not hits[0] < first_zen:
            raise CommandError(f"an audio render must carry {flag} exactly once, before the Zen tokens")


def check_phase_flags(args: Sequence[str], phase: Optional[str], first_zen: int) -> None:
    """The network switch and the adapter-steering rule always; with ``phase`` also the headless and adapter rules."""
    _check_network(args, first_zen)
    _check_steering(args)
    if phase is not None:
        _check_headless(args, phase, first_zen)
        _check_adapter(args, phase, first_zen)
