# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The real PDH bindings, Windows only (GPU memory reader FR-GT-2, U-13).

Open, one collect and close, under 1 s, on the main thread and then on a
second thread with the same query in turn; and one real backend sample whose
``per_pid_shared`` is filled when the process ``Shared Usage`` counter opens
(per machine memory test U-3). No card value is asserted; skipped when the
GPU memory counters are not available.
"""
import os
import sys
import threading
import time

import pytest

from sethlans_worker_agent.gpu_memory import win_backend, win_pdh

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows performance counters')


@pytest.fixture
def query():
    try:
        opened = win_pdh.PdhQuery(win_backend.PATHS)
    except win_pdh.PdhError as exc:
        pytest.skip(f'GPU memory counters unavailable: {exc}')
    yield opened
    opened.close()


def _collect_in_time(query):
    started = time.monotonic()
    data = query.collect()
    assert time.monotonic() - started < 1.0
    return data


def test_collect_on_the_main_thread_then_on_a_second_thread(query):
    first = _collect_in_time(query)
    if first is None or first.get(win_backend.ADAPTER_PATH) is None:
        pytest.skip('the GPU memory counters gave no reading here')
    results = []
    worker = threading.Thread(target=lambda: results.append(_collect_in_time(query)), daemon=True)  # a hang fails
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert results and set(results[0]) == set(win_backend.PATHS)


def test_a_real_sample_fills_per_pid_shared_when_the_shared_counter_opens():
    """U-3: this node's Windows build has the process ``Shared Usage`` counter and a real sample fills
    ``per_pid_shared`` (a dict keyed by the card keys). Skipped, with the reason, when the counters give no
    reading or the backend had to open without ``Shared Usage``."""
    opened = []

    def factory(paths):
        query = win_pdh.PdhQuery(paths)
        opened.append(tuple(paths))
        return query

    backend = win_backend.PdhBackend(query_factory=factory, cross_check=lambda reading: None)
    try:
        if not backend.open():
            pytest.skip('the GPU memory counters gave no reading here')
        if opened != [win_backend.PATHS]:
            pytest.skip('the process Shared Usage counter could not be opened on this Windows build (U-3)')
        reading = backend.sample(frozenset({os.getpid()}))
    finally:
        backend.close()
    if reading is None:
        pytest.skip('the GPU memory counters gave no sample here')
    keys = {gpu.card_key for gpu in reading.gpus if gpu.card_key is not None}
    assert isinstance(reading.per_pid_shared, dict)
    assert set(reading.per_pid_shared) == set(reading.per_pid) == keys
    assert all(isinstance(per_pid, dict) for per_pid in reading.per_pid_shared.values())
