# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Gating, card choice and constants of the real per-machine memory test E2E
(Unreal per-machine memory test, chunk 9, AC-E1..AC-E9).

Both parts need everything the other real Unreal E2E tests need
(``unreal_e2e_config.unreal_skip_reason``: ``SETHLANS_UNREAL_E2E=1``,
``SETHLANS_UNREAL_E2E_SHARE``, Windows, UE 5.8.4, the PoC archive) plus:

- **Part A** (``test_unreal_per_machine_memory``): ``SETHLANS_UNREAL_E2E_PM=1``
  and at least two Unreal cards of **one card kind** (PCI vendor, device and
  revision ids, D1) on this machine. Cards are grouped by kind only, never
  by name or brand.
- **Part B** (``test_unreal_per_machine_two_machines``):
  ``SETHLANS_UNREAL_E2E_PM_TWO_MACHINES=1``, the cards of exactly one kind
  ticked here (set ``SETHLANS_COMPUTE_CARDS`` when the machine has more),
  ``SETHLANS_UNREAL_E2E_PM_MANAGER_HOST`` (this machine's LAN address as the
  second machine reaches it) and ``SETHLANS_UNREAL_E2E_PM_PORT`` (the fixed
  manager TLS port the second machine connects to). Allow that port inbound,
  TCP, from the second machine's address only, for the run only (elevated
  PowerShell; ``<ip>`` is the second machine's address)::

      New-NetFirewallRule -DisplayName SethlansPM -Direction Inbound -Action Allow `
          -Protocol TCP -LocalPort <port> -RemoteAddress <ip>
      Remove-NetFirewallRule -DisplayName SethlansPM

  Run Part B on a trusted LAN only: the second worker enrolls with the run's
  enrollment key over that network (the normal enrollment bootstrap).

Optional for both: ``SETHLANS_COMPUTE_CARDS`` (PCI addresses; narrows the
choice), ``SETHLANS_UNREAL_E2E_PM_CACHE_DIR`` (the worker's Unreal cache, a
space-free dir used only by these tests; default: the other tests' cache dir
plus ``-pm``), ``SETHLANS_UNREAL_E2E_PM_OVER_SIZE`` (``WxH``, default
``5440x3060``: a size that clearly over-fills a 20 GB card and leaves 3-5 GB
free on a 32 GB one, by the 1080p/4K figures of the VRAM experiments),
``SETHLANS_UNREAL_E2E_PM_RESULTS`` (the JSON-lines result file for the
report; default in the temp dir), ``SETHLANS_UNREAL_E2E_PM_EXPECT_KIND`` (a
card kind prefix such as ``1002:744c:`` that the pilot's kind must start
with; checked only when set).

Part A only: ``SETHLANS_UNREAL_E2E_PM_E5_SHARED_REF_MB`` (MB) overrides the
spill reference AC-E5 writes into the admitted record. By default it is V1's
result ``shared_peak_mb`` (measured at 640x360) plus frame read-back scaled
by pixel count, ``READBACK_MB_PER_MPX`` MB per extra megapixel (measured
above baseline: +95, +169 and +380 MB at 1080p, 1440p and 4K). Never the
over-size pilot's own shared peak: that holds the overflow.

The checks run in the modules' ``skipif``, so a machine that is not opted in
stops before importing worker code, probing cards or starting anything.
"""

import os
import tempfile

from tests.e2e import unreal_e2e_config as base
from tests.e2e import unreal_per_card_config as pcc

PART_A_ENV = "SETHLANS_UNREAL_E2E_PM"
PART_B_ENV = "SETHLANS_UNREAL_E2E_PM_TWO_MACHINES"
CACHE_ENV = "SETHLANS_UNREAL_E2E_PM_CACHE_DIR"
OVER_SIZE_ENV = "SETHLANS_UNREAL_E2E_PM_OVER_SIZE"
RESULTS_ENV = "SETHLANS_UNREAL_E2E_PM_RESULTS"
EXPECT_KIND_ENV = "SETHLANS_UNREAL_E2E_PM_EXPECT_KIND"
E5_SHARED_REF_ENV = "SETHLANS_UNREAL_E2E_PM_E5_SHARED_REF_MB"
MANAGER_HOST_ENV = "SETHLANS_UNREAL_E2E_PM_MANAGER_HOST"
PORT_ENV = "SETHLANS_UNREAL_E2E_PM_PORT"
HANDOFF_ENV = "SETHLANS_UNREAL_E2E_PM_HANDOFF"
REMOTE_WAIT_ENV = "SETHLANS_UNREAL_E2E_PM_REMOTE_WAIT_S"
ENROLLMENT_KEY_ENV = "SETHLANS_UNREAL_E2E_PM_ENROLLMENT_KEY"

# Versions at the small size: shots 0055 (518-730) and 0060 (730-864) of the PoC
# sequence. With piece_seconds 6 (T = 144, limit 216) each shot is one piece.
PIECE_SECONDS_A = 6
PIECES_A = [(518, 729, 24), (730, 863, 24)]
# Part B: piece_seconds 5 gives three pieces (cfg.SEQUENCE_PIECES), one per card of the farm.
PIECE_SECONDS_B = base.PIECE_SECONDS
SMALL_SIZE = base.SEQUENCE_SIZE
# AC-E6 draft (FR-SQ-7 keys; no draft control raises the peak, SEQ D10). Screen percentage alone
# leaves the warm-up as it is, so the draft pieces reuse V1's warm slots.
DRAFT = {"screen_percentage": 50}
# The over-size versions render a 12-frame range of shot 0060 (one piece, 24-frame lead-in).
OVER_FRAMES = (730, 741)
OVER_PIECES = 1
DEFAULT_OVER_SIZE = (5440, 3060)
# Frame read-back in shared memory per megapixel above the small size (AC-E5's default reference).
READBACK_MB_PER_MPX = 46
# The worker of these tests: job logs kept for every job of a run (default 10), and a render
# that crawls in shared memory (AC-E5) is stopped after 45 minutes instead of 4 hours.
WORKER_EXTRA_ENV = {"SETHLANS_UNREAL_KEEP_JOB_LOGS": "100", "SETHLANS_UNREAL_RENDER_TIMEOUT_SECONDS": "2700"}
DEFAULT_REMOTE_WAIT_S = 2700
_KEY_PREFIX = "pci:"


def _flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true")


def over_size():
    """``(width, height)`` of the over-size versions (AC-E4, AC-E5, AC-E8)."""
    text = os.environ.get(OVER_SIZE_ENV, "").strip().lower()
    if not text:
        return DEFAULT_OVER_SIZE
    width, sep, height = text.partition("x")
    assert sep and width.isdigit() and height.isdigit(), f"{OVER_SIZE_ENV} must be WxH, got {text!r}"
    return int(width), int(height)


def cache_dir():
    explicit = os.environ.get(CACHE_ENV, "").strip()
    return explicit or (os.environ.get(base.CACHE_DIR_ENV) or base.DEFAULT_CACHE_DIR) + "-pm"


def results_path():
    return os.environ.get(RESULTS_ENV, "").strip() or os.path.join(
        tempfile.gettempdir(), "sethlans-unreal-pm-e2e-results.jsonl")


def _kind(card):
    from shared.unreal.card_kind import card_kind
    return card_kind(card.vendor_id, card.device_id, card.revision_id)


def kind_groups(environ=None, probe=pcc._probe):
    """``({kind: [cards]}, None)`` of the Unreal cards (narrowed by ``SETHLANS_COMPUTE_CARDS``), or ``(None, reason)``."""
    from sethlans_worker_agent.cards.identity import key_address
    environ = os.environ if environ is None else environ
    cards = probe()
    if cards is None:
        return None, "listing the graphics cards through the operating system failed"
    usable = [card for card in cards if isinstance(card.key, str) and card.key.startswith(_KEY_PREFIX)
              and card.dxgi_index is not None]
    wanted = pcc._wanted(environ)
    if wanted is not None:
        usable = [card for card in usable if key_address(card.key) in wanted]
    groups = {}
    for card in usable:
        groups.setdefault(_kind(card), []).append(card)
    groups.pop(None, None)  # a card without vendor or device id has no kind and is never tested (D1)
    return groups, None


def _found(groups):
    return ", ".join(f"{kind} x{len(cards)}" for kind, cards in sorted(groups.items())) or "none"


def choose_one_kind(min_cards, environ=None, probe=pcc._probe):
    """``(cards, None)``: the cards of exactly one kind to tick (at least ``min_cards``); ``(None, reason)``."""
    groups, reason = kind_groups(environ, probe)
    if reason:
        return None, reason
    if len(groups) != 1:
        return None, (f"needs the Unreal cards of exactly one card kind; found {_found(groups)}; "
                      f"set {pcc.CARDS_ENV} to the cards of one kind")
    cards = next(iter(groups.values()))
    if len(cards) < min_cards:
        return None, f"needs at least {min_cards} Unreal cards of one card kind; found {_found(groups)}"
    return tuple(cards), None


def part_a_skip_reason():
    """Why Part A cannot run here, or ``None``."""
    reason = base.unreal_skip_reason()
    if reason is not None:
        return reason
    if not _flag(PART_A_ENV):
        return f"the per-machine memory test E2E is opt-in: set {PART_A_ENV}=1"
    return choose_one_kind(2)[1]


def part_b_skip_reason():
    """Why Part B cannot run here, or ``None``."""
    reason = base.unreal_skip_reason()
    if reason is not None:
        return reason
    if not _flag(PART_B_ENV):
        return f"the two-machine memory test E2E is opt-in: set {PART_B_ENV}=1"
    if not os.environ.get(MANAGER_HOST_ENV, "").strip():
        return f"set {MANAGER_HOST_ENV} to this machine's address as the second machine reaches it"
    if not os.environ.get(PORT_ENV, "").strip().isdigit():
        return f"set {PORT_ENV} to the fixed manager port the second machine connects to"
    return choose_one_kind(1)[1]


def worker_env(cards):
    """The worker env of these tests: exactly ``cards`` ticked, plus ``WORKER_EXTRA_ENV``."""
    return dict(pcc.card_env(cards), **WORKER_EXTRA_ENV)
