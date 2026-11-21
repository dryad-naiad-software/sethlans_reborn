# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal job's reservation (Unreal foundation FR-UE-1; render per card FR-PC-SLOT-1, D16,
FR-PC-RAM-5), handed out by ``reserve.Reserver.try_reserve`` (re-exported there).

It carries what ``execute`` needs from the checks, the job's slot, and its ``claim_id`` (uuid4
hex, D16: the ledgers' key, never the job id). ``ram`` is the RAM ledger the claim is registered
in (``None``: not registered); ``ram_target()`` is where the runner publishes the claim's figures.
``on_release`` drops the ledger entry and gives the slot back; ``retain_until_restart`` drops the
ledger entry and keeps the slot (``release()`` is a no-op after it, FR-PC-SU-1).
"""
import uuid
from typing import Callable, Optional

from sethlans_worker_agent.executors.base import RendererReservation
from sethlans_worker_agent.unreal_executor.runner_ram import RamTarget


class UnrealReservation(RendererReservation):
    """The slot plus what ``execute`` needs from the checks."""

    def __init__(self, params, install, gate, on_release: Callable[[], None], card_keys: tuple = (),
                 slot: int = 0, claim_id: str = '', ram=None):
        super().__init__(on_release=on_release)
        self.params = params
        self.install = install
        self.gate = gate
        self.card_keys = tuple(card_keys)  # held by the exclusive reservation (job_claim, C-27 b)
        self.slot = slot  # the job's Unreal slot (FR-PC-SLOT-1); its release gives it back
        self.claim_id = claim_id or uuid.uuid4().hex  # D16: the ledgers' key, never the job id
        self.ram = ram  # the RAM ledger the claim is registered in (None: not registered)

    def ram_target(self) -> Optional[RamTarget]:
        """Where the runner publishes this claim's RAM figures (FR-PC-RAM-2); None when not registered."""
        return RamTarget(self.ram, self.claim_id) if self.ram is not None else None

    def retain_until_restart(self) -> None:
        """Keep the slot until restart; the RAM ledger entry goes (release is a no-op from now on)."""
        super().retain_until_restart()
        if self.ram is not None:
            self.ram.unregister(self.claim_id)
