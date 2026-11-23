# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A stand-in "other program" on one graphics card (Windows only), for the
real per-machine memory test E2E (owner decision Q10, band 2).

Run as its own process, so the worker sees it as another program and never
as a member of a job's Job Object::

    python tests/e2e/gpu_memory_holder.py --card pci:0000:01:00.0 --mb 1536 \\
        --seconds 30 --status holder.json [--watch-pid N ...]

- **The card, by identity only.** ``--card`` is a worker card key. The
  worker's own Windows card probe (``cards.os_probe_windows.cards_with_luids``:
  DXGI adapters, each with its PCI address from D3DKMT) gives the card with
  that PCI address and its LUID; the memory goes on the DXGI adapter with
  that LUID (``gpu_memory_d3d12``). No match, or more than one, ends the
  run with ``EXIT_REFUSED``; there is never a fallback to another adapter.
- **What it holds.** ``--mb`` MB of committed buffer resources in a DEFAULT
  heap (video memory), in ``--chunk-mb`` pieces; at most ``MAX_MB`` and at
  most ``MAX_CARD_FRACTION`` of the card's dedicated memory. An allocation
  that fails frees what was made and ends with ``EXIT_FAILED``.
- **How long.** Until ``--seconds`` (at most ``MAX_SECONDS``) have passed
  or any ``--watch-pid`` process is gone (checked every ``WATCH_POLL_S``),
  then it frees everything and exits 0 by itself.
- **Status.** ``--status`` is a small JSON file, rewritten atomically at each
  step: ``pid``, ``state`` (``starting``, ``holding``, ``freed``,
  ``refused``, ``failed``), ``card``, ``adapter``, ``requested_mb``,
  ``allocated_mb``, ``stop_reason``, ``error`` and wall-clock times. The
  LUID is never written (it changes on reboot).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT / "worker"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

MAX_SECONDS = 600
MAX_MB = 16384
MAX_CARD_FRACTION = 0.5
DEFAULT_CHUNK_MB = 256
WATCH_POLL_S = 0.5
EXIT_REFUSED = 2
EXIT_FAILED = 3
FINAL_STATES = ("freed", "refused", "failed")
REPLACE_TRIES = 20
REPLACE_RETRY_S = 0.05
MIB = 1024 * 1024


class HolderError(RuntimeError):
    """The run cannot hold what was asked (bad card, size or platform)."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Hold GPU memory on one card as another program would.")
    parser.add_argument("--card", required=True, help="worker card key, e.g. pci:0000:01:00.0")
    parser.add_argument("--mb", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--status", required=True, help="JSON status file")
    parser.add_argument("--chunk-mb", type=int, default=DEFAULT_CHUNK_MB)
    parser.add_argument("--watch-pid", type=int, action="append", default=[])
    args = parser.parse_args(argv)
    if not 1 <= args.mb <= MAX_MB:
        parser.error(f"--mb must be 1..{MAX_MB}")
    if not 0 < args.seconds <= MAX_SECONDS:
        parser.error(f"--seconds must be more than 0 and at most {MAX_SECONDS}")
    if not 1 <= args.chunk_mb <= 1024:
        parser.error("--chunk-mb must be 1..1024")
    return args


def chunk_sizes(mb, chunk_mb):
    """``mb`` split into ``chunk_mb`` pieces, the last one smaller when it does not divide."""
    full, rest = divmod(mb, chunk_mb)
    return [chunk_mb] * full + ([rest] if rest else [])


def match_card(card_key, pairs):
    """``(card, luid)`` of the one probed card whose PCI address is ``card_key``'s; ``HolderError`` otherwise."""
    from sethlans_worker_agent.cards.identity import key_address
    wanted = key_address(card_key)
    if wanted is None:
        raise HolderError(f"{card_key!r} is not a PCI card key")
    if pairs is None:
        raise HolderError("the Windows card list (DXGI and D3DKMT) is unavailable")
    found = [(card, luid) for card, luid in pairs if key_address(card.key) == wanted]
    if len(found) != 1:
        raise HolderError(f"{len(found)} cards have the address of {card_key}; known: {[c.key for c, _l in pairs]}")
    return found[0]


