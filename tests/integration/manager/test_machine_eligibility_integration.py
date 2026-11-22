# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: which pairs get a targeted pilot (Unreal per-machine
memory test D1, D5, D14, FR-MT-K-3, FR-MT-P-1 interim).

Covers: a real heartbeat with five ticked ``ok`` cards of different
kinds, an unticked, a lost and a vendor-less card stores a report whose
``worker_kinds`` are the first four sorted kinds, and a new job then gets
one pilot per kind; offline, inactive, override (no live card reading),
share-unreachable, build-less and kind-less workers get no pilot; a
worker with 20 QUEUED live targeted pilots gets no more (RENDERING,
superseded and stale ones, made under CVars the project no longer has, do
not count; 19 still allow one) while another worker still gets its pilot.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.unit.manager.card_report_helpers import report
from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Worker
from workers.renderers.unreal_machine import worker_kinds
from workers.services import machine_tests

from .machine_test_helpers import KIND_AMD, kind_card, pair_record, set_cards
from .unreal_helpers import caps, unreal_asset
from .unreal_pilot_helpers import KIND, created_job, pilots, ready_worker

HB = '/api/heartbeat/'
TICKED_KINDS = ['8086:56a0:0005', '10de:2b85:00a1', '1002:744c:00c8', '10de:2684:none', '1002:7550:00c0']


@pytest.fixture
def uasset(db):
    return unreal_asset()


def _targets(asset):
    return sorted(p.target_worker_id for p in pilots(asset))


@pytest.mark.django_db
class TestHeartbeatKinds:

    def test_five_ticked_kinds_give_the_first_four(self, admin_client, uasset, make_worker_client):
        worker, client, _token = make_worker_client('mt-kinds')
        cards = [kind_card(kind, index) for index, kind in enumerate(TICKED_KINDS)]
        cards += [
            kind_card('0001:0001:0001', 5, ticked=False, state='unticked'),
            kind_card('0002:0002:0002', 6, state='lost'),
            kind_card('0003:0003:0003', 7, vendor_id=None),
        ]
        resp = client.post(HB, {'hostname': worker.hostname, 'cards': report(
            cards=cards, alerts=[], notices=[], warnings=[])}, format='json')
        assert resp.status_code == 200, resp.data
        stored = Worker.objects.get(pk=worker.pk)
        assert len(stored.card_status['cards']) == 8
        expected = ('1002:744c:00c8', '1002:7550:00c0', '10de:2684:none', '10de:2b85:00a1')
        assert worker_kinds(stored) == expected
        # The same worker, given the build, the share and a live reading, gets one pilot per kind.
        status = stored.card_status
        ready_worker(stored)
        Worker.objects.filter(pk=worker.pk).update(card_status=status)
        created_job(admin_client, uasset)
        assert sorted(p.render_settings['unreal.card_kind'] for p in pilots(uasset)) == list(expected)


@pytest.mark.django_db
class TestNoPilot:

    def test_offline_worker(self, admin_client, uasset, worker_with_token):
        worker, _client = worker_with_token
        ready_worker(worker)
        old = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS + 60)
        Worker.objects.filter(pk=worker.pk).update(last_seen=old)
        created_job(admin_client, uasset)
        assert pilots(uasset) == []

    def test_inactive_worker(self, admin_client, uasset, worker_with_token):
        worker, _client = worker_with_token
        ready_worker(worker)
        Worker.objects.filter(pk=worker.pk).update(is_active=False)
        created_job(admin_client, uasset)
        assert pilots(uasset) == []

    def test_override_worker(self, admin_client, uasset, worker_with_token):
        """D14: ``SETHLANS_UNREAL_VRAM_MB`` means no live card reading, so never eligible."""
        ready_worker(worker_with_token[0], source='override')
        created_job(admin_client, uasset)
        assert pilots(uasset) == []

    @pytest.mark.parametrize('capabilities', [caps(reachable=False), caps(builds=('5.7.4-46234231',))],
                             ids=['share_unreachable', 'other_build'])
    def test_worker_without_share_or_build(self, admin_client, uasset, worker_with_token, capabilities):
        worker, _client = worker_with_token
        ready_worker(worker)
        Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=capabilities)
        created_job(admin_client, uasset)
        assert pilots(uasset) == []

    def test_worker_without_a_ticked_kind(self, admin_client, uasset, worker_with_token):
        worker, _client = worker_with_token
        ready_worker(worker)
        set_cards(worker, [kind_card(KIND, 0, ticked=False), kind_card(KIND_AMD, 1, state='held')])
        created_job(admin_client, uasset)
        assert pilots(uasset) == []


