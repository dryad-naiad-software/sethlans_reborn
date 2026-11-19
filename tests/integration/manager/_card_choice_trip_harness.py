# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plumbing for the card-change round trips, manager -> worker -> manager
(worker card selection chunks 10-11, FR-CM-4..13).

Built on the chunk 5 contract harness (``_card_contract_harness``): a real
``config.json``, the worker's real report and its real
``system_monitor.register_with_manager`` / ``send_heartbeat``, with only the
HTTP call routed to the Django test client through the real heartbeat
endpoint and a worker token. The heartbeat answer therefore reaches the
worker's real ``cards.remote.handle_response``, which probes through the
faked OS probe and saves through the real ``choice.save_choice``.

The machine has two NVIDIA cards Blender drives with OptiX: ``KEY_OK`` is
ticked, ``KEY_UNTICKED`` is not, no alerts, revision 3.

``trip_worker`` is the body of each module's fixture (``yield from``; a
fixture defined here would be F811 in every test module).

Module name starts with an underscore so pytest does not collect it.
"""
import json

from sethlans_worker_agent import api_handler, config, system_monitor
from sethlans_worker_agent.capacity import card_blender
from sethlans_worker_agent.cards import inventory, os_probe, remote
from workers.models import Worker

from ._card_contract_harness import (
    KEY_OK, KEY_UNTICKED, NVIDIA, RTX, blender_record, os_card, record, worker_machine,
)

HB = '/api/heartbeat/'
HOST = 'trip-host'
DEVICE = 0x2704
OTHER_DEVICE = 0x2782  # another RTX model at the same PCI address
BASE_REVISION = 3
TRIP_OS = (os_card(KEY_OK, RTX, NVIDIA, DEVICE), os_card(KEY_UNTICKED, RTX, NVIDIA, DEVICE))
TRIP_BLENDER = [blender_record(KEY_OK, RTX, 'OPTIX'), blender_record(KEY_UNTICKED, RTX, 'OPTIX')]
BOTH = [KEY_OK, KEY_UNTICKED]
NOT_ALLOWED_409 = "This worker does not allow the manager to change its graphics card choice."

__all__ = ['trip_worker']


def trip_compute(**changes):
    """The saved ``compute`` section: ``KEY_OK`` ticked, both cards known, no alerts."""
    compute = {'method': 'gpu', 'gpu_mode': 'split', 'revision': BASE_REVISION,
               'cards': [record(KEY_OK, RTX, NVIDIA, DEVICE)], 'known': list(BOTH), 'alerts': []}
    compute.update(changes)
    return compute


def without(compute, *names):
    """``compute`` without ``names``; the default leaves out what a refusal writes (FR-CM-7, D16)."""
    names = names or ('last_request', 'recent_request_ids')
    return {name: value for name, value in compute.items() if name not in names}


def fresh_inventory():
    """The inventory the worker's pages save with: a fresh probe joined with the Blender list in use."""
    blender_cards, version = card_blender.known()
    return inventory.build(os_probe.probe(), blender_cards, version)


class Trip:
    """One enrolled worker, its machine, and every heartbeat body sent and answer received."""

    def __init__(self, worker, client, machine):
        self.worker = worker
        self.client = client
        self.machine = machine
        self.sent = []
        self.answers = []

    def send(self, body):
        """``api_handler.send_authenticated_heartbeat`` through the Django test client (JSON both ways)."""
        self.sent.append(json.loads(json.dumps(body)))
        resp = self.client.post(HB, body, format='json')
        assert resp.status_code == 200, resp.data
        answer = json.loads(resp.content)
        self.answers.append(answer)
        return answer

    def register(self):
        assert system_monitor.register_with_manager() == self.worker.pk
        return self.answers[-1]

    def beat(self):
        """One real heartbeat: the report goes out, the answer is handled; the answer."""
        system_monitor.send_heartbeat()
        return self.answers[-1]

    def last_report(self):
        return self.sent[-1]['cards']

    def row(self):
        return Worker.objects.get(pk=self.worker.pk)

    def reported_ids(self):
        """``{key: {vendor_id, device_id}}`` from the stored report, as an admin page names them."""
        return {entry['key']: {'vendor_id': entry['vendor_id'], 'device_id': entry['device_id']}
                for entry in self.row().card_status['cards']}

    def body(self, keys=BOTH, gpu_mode='combined', method='gpu', base_revision=None):
        """An action body ticking ``keys`` with the reported ids, both cards shown."""
        ids = self.reported_ids()
        revision = self.row().card_status['revision'] if base_revision is None else base_revision
        return {'method': method, 'gpu_mode': gpu_mode, 'cards': [{'key': key, **ids[key]} for key in keys],
                'shown': list(BOTH), 'base_revision': revision}

    def post(self, admin_client, expect=202, **body):
        resp = admin_client.post(f'{HB}{self.worker.pk}/card-choice/', self.body(**body), format='json')
        assert resp.status_code == expect, resp.data
        return json.loads(resp.content)

    def delete(self, admin_client, expect=204):
        resp = admin_client.delete(f'{HB}{self.worker.pk}/card-choice/')
        assert resp.status_code == expect, getattr(resp, 'data', None)
        return resp


def _route(monkeypatch, trip):
    monkeypatch.setattr(api_handler, 'send_authenticated_heartbeat', trip.send)
    monkeypatch.setattr(system_monitor, 'get_system_info',
                        lambda: {'hostname': HOST, 'os': 'Linux', 'available_tools': {}})
    monkeypatch.setattr(system_monitor, '_get_ui_url', lambda: None)
    monkeypatch.setattr(system_monitor, '_get_ui_cert_fingerprint', lambda: '')
    monkeypatch.setattr(system_monitor, '_add_unreal_payload', lambda body: None)
    monkeypatch.setattr(system_monitor, '_process_heartbeat_versions', lambda *args, **kwargs: True)
    monkeypatch.setattr(config, 'get_schedule_config', lambda: {})
    monkeypatch.setattr(config, 'API_TOKEN', 'token')
    for name in ('WORKER_ID', '_versions_ready', '_manager_setup_complete'):
        monkeypatch.setattr(system_monitor, name, getattr(system_monitor, name))  # restored after the test


def trip_worker(monkeypatch, tmp_path, make_worker_client):
    """Generator for a module's fixture: a ``Trip`` whose worker is not registered yet."""
    machines = worker_machine(monkeypatch, tmp_path)
    machine = next(machines)
    remote.reset_for_tests()
    card_blender.reset_for_tests()
    try:
        machine.install(os_cards=TRIP_OS, blender_cards=TRIP_BLENDER)
        machine.seed(trip_compute())
        worker, client, _token = make_worker_client(HOST)
        trip = Trip(worker, client, machine)
        _route(monkeypatch, trip)
        yield trip
    finally:
        remote.reset_for_tests()
        card_blender.reset_for_tests()
        next(machines, None)
