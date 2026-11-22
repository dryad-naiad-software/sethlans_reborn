# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Two-card harness for the "Unreal on its card" tests (gpu-memory-reader
chunk 7: D22, FR-GU-4, FR-GU-10, FR-GU-11).

Two made-up identical cards of one brand (the brand rule, render per card
D7; identical cards are the case only the card key tells apart). D8 orders
equal cards by PCI address, so ``FIRST`` (the lower address) comes first.
The reading index and the DXGI index differ on purpose (FR-GN-5), and the
fake engine logs each card at its own DXGI index, so ``adapter_log`` checks
the card the launch was given. The real ``Reserver`` runs over local
deps, so the gate-to-launch hand-off goes through ``try_reserve``.

The sampler raises the job's card (the card of the latest ``choose``) by
``JOB_DELTA_MB`` while a fake UE runs, and every other card by ``NOISE_MB``
(another program on it). Its readings go through the real
``vram.take_reading`` cut to ``cut_to`` (the Unreal cards in use), unless
``cut_to`` is ``None``.
"""
import dataclasses
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace

from sethlans_worker_agent.cards.identity import key_address, key_sort_value
from sethlans_worker_agent.executors.base import ExecutionContext
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu
from sethlans_worker_agent.project_archive.reachability import resolve_share_path
from sethlans_worker_agent.unreal_executor import adapter_choice, executor, reserve, reserve_local, vram
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict
from tests.integration.worker.project_archive._helpers import SHARE_ROOT, AlwaysReachable
from shared.unreal.card_kind import card_kind
from tests.integration.worker.unreal_executor._harness import FakeSampler, Harness


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    total_mb: int
    idle_mb: int
    reading_index: int
    dxgi_index: int
    vendor_id: int
    device_id: int
    revision_id: int

    def adapter_line(self) -> list:
        return [self.name, f'{self.vendor_id:04x}', f'{self.device_id:04x}', f'{self.revision_id:04x}']

    @property
    def kind(self) -> str:
        return card_kind(self.vendor_id, self.device_id, self.revision_id)


FIRST = Card('pci:0000:0a:00.0', 'Example Graphics 20G', 20464, 1000, 1, 3, 0x1002, 0x7448, 0x00)  # D8's card
SECOND = Card('pci:0000:2c:00.0', 'Example Graphics 20G', 20464, 500, 0, 1, 0x1002, 0x7448, 0x00)
CARDS = (SECOND, FIRST)   # reading order
JOB_DELTA_MB = 2000
NOISE_MB = 6000
ADAPTERS = {str(card.dxgi_index): card.adapter_line() for card in CARDS}


def raw_reading(used=None, keyless=(), taken_at=None) -> GpuMemoryReading:
    """A ``pdh``-like reading of both cards (``used``: key -> MB, default idle)."""
    used = used or {}
    gpus = tuple(make_gpu(card.reading_index, card.name, card.total_mb, used.get(card.key, card.idle_mb), None,
                          card_key=None if card.key in keyless else card.key) for card in CARDS)
    return GpuMemoryReading(time.monotonic() if taken_at is None else taken_at, 'pdh', gpus,
                            measures_card=True, measures_process=False)


def with_kinds(reading):
    """The kinds ``vram`` gives the Unreal cards in use from the card inventory (D1), here from ``CARDS``."""
    if reading is None:
        return None
    kinds = {key_address(card.key): card.kind for card in CARDS}
    return dataclasses.replace(reading, gpus=tuple(dataclasses.replace(gpu, card_kind=kinds.get(key_address(gpu.card_key)))
                                                   for gpu in reading.gpus))


class TwoCardSampler(FakeSampler):

    def __init__(self, exe, job_key, cut_to, keyless=()):
        super().__init__(exe)
        self.job_key, self.cut_to, self.keyless = job_key, cut_to, frozenset(keyless)
        self._lock = threading.Lock()
        self.seen = set()   # card keys of every reading handed out

    def _used(self) -> dict:
        if not self._busy():
            return {}
        job = self.job_key()
        return {card.key: card.idle_mb + (JOB_DELTA_MB if card.key == job else NOISE_MB) for card in CARDS}

    def reading(self):
        raw = raw_reading(self._used(), self.keyless)
        out = raw
        if self.cut_to is not None:
            addresses = frozenset(key_address(key) for key in self.cut_to)
            out = vram.take_reading(0, read=lambda pids: raw, cards=lambda: addresses)
        out = with_kinds(out)  # a keyless card gets no kind (as in vram: kinds are by address)
        with self._lock:
            self.seen.update(gpu.card_key for gpu in (out.gpus if out is not None else ()))
        return out


class TwoCardHarness(Harness):
    """``ticked``: the Unreal cards ``choose`` may use; ``reserve_keys``: what the card code reports at reserve."""

    def __init__(self, tmp_path, monkeypatch, ticked=(FIRST, SECOND), cut_to=(FIRST.key, SECOND.key), keyless=(),
                 opts=None):
        super().__init__(tmp_path, monkeypatch, dict(opts or {}, adapters=ADAPTERS))
        self.ticked = tuple(ticked)
        self.kind = FIRST.kind  # both cards are one kind: one machine result serves both (AC-2)
        self.reserve_keys = [card.key for card in self.ticked]
        self.gated_key = None
        self.sampler = TwoCardSampler(self.exe, self.job_key, cut_to, keyless)
        self.executor = executor.UnrealExecutor(reserve_deps=self.reserve_deps(), run_deps=self.run_deps())

    def job_key(self):
        """The card the fake UE loads: the latest choice; before one (a pilot's sampled prep), the gated card."""
        return self.choices[-1][2] if self.choices else self.gated_key

    def choose_adapter(self, job_id, key=None, claim_id=None):
        """``adapter_choice.choose`` over the ticked cards: D8, or the ``key`` card only; ``LocalConflict`` else."""
        cards = [card for card in self.ticked if key is None or card.key == key]
        if not cards:
            raise LocalConflict('local_conflict', adapter_choice.KEPT_CARD_REASON)
        card = min(cards, key=lambda c: (-c.total_mb, key_sort_value(c.key)))
        self.choices.append((job_id, key, card.key))
        return adapter_choice.Choice(job_id, card.key, card.name, card.dxgi_index, card.vendor_id, card.device_id,
                                     card.revision_id)

    def reserve_deps(self) -> reserve.ReserveDeps:
        return reserve.ReserveDeps(
            can_take=lambda: True, status=lambda: (True, ''), installs=lambda: (self.install,),
            sampler=lambda: self.sampler, port_ok=lambda slot: SimpleNamespace(ok=True, message=''),
            isolation_ok=lambda: True, share_state=lambda: SimpleNamespace(root=SHARE_ROOT, reachable=True),
            entry_ready=lambda sha: False, reachability=AlwaysReachable, resolve=resolve_share_path,
            budget=lambda: None, card_keys=lambda: tuple(self.reserve_keys),
            ram_readers=reserve_local.RamReaders(lambda: 1 << 20, lambda: 1 << 20))  # not the host's RAM (D9)

    def token_free(self) -> bool:
        """Every Unreal slot is free (the slot pool replaced the token, FR-PC-SLOT-1)."""
        return self.executor._reserver.slots.free() == tuple(range(16))

    def run(self, job_data, reservation):
        job_data['_renderer_reservation'] = reservation
        self.gated_key = reservation.gate.card_key
        try:
            result = self.executor.execute(job_data, ExecutionContext(None, None, None, threading.Event()))
            if result.after_report is not None:
                result.after_report()
            return result
        finally:
            reservation.release()

    def log(self, name) -> str:
        return (next(self.cache.layout.j.iterdir()) / 'logs' / name).read_text(encoding='utf-8')

    def nothing_prepared(self) -> bool:
        return all(not path.exists() or not any(path.iterdir()) for path in (self.cache.layout.j, self.cache.layout.w))
