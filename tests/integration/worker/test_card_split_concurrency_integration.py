# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Concurrent split-mode reservations on cards from the saved choice (card selection, chunk 2).

In the style of ``test_gpu_lock_concurrency.py``, but the capacity comes
from the real choice path (``install_card_choice``: cards A and C ticked,
card B unticked) and the checks are on card keys (FR-CL-3): two jobs in
split mode each hold their own card, the keys of live reservations never
overlap, card B is never reserved, and the render scripts generated from
the concurrent reservations enable disjoint cards (FR-CB-1).
"""

import threading

from sethlans_worker_agent.render_script import generate_render_config_script

from ._card_choice_harness import FakeBlender, ID_A, ID_C, KEY_A, KEY_B, KEY_C, install_card_choice


def _reserve_together(capacity, job_ids, device_pref='GPU'):
    """Reserve for every job id from its own thread, all released at once by a barrier."""
    results = {}
    lock = threading.Lock()
    barrier = threading.Barrier(len(job_ids), timeout=10)

    def reserve(job_id):
        barrier.wait()
        reservation = capacity.reserve_for_job(job_id, device_pref)
        with lock:
            results[job_id] = reservation

    threads = [threading.Thread(target=reserve, args=(job_id,)) for job_id in job_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    return results


class TestCardSplitConcurrencyIntegration:

    def test_two_jobs_hold_their_own_card(self, mocker):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A, KEY_C))

        results = _reserve_together(capacity, [100, 200])

        first, second = results[100], results[200]
        assert len(first.card_keys) == 1 and len(second.card_keys) == 1
        assert set(first.card_keys).isdisjoint(second.card_keys)
        assert set(first.card_keys) | set(second.card_keys) == {KEY_A, KEY_C}
        for reservation in (first, second):
            # Each reservation carries its own copy of the cards in use (C2).
            assert reservation.cards == capacity.profile.cards
            assert [card.key for card in reservation.job_cards] == list(reservation.card_keys)
        assert capacity.gpu_assignments_snapshot() == {first.card_keys[0]: 100, second.card_keys[0]: 200}

    def test_many_contenders_get_one_card_each_and_never_the_unticked_one(self, mocker):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A, KEY_C))

        results = _reserve_together(capacity, list(range(1, 17)))

        won = {job_id: r for job_id, r in results.items() if r is not None}
        assert len(won) == 2
        keys = [r.card_keys[0] for r in won.values()]
        assert sorted(keys) == [KEY_A, KEY_C]
        assert KEY_B not in capacity.gpu_assignments_snapshot()
        assert capacity.free_device_prefs() == []

    def test_scripts_of_concurrent_reservations_enable_disjoint_cards(self, mocker, monkeypatch):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A, KEY_C))
        results = _reserve_together(capacity, [1, 2])
        blender = FakeBlender(monkeypatch)

        enabled = {}
        for job_id, reservation in results.items():
            script = generate_render_config_script(job_id, 'CYCLES', 'GPU', {}, reservation=reservation)
            prefs, scene, code = blender.run_script(script)
            assert code == 0 and scene.cycles.device == 'GPU'
            enabled[reservation.card_keys[0]] = prefs.enabled()

        assert enabled == {KEY_A: [('CUDA', ID_A)], KEY_C: [('CUDA', ID_C)]}

    def test_live_reservations_never_share_a_card_under_churn(self, mocker):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A, KEY_C))
        errors = []
        barrier = threading.Barrier(6, timeout=10)

        def churn(base):
            try:
                barrier.wait()
                for i in range(40):
                    job_id = base * 1000 + i
                    reservation = capacity.reserve_for_job(job_id, 'GPU')
                    if reservation is None:
                        continue
                    key = reservation.card_keys[0]
                    owner = capacity.gpu_assignments_snapshot().get(key)
                    if key == KEY_B or owner != job_id:
                        errors.append((job_id, key, owner))
                    capacity.release_slot(job_id)
            except BaseException as exc:  # noqa: BLE001 - surface in the main thread
                errors.append(exc)

        threads = [threading.Thread(target=churn, args=(n,)) for n in range(1, 7)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            assert not thread.is_alive()

        assert errors == []
        assert capacity.gpu_assignments_snapshot() == {}
        assert capacity.has_no_reservations()
