# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Two Unreal jobs at once on a two-card worker (render per card chunk 4b: D1, D4, D5, D16, FR-PC-SEL-1, -3).

The real reserver gates both jobs over a real ``WorkerCapacity``: the first
takes D8's card (the lower address); its hold (as ``job_claim`` takes it) makes the second
job's gate skip that card and take the next free card it fits. Both jobs then
run in their own threads through the real executor with the fake engine. A
watcher sees the two fake renders alive at the same time, each launched with
its own ``-graphicsadapter`` and its own slot's Zen port, and each claim's
card choice is kept under its own claim id until its job ends.
"""
import sys
import threading

import psutil
import pytest

from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.unreal_executor import adapter_choice
from tests.integration.worker.unreal_executor._harness import summary
from tests.integration.worker.unreal_executor._two_cards import FIRST, SECOND, TwoCardHarness
from tests.unit.worker.executors._fakes import profile

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects, Windows command lines')

PORT_FLAG = '-ini:Engine:[Zen.AutoLaunch]:DesiredPort='


class RenderWatch:
    """Samples the fake engine's processes; keeps every set of renders seen alive together."""

    def __init__(self, exe):
        self.exe, self.together, self.stop = str(exe).lower(), set(), threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    @staticmethod
    def _flag(cmdline, prefix):
        return next((arg[len(prefix):].split(',')[0] for arg in cmdline if arg.startswith(prefix)), None)

    def _renders(self):
        found = []
        for proc in psutil.process_iter(['exe', 'cmdline']):
            cmdline = proc.info.get('cmdline') or []
            if (proc.info.get('exe') or '').lower() == self.exe and '-run=pythonscript' not in cmdline:
                adapter = self._flag(cmdline, '-graphicsadapter=')
                if adapter is not None:
                    found.append((int(adapter), int(self._flag(cmdline, PORT_FLAG) or 0)))
        return frozenset(found)

    def _watch(self):
        while not self.stop.wait(0.05):
            alive = self._renders()
            if len(alive) > 1:
                self.together.add(alive)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(5)


def _hold(cap, harness, job):
    """``try_reserve`` and the claim's hold of the reservation's card (``job_claim._reserve_slot``)."""
    reservation = harness.executor.try_reserve(job)
    assert reservation is not None
    assert cap.reserve_unreal_card(job['id'], reservation.card_key) is not None
    return reservation


def test_two_jobs_render_at_once_each_on_its_own_card_and_slot(tmp_path, monkeypatch):
    h = TwoCardHarness(tmp_path, monkeypatch, opts={'render_sleep': 4, 'phase_sleep': 1})
    cap = WorkerCapacity(profile(gpu_slot_count=0))
    h.executor._reserver.deps.capacity = lambda: cap
    seen_claims = {}
    choose = h.choose_adapter

    def choose_per_claim(job_id, key=None, claim_id=None):
        seen_claims.setdefault(job_id, set()).add(claim_id)
        choice = choose(job_id, key=key, claim_id=claim_id)
        adapter_choice._current[claim_id] = choice  # what adapter_choice.choose keeps (FR-PC-SEL-3)
        return choice
    h.executor._run_deps.choose_adapter = choose_per_claim

    first, second = h.job(91), h.job(92)
    a = _hold(cap, h, first)
    b = _hold(cap, h, second)
    assert (a.card_key, b.card_key) == (FIRST.key, SECOND.key)  # D4: the held card is skipped
    assert (a.slot, b.slot) == (0, 1) and a.claim_id != b.claim_id

    results = {}

    def run(job, reservation):
        try:
            results[job['id']] = h.run(job, reservation)
        finally:
            cap.release_slot(job['id'])
    threads = [threading.Thread(target=run, args=pair) for pair in ((first, a), (second, b))]
    with RenderWatch(h.exe) as watch:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
    assert not any(thread.is_alive() for thread in threads)

    for job_id in (91, 92):
        assert results[job_id].success and not results[job_id].released, results[job_id].error_message
    # Both renders ran together, each on its own card's DXGI index and its own slot's Zen port (D5).
    assert frozenset({(FIRST.dxgi_index, h.port), (SECOND.dxgi_index, h.port + 1)}) in watch.together
    assert summary(results[91])['card'] == FIRST.key and summary(results[92])['card'] == SECOND.key
    assert summary(results[91])['slot'] == '0' and summary(results[92])['slot'] == '1'
    assert {job_id: card for job_id, _key, card in h.choices} == {91: FIRST.key, 92: SECOND.key}
    # D16: each job's choices went under its own claim, and each was forgotten when its job ended.
    assert seen_claims == {91: {a.claim_id}, 92: {b.claim_id}}
    assert adapter_choice.all_current() == ()
    assert h.token_free() and cap.holds_snapshot() == {}
