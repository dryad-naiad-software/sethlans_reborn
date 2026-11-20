# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal on its card, end to end on a two-card worker (gpu-memory-reader
chunk 7: D22, FR-GU-4, FR-GU-10, FR-GU-11; FR-GH-10 round trip).

The real reserver gates the job, the real executor runs the fake engine, and
the pilot body the worker built goes to the real ``pilot-result/`` endpoint.
Card names and keys are made up.
"""
import sys

import pytest

from sethlans_worker_agent.unreal_executor import reserve
from tests.integration.worker.unreal_executor._harness import OVERRIDES, summary
from tests.integration.worker.unreal_executor._two_cards import (
    BIG, JOB_DELTA_MB, SMALL, TwoCardHarness, raw_reading)

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects, Windows command lines')

UNTICKED = "the card the memory gate checked is no longer an Unreal card in use."
KEYLESS = "the card the memory gate checked has no PCI address, so a pilot could not be reported."
PILOT_OPTS = {'render_sleep': 4, 'phase_sleep': 4}


@pytest.fixture(autouse=True)
def _forget_refused_projects():
    from sethlans_worker_agent.unreal_executor import adapter_log
    adapter_log.reset_for_tests()
    yield
    adapter_log.reset_for_tests()


@pytest.fixture
def manager_pilot(db):
    """A pilot on the manager, claimed by a worker whose heartbeat reported both cards (FR-GH-10)."""
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    from tests.integration.manager.gpu_memory_reader_helpers import payload_of, send_heartbeat
    from tests.integration.manager.unreal_helpers import unreal_asset, unreal_project
    from tests.integration.manager.unreal_pilot_helpers import claim, created_job, pilots
    from workers.models import Worker

    admin = APIClient()
    admin.force_authenticate(user=User.objects.create_superuser('admin', 'admin@example.com', 'testpass123'))
    user = User.objects.create_user(username='worker_twocards')
    worker = Worker.objects.create(hostname='twocards', user=user, is_active=True,
                                   available_tools={'blender': ['4.2.19']})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
    send_heartbeat(client, worker, payload_of(raw_reading(taken_at=100.0)))
    asset = unreal_asset(unreal_project(unreal_cvar_overrides=dict(OVERRIDES)))
    created_job(admin, asset)
    job = pilots(asset)[0]
    resp = claim(client, job, worker)
    assert resp.status_code == 200, resp.data
    return job, client


def _launched_on(h, card):
    for name in ('warmup.1.log', 'render.1.log'):
        text = h.log(name)
        assert f'Found D3D12 adapter {card.dxgi_index}: {card.name} (VendorId: {card.vendor_id:04x}' in text
        assert f'Chosen D3D12 Adapter Id = {card.dxgi_index}' in text


def _pilot_on(h, card, result):
    assert result.success and result.released, result.error_message
    body, = h.manager.pilot_bodies
    assert body['gpu'] == {'index': card.reading_index, 'name': card.name, 'total_mb': card.total_mb,
                           'card_key': card.key}
    # Only the job's card is traced: the other card's +6,000 MB does not become the peak (FR-GU-11).
    assert body['card_delta_peak_mb'] == body['peak_mb'] == JOB_DELTA_MB and body['peak_source'] == 'card'
    assert body['baseline_used_mb'] == card.idle_mb
    s = summary(result)
    assert s['vram_gate'] == 'pilot' and s['vram_mb'] == str(card.total_mb) and s['vram_used_mb'] == str(card.idle_mb)
    assert s['pilot_adapter'] == card.name and s['pilot_card_delta_peak_mb'] == str(JOB_DELTA_MB)
    return body


def _skipped(caplog, message):
    return any(message in record.getMessage() for record in caplog.records if record.name == reserve.__name__)


@pytest.mark.django_db
def test_larger_card_is_gated_handed_to_the_first_launch_and_reported(tmp_path, monkeypatch, manager_pilot):
    from tests.integration.manager.unreal_pilot_helpers import post_result, record_of
    manager_job, client = manager_pilot
    h = TwoCardHarness(tmp_path, monkeypatch, opts=PILOT_OPTS)
    job = h.job(77, kind='pilot')
    reservation = h.executor.try_reserve(job)
    assert reservation is not None and reservation.gate.card_key == BIG.key
    assert reservation.card_keys == (BIG.key, SMALL.key)
    result = h.run(job, reservation)
    # FR-GU-10: the first choose gets the gated key; the render keeps that card (D8).
    assert h.choices == [(77, BIG.key, BIG.key), (77, BIG.key, BIG.key)]
    _launched_on(h, BIG)
    body = _pilot_on(h, BIG, result)
    resp = post_result(client, manager_job.pk, body)
    assert resp.status_code == 200, resp.data
    assert resp.data['status'] == 'DONE' and resp.data['peak_mb'] == JOB_DELTA_MB
    record = record_of(manager_job)
    assert (record.gpu_index, record.gpu_name, record.gpu_total_mb) == (BIG.reading_index, BIG.name, BIG.total_mb)
    assert h.token_free()


def test_render_job_runs_on_the_gated_card(tmp_path, monkeypatch):
    h = TwoCardHarness(tmp_path, monkeypatch)
    job = h.job(78)
    reservation = h.executor.try_reserve(job)
    assert reservation is not None and reservation.gate.card_key == BIG.key
    result = h.run(job, reservation)
    assert result.success and not result.released, result.error_message
    assert [key for _job, key, _card in h.choices][:1] == [BIG.key] and {c for *_rest, c in h.choices} == {BIG.key}
    _launched_on(h, BIG)
    s = summary(result)
    assert s['vram_gate'] == 'measured' and s['vram_mb'] == str(BIG.total_mb)
    assert h.manager.uploads and h.token_free()


@pytest.mark.parametrize('kind', ['pilot', 'render'])
def test_gated_card_unticked_before_reserve_skips_before_any_prep(tmp_path, monkeypatch, caplog, kind):
    """The reading still holds the big card (the cut set is up to 5 s old); the card code no longer has it."""
    h = TwoCardHarness(tmp_path, monkeypatch)
    h.reserve_keys = [SMALL.key]
    job = h.job(80, kind=kind)
    assert h.executor.try_reserve(job) is None
    assert _skipped(caplog, UNTICKED)
    assert h.choices == [] and h.nothing_prepared() and h.token_free()
    h.reserve_keys = [BIG.key, SMALL.key]   # ticked again: the token was free, the key check was the only cause
    reservation = h.executor.try_reserve(job)
    assert reservation is not None and reservation.gate.card_key == BIG.key
    assert reservation.release() and h.token_free()


def test_pilot_on_a_keyless_gated_card_is_skipped_before_the_render(tmp_path, monkeypatch, caplog):
    h = TwoCardHarness(tmp_path, monkeypatch, cut_to=None, keyless=(BIG.key,))
    job = h.job(81, kind='pilot')
    assert h.executor.try_reserve(job) is None
    assert _skipped(caplog, KEYLESS) and not _skipped(caplog, UNTICKED)
    assert h.choices == [] and h.nothing_prepared() and h.token_free()
    assert not h.manager.pilot_bodies and not h.manager.releases


def test_one_card_ticked_of_two_is_the_only_card_gated_or_used(tmp_path, monkeypatch):
    """The smaller card is the Unreal card: the bigger one never reaches the gate, a launch or a trace."""
    h = TwoCardHarness(tmp_path, monkeypatch, ticked=(SMALL,), cut_to=(SMALL.key,), opts=PILOT_OPTS)
    job = h.job(82, kind='pilot')
    reservation = h.executor.try_reserve(job)
    assert reservation is not None and reservation.gate.card_key == SMALL.key
    assert reservation.card_keys == (SMALL.key,)
    result = h.run(job, reservation)
    assert h.choices == [(82, SMALL.key, SMALL.key), (82, SMALL.key, SMALL.key)]
    _launched_on(h, SMALL)
    _pilot_on(h, SMALL, result)
    assert h.sampler.seen == {SMALL.key} and h.token_free()
