# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
RAM figures of the running claims, for the RAM gate (render per card FR-PC-RAM-5, D9, D16).

Sizes are MB (MiB, bytes // 1,048,576), like the ``unreal.ram_*_mb`` settings and the reported
``ram_<phase>_*_mb`` values. Times are ``time.monotonic()`` seconds.

* **Unreal claims**, keyed by ``claim_id`` (never by job id, D16): ``register(claim_id, need_ws,
  need_commit)`` in ``try_reserve``; the runner, on its own thread, calls ``launch_started``,
  ``publish(claim_id, current_ws, current_commit)`` on every tick, ``mark_failed`` when a read
  fails, and ``launch_ended`` (current figures back to 0: no process runs between launches);
  ``unregister(claim_id)`` from the reservation's ``on_release`` and from ``retain_until_restart``.
  ``set_need(claim_id, need_ws, need_commit)``: the runner lowers the need to the render phase's
  figure once a warm-up's process tree has exited, and raises it back before a (retry) warm-up.
  A publish or mark for a claim that is not registered (a late tick after release) is ignored.
  A good publish, ``launch_started`` and ``launch_ended`` clear ``failed`` (a read that failed as
  the tree exited does not hold the gate shut until release). Publish times come from the ledger's
  clock only (``time.monotonic``; tests inject one).
* **Blender claims**, keyed by job id: ``register_blender(job_id, at)`` at the claim,
  ``unregister_blender(job_id)`` at release. Each counts ``ram_blender_mb`` for
  ``BLENDER_WINDOW_SECONDS`` after its claim time.

``outstanding(...)`` is D9's ``outstanding_x``: for every other Unreal claim ``max(0, need_x -
current_x)``, plus ``blender_mb`` for each Blender claim made in the window. It fails closed, with
``UNAVAILABLE_REASON``, when another claim is ``failed`` or is in a launch and has not published in
the last ``STALE_SECONDS`` (FR-PC-RAM-6).

``_lock`` is a leaf lock (NF-1): nothing else is called while it is held.
"""
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, NamedTuple, Optional

STALE_SECONDS = 10.0
BLENDER_WINDOW_SECONDS = 300.0
UNAVAILABLE_REASON = "RAM figures of a running job are not available; waiting."


@dataclass(frozen=True)
class Entry:
    """One Unreal claim's need and its runner's latest figures."""
    need_ws: int
    need_commit: int
    current_ws: int = 0
    current_commit: int = 0
    published_at: Optional[float] = None
    in_launch: bool = False
    failed: bool = False


class Outstanding(NamedTuple):
    ws: int
    commit: int
    reason: str = ''      # non-empty: the gate fails closed with this reason


def _size(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"a RAM figure must be a non-negative integer, not {value!r}")
    return value


class RamLedger:
    """The ledger; one per worker process (``ledger``), fresh ones in tests."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._claims: Dict[str, Entry] = {}
        self._blender: Dict[int, float] = {}

    # -- Unreal claims ------------------------------------------------------
    def register(self, claim_id: str, need_ws: int, need_commit: int) -> None:
        entry = Entry(_size(need_ws), _size(need_commit))
        with self._lock:
            if claim_id in self._claims:
                raise ValueError(f"claim {claim_id} is already in the RAM ledger")
            self._claims[claim_id] = entry

    def unregister(self, claim_id: str) -> None:
        with self._lock:
            self._claims.pop(claim_id, None)

    def _change(self, claim_id: str, **fields) -> bool:
        with self._lock:
            entry = self._claims.get(claim_id)
            if entry is None:
                return False
            self._claims[claim_id] = replace(entry, **fields)
            return True

    def launch_started(self, claim_id: str) -> bool:
        """A launch began; its first publish is due within ``STALE_SECONDS`` of now."""
        return self._change(claim_id, in_launch=True, current_ws=0, current_commit=0, failed=False,
                            published_at=self._clock())

    def launch_ended(self, claim_id: str) -> bool:
        """The launch's tree exited: no process, so the figures are known again (0) and ``failed`` clears."""
        return self._change(claim_id, in_launch=False, current_ws=0, current_commit=0, failed=False)

    def publish(self, claim_id: str, current_ws: int, current_commit: int) -> bool:
        """The runner's figures for this tick, stamped with the ledger's clock; False when the claim is gone."""
        fields = {'current_ws': _size(current_ws), 'current_commit': _size(current_commit), 'failed': False,
                  'published_at': self._clock()}
        return self._change(claim_id, **fields)

    def set_need(self, claim_id: str, need_ws: int, need_commit: int) -> bool:
        """The claim's need from now on; False when the claim is gone."""
        return self._change(claim_id, need_ws=_size(need_ws), need_commit=_size(need_commit))

    def mark_failed(self, claim_id: str) -> bool:
        return self._change(claim_id, failed=True)

    # -- Blender claims -----------------------------------------------------
    def register_blender(self, job_id: int, at: Optional[float] = None) -> None:
        when = self._clock() if at is None else at
        with self._lock:
            self._blender[job_id] = when

    def unregister_blender(self, job_id: int) -> None:
        with self._lock:
            self._blender.pop(job_id, None)

    # -- reading ------------------------------------------------------------
    def snapshot(self) -> Dict[str, Entry]:
        with self._lock:
            return dict(self._claims)

    def has_claims(self) -> bool:
        with self._lock:
            return bool(self._claims) or bool(self._blender)

    def outstanding(self, blender_mb: int, exclude: Optional[str] = None, now: Optional[float] = None) -> Outstanding:
        """D9's outstanding physical and commit MB of every claim but ``exclude``."""
        now = self._clock() if now is None else now
        with self._lock:
            claims = [entry for claim_id, entry in self._claims.items() if claim_id != exclude]
            recent_blender = sum(1 for at in self._blender.values() if now - at <= BLENDER_WINDOW_SECONDS)
        ws = commit = _size(blender_mb) * recent_blender
        for entry in claims:
            stale = entry.in_launch and (entry.published_at is None or now - entry.published_at > STALE_SECONDS)
            if entry.failed or stale:
                return Outstanding(0, 0, UNAVAILABLE_REASON)
            ws += max(0, entry.need_ws - entry.current_ws)
            commit += max(0, entry.need_commit - entry.current_commit)
        return Outstanding(ws, commit)


ledger = RamLedger()


def reset_for_tests() -> None:
    """Empty the worker's ledger (test isolation only)."""
    with ledger._lock:
        ledger._claims.clear()
        ledger._blender.clear()
