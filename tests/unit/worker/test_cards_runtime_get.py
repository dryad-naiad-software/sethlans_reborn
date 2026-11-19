# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
AC-41 / FR-CW-4: the anonymous ``GET /api/cards/`` is read-only and cached.

- Read-only (S1): with a ticked card missing it raises no alert, stops no
  job, requests no rebuild and leaves ``config.json`` as it was (a real file
  under ``tmp_path``).
- Cache: at most one probe per 5 s, measured from the end of the probe, on a
  fake clock (``cards_cache.time`` is replaced, never slept on).
- One probe at a time: callers arriving while a probe runs get its result.
- The POST's fresh probe refreshes the cache.
"""
import json
import threading
import types

import pytest

from sethlans_worker_agent import capacity, config_compute, config_store
from sethlans_worker_agent.cards import choice, in_use, store
from sethlans_worker_agent.web_ui import cards_cache, cards_view
from tests.unit.worker._card_choice import KEY_RTX_A, KEY_XT_23, inv, rtx_a, xt_03, xt_23
from tests.unit.worker._card_pages import RUNTIME_GET, RUNTIME_POST, body, call, fake_probes, fake_saved

REAL_READ_RAW = config_compute._read_raw  # taken before the autouse fixture replaces it
WAIT = 5.0  # upper bound for every Event wait and thread join; never reached when the code is right


class FakeClock:
    """``time.monotonic`` stand-in; remembers which threads asked."""

    def __init__(self):
        self.now = 0.0
        self.callers = []
        self._cond = threading.Condition()

    def monotonic(self):
        with self._cond:
            self.callers.append(threading.current_thread().name)
            self._cond.notify_all()
            return self.now

    def wait_for_callers(self, *names):
        with self._cond:
            return self._cond.wait_for(lambda: all(name in self.callers for name in names), timeout=WAIT)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(cards_cache, 'time', types.SimpleNamespace(monotonic=fake.monotonic))
    return fake


@pytest.fixture(autouse=True)
def _open(setup_complete):
    yield


class TestReadOnly:

    @pytest.fixture
    def config_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv('SETHLANS_WORKER_DATA_DIR', str(tmp_path))
        monkeypatch.setattr(config_store, 'load_system_config', lambda: {})
        monkeypatch.setattr(config_compute, '_read_raw', REAL_READ_RAW)
        compute = {'method': 'gpu', 'gpu_mode': 'split', 'revision': 3, 'alerts': [],
                   'cards': [in_use.record_of(rtx_a()), in_use.record_of(xt_23())]}
        path = tmp_path / 'config.json'
        path.write_text(json.dumps({'compute': compute}), encoding='utf-8')
        return path

    @pytest.fixture
    def forbidden(self, monkeypatch):
        """Every action a GET must never take; each records its calls."""
        calls = []

        def recorder(name):
            return lambda *args, **kwargs: calls.append(name)
        for module, name in ((store, 'add_alert'), (store, 'request_card_stop'), (choice, '_request_rebuild'),
                             (capacity, 'request_rebuild'), (config_store, 'set_many'), (config_store, 'save')):
            monkeypatch.setattr(module, name, recorder(f'{module.__name__}.{name}'), raising=False)
        return calls

    def test_ten_gets_with_a_missing_card_change_nothing(self, monkeypatch, config_file, forbidden):
        before = config_file.read_bytes()
        probes = fake_probes(monkeypatch, inv(xt_03(), xt_23()))
        for _ in range(10):
            status, answer = call(RUNTIME_GET)
            assert status == 200
            assert answer['ticked'] == [KEY_RTX_A, KEY_XT_23] and answer['revision'] == 3
            assert KEY_RTX_A not in [entry['key'] for entry in answer['cards']]
        assert len(probes) == 1
        assert forbidden == []
        assert config_file.read_bytes() == before
        assert sorted(p.name for p in config_file.parent.iterdir()) == ['config.json']


class TestCache:

    def test_ten_gets_within_five_seconds_run_one_probe(self, monkeypatch, clock):
        fake_saved(monkeypatch)
        probes = fake_probes(monkeypatch)
        for step in range(10):
            clock.now = step * 0.5
            assert call(RUNTIME_GET)[0] == 200
        assert len(probes) == 1

    def test_probe_older_than_five_seconds_is_replaced(self, monkeypatch, clock):
        fake_saved(monkeypatch)
        probes = fake_probes(monkeypatch, inv(xt_03(), xt_23()), inv(xt_23()))
        assert len(call(RUNTIME_GET)[1]['cards']) == 2
        clock.now = 5.0
        assert len(call(RUNTIME_GET)[1]['cards']) == 2 and len(probes) == 1
        clock.now = 5.01
        assert len(call(RUNTIME_GET)[1]['cards']) == 1 and len(probes) == 2
        clock.now = 9.9
        call(RUNTIME_GET)
        assert len(probes) == 2

    def test_cache_age_counts_from_the_probe_end(self, monkeypatch, clock):
        handed = []

        def slow_probe():  # the probe "takes" 4 s
            handed.append(cards_view.Probe(inv(xt_23()), []))
            clock.now += 4.0
            return handed[-1]
        monkeypatch.setattr(cards_view, 'probe_now', slow_probe)
        cards_cache.cached_probe()
        clock.now = 9.0  # 5 s after it finished, 9 s after it started
        cards_cache.cached_probe()
        assert len(handed) == 1
        clock.now = 9.01
        cards_cache.cached_probe()
        assert len(handed) == 2

    def test_probe_slower_than_the_cache_age_is_not_rerun_back_to_back(self, monkeypatch, clock):
        handed = []

        def slower_probe():  # "takes" 7 s, longer than the cache age
            handed.append(cards_view.Probe(inv(xt_23()), []))
            clock.now += 7.0
            return handed[-1]
        monkeypatch.setattr(cards_view, 'probe_now', slower_probe)
        cards_cache.cached_probe()
        clock.now += 0.5
        cards_cache.cached_probe()
        assert len(handed) == 1

    def test_saved_choice_is_read_on_every_get(self, monkeypatch, clock):
        state, _writes = fake_saved(monkeypatch)
        probes = fake_probes(monkeypatch)
        assert call(RUNTIME_GET)[1]['revision'] == 0
        state['compute']['revision'] = 4
        assert call(RUNTIME_GET)[1]['revision'] == 4
        assert len(probes) == 1


class TestOneProbeAtATime:

    def test_callers_during_a_slow_probe_share_its_result(self, monkeypatch, clock):
        entered, release = threading.Event(), threading.Event()
        handed = []

        def slow_probe():  # holds the probe lock until released; "takes" 10 s
            handed.append(cards_view.Probe(inv(xt_23()), []))
            entered.set()
            assert release.wait(WAIT)
            clock.now += 10.0
            return handed[-1]
        monkeypatch.setattr(cards_view, 'probe_now', slow_probe)
        results = {}

        def get(name):
            results[name] = cards_cache.cached_probe()
        first = threading.Thread(target=get, args=('first',), name='first', daemon=True)
        first.start()
        assert entered.wait(WAIT)
        others = [threading.Thread(target=get, args=(name,), name=name, daemon=True) for name in ('second', 'third')]
        for thread in others:
            thread.start()
        assert clock.wait_for_callers('second', 'third')  # both arrived while the probe runs
        release.set()
        for thread in [first, *others]:
            thread.join(WAIT)
            assert not thread.is_alive()
        assert len(handed) == 1
        assert results['second'] is results['first'] and results['third'] is results['first']


class TestPostRefreshesCache:

    def test_post_probe_is_served_to_the_next_get(self, monkeypatch, fresh_shutdown_event, bearer_header, clock):
        fake_saved(monkeypatch)
        probes = fake_probes(monkeypatch, inv(xt_03(), xt_23()), inv(xt_23()))
        assert len(call(RUNTIME_GET)[1]['cards']) == 2
        status, _answer = call(RUNTIME_POST, 'POST', body(shown=['0000:23:00.0']), bearer_header)
        assert status == 200 and len(probes) == 2
        answer = call(RUNTIME_GET)[1]
        assert [entry['key'] for entry in answer['cards']] == [KEY_XT_23] and answer['revision'] == 1
        assert len(probes) == 2

    def test_post_probes_even_with_a_fresh_cache(self, monkeypatch, clock):
        fake_saved(monkeypatch)
        probes = fake_probes(monkeypatch)
        first = cards_cache.cached_probe()
        assert cards_cache.fresh_probe() is not first
        assert len(probes) == 2 and cards_cache.cached_probe() is probes[1]
