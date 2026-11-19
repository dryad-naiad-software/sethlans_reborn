# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Duplicate alerts in a hand-edited ``config.json`` (FR-CK-5): the loader keeps the first alert per id
and per ``(kind, key)``, with one WARNING per process; the method is not forced to CPU for it.
A dismissal of the kept id then removes exactly one alert, and the report sends it once.
"""
import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import choice, os_probe, payload
from tests.unit.worker._card_pages import fake_saved

KEY_A, KEY_B = 'pci:0000:23:00.0', 'pci:0000:03:00.0'
LOG = 'sethlans_worker_agent.config_compute'


def _alert(alert_id, kind='card_lost', key=KEY_A, detail='first'):
    return {'id': alert_id, 'kind': kind, 'key': key, 'name': 'Card', 'detail': detail,
            'raised_at': '2026-11-19T08:00:00+00:00'}


@pytest.fixture(autouse=True)
def _fresh_log_once():
    os_probe.reset_logged_for_tests()
    yield
    os_probe.reset_logged_for_tests()


def _warnings(caplog):
    return [r for r in caplog.records if r.name == LOG and r.levelname == 'WARNING']


class TestDistinctAlerts:

    def test_same_id_keeps_the_first(self, caplog):
        raw = {'method': 'gpu', 'alerts': [_alert('a' * 32), _alert('a' * 32, 'card_changed', KEY_B, 'second')]}
        section = config_compute.parse(raw, {})
        assert [alert['detail'] for alert in section.alerts] == ['first']
        assert section.invalid == () and section.method == 'gpu'
        assert len(_warnings(caplog)) == 1

    def test_same_kind_and_key_keeps_the_first(self, caplog):
        raw = {'alerts': [_alert('a' * 32), _alert('b' * 32, detail='second'), _alert('c' * 32, 'card_changed')]}
        section = config_compute.parse(raw, {})
        assert [alert['id'] for alert in section.alerts] == ['a' * 32, 'c' * 32]
        assert len(_warnings(caplog)) == 1

    def test_warns_once_per_process(self, caplog):
        raw = {'alerts': [_alert('a' * 32), _alert('a' * 32)]}
        for _ in range(3):
            config_compute.parse(raw, {})
        assert len(_warnings(caplog)) == 1

    def test_distinct_alerts_are_kept_silently(self, caplog):
        raw = {'alerts': [_alert('a' * 32), _alert('b' * 32, key=KEY_B)]}
        assert len(config_compute.parse(raw, {}).alerts) == 2
        assert _warnings(caplog) == []


class TestEffect:

    def test_report_sends_it_once_and_one_dismissal_removes_one(self, monkeypatch):
        state, _writes = fake_saved(monkeypatch, {'revision': 0, 'alerts': [
            _alert('a' * 32), _alert('a' * 32, 'card_changed', KEY_B, 'second')]})

        assert [alert['id'] for alert in payload.build()['alerts']] == ['a' * 32]
        removed = choice.apply_manager_dismissals({'dismissed_card_alerts': ['a' * 32]})

        assert [alert['detail'] for alert in removed] == ['first']
        assert state['compute']['alerts'] == []  # the duplicate is not written back