@pytest.mark.django_db
class TestOpenPilotCap:

    @pytest.fixture
    def two(self, worker_with_token, make_worker_client):
        worker_a, _client = worker_with_token
        worker_b, _client_b, _token = make_worker_client('mt-cap-b')
        ready_worker(worker_a)
        ready_worker(worker_b)
        return worker_a, worker_b

    def _fill(self, asset, worker, count, status='QUEUED', current=True, overrides=None):
        """``count`` targeted pilots of other keys, each with its record (current unless told otherwise).

        ``overrides``: the records' CVar hash (default: the project's current one).
        """
        other = unreal_asset(asset.project, name=f'MtCapFill{status}{current}{overrides}')
        for n in range(count):
            pair_record(other, f'{n:064x}', worker, pilot=status, current=current, overrides=overrides)

    def test_superseded_queued_pilots_do_not_count(self, admin_client, uasset, two):
        worker_a, worker_b = two
        self._fill(uasset, worker_a, 20, current=False)
        created_job(admin_client, uasset)
        assert _targets(uasset) == sorted([worker_a.pk, worker_b.pk])

    def test_stale_queued_pilots_do_not_count(self, admin_client, uasset, two):
        """Pilots made under CVars the project no longer has can never run: they leave room."""
        worker_a, worker_b = two
        self._fill(uasset, worker_a, 20, overrides='f' * 64)
        assert machine_tests.open_pilot_counts({worker_a.pk, worker_b.pk}) == {}
        created_job(admin_client, uasset)
        assert _targets(uasset) == sorted([worker_a.pk, worker_b.pk])

    def test_only_live_pilots_are_counted(self, uasset, two):
        """19 live plus stale and superseded ones: the count is 19, so the 20th pilot is still made."""
        worker_a, worker_b = two
        self._fill(uasset, worker_a, 19)
        self._fill(uasset, worker_a, 5, current=False)
        self._fill(uasset, worker_a, 5, overrides='f' * 64)
        self._fill(uasset, worker_b, 3, status='RENDERING')
        assert machine_tests.open_pilot_counts({worker_a.pk, worker_b.pk}) == {worker_a.pk: 19}

    def test_twenty_queued_pilots_stop_more(self, admin_client, uasset, two, mocker):
        worker_a, worker_b = two
        assert machine_tests.MAX_OPEN_PILOTS == 20
        self._fill(uasset, worker_a, 20)
        warning = mocker.patch.object(machine_tests.logger, 'warning')
        created_job(admin_client, uasset)
        assert _targets(uasset) == [worker_b.pk]
        [call] = warning.call_args_list
        assert 'already has %d queued memory tests' in call.args[0] and call.args[1:3] == (worker_a.hostname, 20)

    def test_nineteen_still_allow_one(self, admin_client, uasset, two):
        worker_a, worker_b = two
        self._fill(uasset, worker_a, 19)
        created_job(admin_client, uasset)
        assert _targets(uasset) == sorted([worker_a.pk, worker_b.pk])

    def test_rendering_pilots_do_not_count(self, admin_client, uasset, two):
        worker_a, worker_b = two
        self._fill(uasset, worker_a, 20, status='RENDERING')
        created_job(admin_client, uasset)
        assert _targets(uasset) == sorted([worker_a.pk, worker_b.pk])

    def test_the_cap_counts_pilots_made_in_the_same_call(self, admin_client, uasset, worker_with_token):
        """19 open plus a worker with two kinds: only one more pilot (the 20th)."""
        worker, _client = worker_with_token
        ready_worker(worker)
        set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1)])
        self._fill(uasset, worker, 19)
        created_job(admin_client, uasset)
        assert len(pilots(uasset)) == 1
