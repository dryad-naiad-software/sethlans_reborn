# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A reservation's entries in the worker's claim books (render per card D16, FR-PC-RAM-5, FR-PC-DSK-1,
FR-PC-SU-1), for ``reserve.Reserver``.

``register(claimed, books)`` is the last step of ``try_reserve``: the RAM ledger with the need check
9a worked out, the disk ledger with a need of 0, and, just before the reservation is returned, the
start-up watch (the claim is *starting*). A failure undoes the entries already made.
``release(claim_id, slot, books)`` is the reservation's ``on_release``: every entry goes, then the
slot is given back. ``retain_until_restart`` drops the entries itself and keeps the slot.
"""
from dataclasses import dataclass

from sethlans_worker_agent.unreal_executor.disk_ledger import DiskLedger
from sethlans_worker_agent.unreal_executor.ram_ledger import RamLedger
from sethlans_worker_agent.unreal_executor.slot import SlotPool
from sethlans_worker_agent.unreal_executor.startup_watch import StartupWatch


@dataclass(frozen=True)
class Books:
    """Where a ``Reserver``'s claims are entered."""
    slots: SlotPool
    ram: RamLedger
    disk: DiskLedger
    startup: StartupWatch


def register(claimed, books: Books) -> None:
    need = claimed.ram_need
    books.ram.register(claimed.claim_id, need.ws_mb, need.commit_mb)  # FR-PC-RAM-5
    try:
        books.disk.register(claimed.claim_id)  # need 0 until a piece's disk check (FR-PC-DSK-1)
    except BaseException:
        books.ram.unregister(claimed.claim_id)
        raise
    books.startup.mark(claimed.claim_id)  # FR-PC-SU-1: last, nothing after it can fail


def release(claim_id: str, slot: int, books: Books) -> None:
    try:
        try:
            try:
                books.startup.clear(claim_id)
            finally:
                books.ram.unregister(claim_id)
        finally:
            books.disk.unregister(claim_id)
    finally:
        books.slots.give(slot)