def check_size(mb, card):
    """``mb`` is at most ``MAX_CARD_FRACTION`` of the card's dedicated memory."""
    limit = int(card.dedicated_mib * MAX_CARD_FRACTION)
    if mb > limit:
        raise HolderError(f"{mb} MB is more than {MAX_CARD_FRACTION:.0%} of {card.name}'s {card.dedicated_mib} MB")


def stop_reason(now, deadline, watch_pids, alive):
    """Why the hold ends now (``'seconds'``, ``'pid N ended'``), or ``None`` to keep holding."""
    for pid in watch_pids:
        if not alive(pid):
            return f"pid {pid} ended"
    return "seconds" if now >= deadline else None


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Status:
    """The status JSON, rewritten atomically on every ``update``."""

    def __init__(self, path, **fields):
        self.path, self.data = Path(path), {"pid": os.getpid(), "state": "starting", "started_at": _now(), **fields}
        self.write()

    def update(self, **fields):
        self.data.update(fields)
        self.write()

    def write(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
        for attempt in range(REPLACE_TRIES):  # a reader holding the file open makes Windows refuse the replace
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt + 1 == REPLACE_TRIES:
                    raise
                time.sleep(REPLACE_RETRY_S)


_watched = {}  # pid -> psutil.Process, made on first look so is_running() also catches a reused pid


def _alive(pid):
    """The process still runs. Not ``psutil.pid_exists``: an exited process whose handle another process (the
    worker) still holds keeps its pid listed; ``is_running``, the zombie status and a zero-timeout ``wait`` (the
    process's own exit, Windows included) see it ended."""
    import psutil
    try:
        proc = _watched[pid] if pid in _watched else _watched.setdefault(pid, psutil.Process(pid))
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
        proc.wait(timeout=0)
        return False  # it has exited
    except psutil.TimeoutExpired:
        return True
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True  # it exists; another user's process


def _hold(args, status, memory):
    """Allocate, hold until a stop reason, free; returns the exit code."""
    start, allocated = time.monotonic(), 0
    try:
        memory.open()
        for size in chunk_sizes(args.mb, args.chunk_mb):
            memory.allocate(size * MIB)
            allocated += size
        status.update(state="holding", allocated_mb=allocated, held_at=_now(),
                      allocate_s=round(time.monotonic() - start, 2))
        deadline = time.monotonic() + args.seconds
        reason = stop_reason(time.monotonic(), deadline, args.watch_pid, _alive)
        while reason is None:
            time.sleep(WATCH_POLL_S)
            reason = stop_reason(time.monotonic(), deadline, args.watch_pid, _alive)
    except Exception as exc:  # noqa: BLE001 - any failure is reported in the status and the exit code
        memory.free()
        status.update(state="failed", allocated_mb=allocated, error=f"{type(exc).__name__}: {exc}", freed_at=_now())
        return EXIT_FAILED
    memory.free()
    status.update(state="freed", stop_reason=reason, freed_at=_now())
    return 0


def main(argv=None):
    args = parse_args(argv)
    status = Status(args.status, card=args.card, requested_mb=args.mb, allocated_mb=0, seconds=args.seconds,
                    watch_pids=args.watch_pid)
    try:
        if sys.platform != "win32":
            raise HolderError("the GPU memory holder runs on Windows only")
        from sethlans_worker_agent.cards import os_probe_windows
        card, luid = match_card(args.card, os_probe_windows.cards_with_luids())
        check_size(args.mb, card)
    except HolderError as exc:
        status.update(state="refused", error=str(exc), freed_at=_now())
        print(f"gpu_memory_holder: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    from tests.e2e.gpu_memory_d3d12 import D3D12Memory
    status.update(adapter=card.name, vendor_id=f"{card.vendor_id:04x}", device_id=f"{card.device_id:04x}",
                  dedicated_mb=card.dedicated_mib)
    return _hold(args, status, D3D12Memory(luid))


if __name__ == "__main__":
    sys.exit(main())
