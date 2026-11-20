# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The real PDH bindings, Windows only (GPU memory reader FR-GT-2, U-13).

Open, one collect and close, under 1 s, on the main thread and then on a
second thread with the same query in turn. No card value is asserted;
skipped when the GPU memory counters are not available.
"""
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
