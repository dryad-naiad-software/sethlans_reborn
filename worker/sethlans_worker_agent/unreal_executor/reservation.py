# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal job's reservation (Unreal foundation FR-UE-1; render per card FR-PC-SLOT-1, D16,
FR-PC-RAM-5), handed out by ``reserve.Reserver.try_reserve`` (re-exported there).

It carries what ``execute`` needs from the checks, the job's slot, the card it holds
(``card_key``, D1: ``job_claim`` holds it; default the gate's card) and its ``claim_id`` (uuid4
hex, D16: the ledgers' key, never the job id). ``ram`` is the RAM ledger the claim is registered
in (``None``: not registered); ``ram_target()`` is where the runner publishes the claim's figures;
``ram_need`` is the need the RAM gate registered (``job_ram.RamNeed``, FR-PC-RAM-4). ``disk`` is the
disk ledger the claim is registered in (FR-PC-DSK-1); ``disk_target()`` is the job thread's handle on
it. ``startup`` is the start-up watch the claim is marked *starting* in (FR-PC-SU-1); the job thread
calls ``startup_ended()`` once the render phase's first output file is there. ``on_release`` drops
the claim from the ledgers and the watch and gives the slot back; ``retain_until_restart`` drops it
from them and keeps the slot (``release()`` is a no-op after it, FR-PC-SU-1).
"""
import logging
import uuid
from typing import Callable, Optional

from sethlans_worker_agent.executors.base import RendererReservation
from sethlans_worker_agent.unreal_executor.disk_ledger import DiskTarget
from sethlans_worker_agent.unreal_executor.runner_ram import RamTarget

logger = logging.getLogger(__name__)


class UnrealReservation(RendererReservation):
    """The slot plus what ``execute`` needs from the checks."""

    def __init__(self, params, install, gate, on_release: Callable[[], None], card_keys: tuple = (),
                 slot: int = 0, claim_id: str = '', ram=None, card_key: Optional[str] = None, disk=None,
                 ram_need=None, startup=None):
        super().__init__(on_release=on_release)
        self.params = params
        self.install = install
        self.gate = gate
        self.card_keys = tuple(card_keys)  # the Unreal cards in use at reserve time (C-27 b)
        self.card_key = card_key if card_key is not None else getattr(gate, 'card_key', None)  # the held card (D1)
        self.slot = slot  # the job's Unreal slot (FR-PC-SLOT-1); its release gives it back
        self.claim_id = claim_id or uuid.uuid4().hex  # D16: the ledgers' key, never the job id
        self.ram = ram  # the RAM ledger the claim is registered in (None: not registered)
        self.ram_need = ram_need  # job_ram.RamNeed the RAM gate registered (None: not gated here)
        self.disk = disk  # the disk ledger the claim is registered in (None: not registered)
        self.startup = startup  # the start-up watch the claim is marked in (None: not marked)

    def ram_target(self) -> Optional[RamTarget]:
        """Where the runner publishes this claim's RAM figures (FR-PC-RAM-2); None when not registered."""
        if self.ram is None:
            return None
        need = self.ram_need  # a warm-up raises the need to the full figure, its end lowers it (FR-PC-RAM-4)
        if need is None:
            return RamTarget(self.ram, self.claim_id)
        return RamTarget(self.ram, self.claim_id, (need.ws_mb, need.commit_mb),
                         (need.render_ws_mb, need.render_commit_mb))

    def disk_target(self) -> Optional[DiskTarget]:
        """The claim's handle on the disk ledger (FR-PC-DSK-1); None when not registered."""
        return DiskTarget(self.disk, self.claim_id) if self.disk is not None else None

    def startup_ended(self) -> None:
        """The render wrote its first output file: the claim is no longer starting (FR-PC-SU-1)."""
        if self.startup is not None and self.startup.clear(self.claim_id):
            logger.info("Unreal job %s wrote its first output file; its start-up is over.",
                        getattr(self.params, 'job_id', None))

    def retain_until_restart(self) -> None:
        """Keep the slot until restart; the ledger and start-up entries go (release is a no-op from now on)."""
        super().retain_until_restart()
        try:
            if self.startup is not None:
                self.startup.clear(self.claim_id)
        finally:
            try:
                if self.ram is not None:
                    self.ram.unregister(self.claim_id)
            finally:
                if self.disk is not None:
                    self.disk.unregister(self.claim_id)
