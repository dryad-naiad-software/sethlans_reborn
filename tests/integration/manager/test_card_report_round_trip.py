# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract round trip of a card alert dismissal, worker -> manager -> worker
(worker card selection FR-CR-1, FR-CR-3, FR-CR-6; AC-15, AC-17).

The worker's real ``cards`` report (``_card_contract_harness``: a real
``config.json``, faked probes) goes through the real heartbeat endpoint
with a worker token and is stored unchanged. An admin dismisses one alert
with the real action; the next heartbeat answer is fed to the worker's real
``choice.apply_manager_dismissals``, which removes exactly that alert from
``config.json`` and requests one rebuild; the heartbeat after that (a report
without the alert) prunes the id on the manager.

The first test drives the two halves by hand; the second drives the
worker's own ``system_monitor`` registration and heartbeat, with only the
HTTP call routed to the Django test client.
"""
import json

import pytest

from sethlans_worker_agent import api_handler, config, system_monitor
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice
from workers.models import Worker

from ._card_contract_harness import ALERT_IDS, KEY_HELD, machine_compute, stored_form, worker_machine

HB = '/api/heartbeat/'
HOST = 'contract-host'
DISMISSED = ALERT_IDS['card_lost']  # the alert that holds KEY_HELD


def _beat(client, report, **extra):
    """One heartbeat with ``report`` as ``cards``; the answer as the worker receives it (JSON)."""
    resp = client.post(HB, {'hostname': HOST, 'cards': report, **extra}, format='json')
    assert resp.status_code == 200, resp.data
    return json.loads(resp.content)


def _dismiss(admin_client, worker, alert_id=DISMISSED):
    resp = admin_client.post(f'{HB}{worker.pk}/dismiss-card-alert/', {'alert_id': alert_id}, format='json')
    assert resp.status_code == 200, resp.data
    assert resp.data['card_alert_dismissals'] == [alert_id]


def _row(worker):
    return Worker.objects.get(pk=worker.pk)


def _alert_ids(container):
    return [entry['id'] for entry in container['alerts']]


def _without_alerts(compute):
    return {name: value for name, value in compute.items() if name != 'alerts'}


@pytest.fixture
def machine(monkeypatch, tmp_path):
    yield from worker_machine(monkeypatch, tmp_path)


@pytest.fixture
def reporting_worker(machine, make_worker_client):
    """``(worker row, worker client, machine)``: the harness machine with one alert of each kind."""
    worker, client, _token = make_worker_client(HOST)
    machine.install()
    machine.seed(machine_compute())
    return worker, client, machine


@pytest.mark.django_db
class TestDismissalRoundTrip:

    def test_admin_dismissal_reaches_the_worker_and_is_pruned(self, reporting_worker, admin_client):
        worker, client, machine = reporting_worker
        first = machine.report()
        answer = _beat(client, first, os='Linux')  # the registration handler
        assert answer['dismissed_card_alerts'] == []
        assert _row(worker).card_status == stored_form(first)

        _dismiss(admin_client, worker)
        before = machine.compute()
        second = machine.report()
        answer = _beat(client, second)  # the heartbeat handler; the worker still reports the alert
        assert answer['dismissed_card_alerts'] == [DISMISSED]
        assert _row(worker).card_status == stored_form(second)
        assert rebuild.state() == (0, False)

        removed = choice.apply_manager_dismissals(answer)

        assert [entry['id'] for entry in removed] == [DISMISSED]
        after = machine.compute()
        assert after['alerts'] == [entry for entry in before['alerts'] if entry['id'] != DISMISSED]
        assert _without_alerts(after) == _without_alerts(before)
        assert rebuild.state() == (1, True)  # one rebuild requested

        # The same answer again (a lost acknowledgement) dismisses nothing and requests no rebuild.
        assert choice.apply_manager_dismissals(answer) == []
        assert machine.compute() == after
        assert rebuild.state() == (1, True)

        third = machine.report()
        assert _alert_ids(third) == [alert_id for alert_id in _alert_ids(second) if alert_id != DISMISSED]
        assert {entry['key']: entry['state'] for entry in third['cards']}[KEY_HELD] == 'ok'  # O8: back in use
        answer = _beat(client, third)
        assert answer['dismissed_card_alerts'] == []
        assert answer['card_alert_dismissals'] == []
        row = _row(worker)
        assert row.card_alert_dismissals == []
        assert row.card_status == stored_form(third)

    def test_an_alert_raised_after_the_dismissal_is_kept(self, reporting_worker, admin_client):
        """Only the dismissed id is removed: another alert in the same answer's report stays everywhere."""
        worker, client, machine = reporting_worker
        _beat(client, machine.report())
        _dismiss(admin_client, worker)
        answer = _beat(client, machine.report())

        choice.apply_manager_dismissals(answer)
        _beat(client, machine.report())

        remaining = sorted(alert_id for alert_id in ALERT_IDS.values() if alert_id != DISMISSED)
        assert sorted(_alert_ids(machine.compute())) == remaining
        assert sorted(_alert_ids(_row(worker).card_status)) == remaining


@pytest.fixture
def worker_loop(reporting_worker, monkeypatch):
    """The worker's ``system_monitor`` with its HTTP call routed to the Django test client; the bodies sent."""
    worker, client, machine = reporting_worker
    sent = []

    def send(body):
        sent.append(json.loads(json.dumps(body)))
        resp = client.post(HB, body, format='json')
        assert resp.status_code == 200, resp.data
        return json.loads(resp.content)

    monkeypatch.setattr(api_handler, 'send_authenticated_heartbeat', send)
    monkeypatch.setattr(system_monitor, 'get_system_info',
                        lambda: {'hostname': HOST, 'os': 'Linux', 'available_tools': {}})
    monkeypatch.setattr(system_monitor, '_get_ui_url', lambda: None)
    monkeypatch.setattr(system_monitor, '_get_ui_cert_fingerprint', lambda: '')
    monkeypatch.setattr(system_monitor, '_add_unreal_payload', lambda body: None)
    monkeypatch.setattr(system_monitor, '_process_heartbeat_versions', lambda *args, **kwargs: True)
    monkeypatch.setattr(config, 'get_schedule_config', lambda: {})
    monkeypatch.setattr(config, 'API_TOKEN', 'token')
    for name in ('WORKER_ID', '_versions_ready'):
        monkeypatch.setattr(system_monitor, name, getattr(system_monitor, name))  # restored after the test
    return worker, machine, sent


@pytest.mark.django_db
def test_round_trip_through_the_worker_heartbeat_loop(worker_loop, admin_client):
    worker, machine, sent = worker_loop

    assert system_monitor.register_with_manager() == worker.pk
    assert _row(worker).card_status == stored_form(sent[0]['cards'])
    _dismiss(admin_client, worker)

    system_monitor.send_heartbeat()  # the answer names the alert; the worker applies it

    assert DISMISSED in _alert_ids(sent[1]['cards'])
    assert DISMISSED not in _alert_ids(machine.compute())
    assert rebuild.state() == (1, True)
    assert _row(worker).card_alert_dismissals == [DISMISSED]

    system_monitor.send_heartbeat()  # the report no longer holds the alert: the manager prunes the id

    assert DISMISSED not in _alert_ids(sent[2]['cards'])
    row = _row(worker)
    assert row.card_alert_dismissals == []
    assert row.card_status == stored_form(sent[2]['cards'])
    assert rebuild.state() == (1, True)
