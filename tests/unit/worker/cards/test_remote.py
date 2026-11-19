# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``cards.remote.handle_response``: each check of FR-CM-5 in its order (AC-23..AC-28, AC-31, AC-40).

- Applied: one write holding the choice, ``revision + 1`` and the ``applied`` outcome; one rebuild (AC-23).
- A repeat delivery is not evaluated again: one save, one rebuild, one probe, the same outcome (AC-31).
- Refusals change nothing but ``last_request`` and request no rebuild: ``env_override`` and ``not_allowed``
  before any probe, ``probe_failed``, ``identity_mismatch``, ``stale_revision``, ``card_held``, ``invalid``,
  ``save_failed``. A request cannot change the switch (AC-28).
- Logs: receipt, applied with method, mode, cards and revision; refused with its code (AC-40).
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import in_use, matching, remote, store, store_requests
from tests.unit.worker._card_choice import AMD, KEY_XT_23, xt_23
from tests.unit.worker.cards._remote_helpers import (
    OUTCOME_KEYS, REQUEST_ID, Machine, answer, messages, request, ticked,
)

ALERT = {'id': 'cd' * 16, 'kind': 'card_lost', 'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT',
         'detail': 'missing from the card list', 'raised_at': '2026-11-19T08:00:00+00:00'}
RECEIPT = f"Graphics card change '{REQUEST_ID}' from the manager, requested by 'user id=7' at " \
          f"'2026-11-19T10:00:00+00:00'."


def _refused_line(code, reason):
    return f"Graphics card change '{REQUEST_ID}' refused ('{code}'): {ascii(reason)}."


def _assert_refused(machine, code, reason, revision=0):
    record = machine.last_request
    assert {name: record[name] for name in ('id', 'result', 'code', 'reason', 'revision')} == {
        'id': REQUEST_ID, 'result': 'refused', 'code': code, 'reason': reason, 'revision': revision}
    assert (record['requested_by'], record['requested_at']) == ('user id=7', '2026-11-19T10:00:00+00:00')
    assert machine.written_keys() == [OUTCOME_KEYS]  # nothing else changed (FR-CM-5)
    assert machine.compute['recent_request_ids'][-1] == REQUEST_ID
    assert machine.rebuilds == 0


class TestApplied:

    def test_saved_with_the_outcome_in_one_write_and_one_rebuild(self, monkeypatch, caplog):
        machine = Machine(monkeypatch)
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.remote'):
            remote.handle_response(answer(request()))

        compute = machine.compute
        assert (compute['method'], compute['gpu_mode'], compute['revision']) == ('gpu', 'split', 1)
        assert compute['cards'] == [in_use.record_of(xt_23())]
        assert len(machine.writes) == 1
        assert set(machine.written_keys()[0]) >= {'compute.cards', 'compute.revision', 'compute.last_request'}
        assert {name: compute['last_request'][name] for name in ('id', 'result', 'code', 'reason', 'revision')} == {
            'id': REQUEST_ID, 'result': 'applied', 'code': 'applied', 'reason': '', 'revision': 1}
        assert machine.rebuilds == 1 and machine.probes == 1
        assert messages(caplog) == [RECEIPT, f"Graphics card change '{REQUEST_ID}' applied: method 'gpu', mode "
                                             f"'split', cards ['{KEY_XT_23}']; revision 1."]

    def test_outcome_is_reported_without_requester(self, monkeypatch):
        Machine(monkeypatch)
        remote.handle_response(answer(request()))

        outcome = store_requests.outcome_for_report(store.snapshot())
        assert set(outcome) == {'id', 'result', 'code', 'reason', 'revision', 'finished_at'}
        assert (outcome['id'], outcome['result'], outcome['revision']) == (REQUEST_ID, 'applied', 1)

    def test_combined_with_one_card_is_saved_and_logged_as_split(self, monkeypatch, caplog):
        machine = Machine(monkeypatch)
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.remote'):
            remote.handle_response(answer(request(gpu_mode='combined')))
        assert machine.compute['gpu_mode'] == 'split'
        assert "mode 'split'" in messages(caplog, 'INFO')[-1]


class TestRepeatDelivery:

    def test_same_request_twice_is_saved_once_and_reports_the_same_outcome(self, monkeypatch):
        machine = Machine(monkeypatch)
        remote.handle_response(answer(request()))
        first = store_requests.outcome_for_report(store.snapshot())
        remote.handle_response(answer(request()))

        assert machine.compute['revision'] == 1 and len(machine.writes) == 1
        assert (machine.rebuilds, machine.probes) == (1, 1)
        assert store_requests.outcome_for_report(store.snapshot()) == first

    def test_refused_request_twice_is_evaluated_once(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        remote.handle_response(answer(request()))
        remote.handle_response(answer(request()))
        assert len(machine.writes) == 1


class TestRefusedBeforeTheProbe:

    def test_env_override(self, monkeypatch, caplog):
        machine = Machine(monkeypatch)
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.remote'):
            remote.handle_response(answer(request()))
        _assert_refused(machine, 'env_override', remote.ENV_TEXT)
        assert machine.probes == 0
        assert messages(caplog) == [RECEIPT, _refused_line('env_override', remote.ENV_TEXT)]

    @pytest.mark.parametrize('saved, environ', [(False, None), (True, 'false'), (False, 'nonsense')],
                             ids=['saved-off', 'env-off', 'env-invalid'])
    def test_switch_off_is_not_allowed(self, monkeypatch, saved, environ):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': saved})
        if environ is not None:
            monkeypatch.setenv(config_compute.ENV_ALLOW, environ)
        remote.handle_response(answer(request()))
        _assert_refused(machine, 'not_allowed', remote.NOT_ALLOWED_TEXT)
        assert machine.probes == 0

    @pytest.mark.parametrize('saved, sent', [(False, True), (True, False)])
    def test_a_request_never_changes_the_switch(self, monkeypatch, saved, sent):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': saved})
        remote.handle_response(answer(request(allow_manager_changes=sent)))
        assert machine.compute['allow_manager_changes'] is saved
        assert all('compute.allow_manager_changes' not in keys for keys in machine.written_keys())


class TestRefusedAfterTheProbe:

    def test_probe_failed(self, monkeypatch):
        machine = Machine(monkeypatch)
        monkeypatch.setattr(remote, '_fresh_inventory', lambda: None)
        remote.handle_response(answer(request()))
        _assert_refused(machine, 'probe_failed', remote.PROBE_TEXT)

    def test_inventory_built_from_blender_is_probe_failed(self, monkeypatch):
        machine = Machine(monkeypatch)
        machine.inventory = dataclasses.replace(machine.inventory, os_probe_failed=True)
        remote.handle_response(answer(request()))
        _assert_refused(machine, 'probe_failed', remote.PROBE_TEXT)

    def test_absent_key_is_identity_mismatch(self, monkeypatch):
        machine = Machine(monkeypatch)
        absent = 'pci:0000:09:00.0'
        remote.handle_response(answer(request(cards=[ticked(absent)], shown=[absent])))
        _assert_refused(machine, 'identity_mismatch', f'{absent} is not on this machine')

    @pytest.mark.parametrize('vendor_id, device_id', [(AMD, 0x7448), (0x10DE, 0x744C), (None, None)])
    def test_other_model_is_identity_mismatch(self, monkeypatch, vendor_id, device_id):
        machine = Machine(monkeypatch)
        remote.handle_response(answer(request(cards=[ticked(vendor_id=vendor_id, device_id=device_id)])))
        _assert_refused(machine, 'identity_mismatch', f'{KEY_XT_23} is now AMD Radeon RX 7900 XT (1002:744c)')

    def test_stale_revision(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 2})
        remote.handle_response(answer(request(base_revision=1)))
        reason = "the choice was changed on the worker (revision 2) after the manager's view (revision 1)"
        _assert_refused(machine, 'stale_revision', reason, revision=2)

    def test_newly_ticking_a_held_card_is_card_held(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 0, 'alerts': [dict(ALERT)]})
        remote.handle_response(answer(request()))
        _assert_refused(machine, 'card_held', 'AMD Radeon RX 7900 XT has an open alert; dismiss it first')

    def test_keeping_a_held_card_ticked_is_applied_and_it_stays_held(self, monkeypatch):
        compute = {'revision': 0, 'method': 'cpu', 'cards': [in_use.record_of(xt_23())], 'alerts': [dict(ALERT)]}
        machine = Machine(monkeypatch, compute)
        remote.handle_response(answer(request()))

        assert machine.last_request['code'] == 'applied' and machine.compute['method'] == 'gpu'
        assert machine.compute['alerts'] == [ALERT]
        assert matching.evaluate(store.snapshot(), machine.inventory).states[KEY_XT_23] == 'held'

    def test_store_refusal_is_invalid_with_its_sentence(self, monkeypatch):
        machine = Machine(monkeypatch)
        remote.handle_response(answer(request(cards=[])))
        _assert_refused(machine, 'invalid', 'GPU rendering needs at least one ticked graphics card.')

    def test_failed_save_is_save_failed(self, monkeypatch):
        machine = Machine(monkeypatch)
        real_set_many = config_store.set_many
        calls = []

        def fail_first(pairs):
            calls.append(pairs)
            if len(calls) == 1:
                raise OSError('disk full')
            return real_set_many(pairs)

        monkeypatch.setattr(config_store, 'set_many', fail_first)
        remote.handle_response(answer(request()))
        _assert_refused(machine, 'save_failed', remote.SAVE_FAILED_TEXT)

    def test_outcome_kept_in_memory_when_it_cannot_be_written(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})

        def fail(_pairs):
            raise OSError('read-only')

        monkeypatch.setattr(config_store, 'set_many', fail)
        remote.handle_response(answer(request()))
        remote.handle_response(answer(request()))  # not evaluated again: the id is in memory

        assert 'last_request' not in machine.compute
        outcome = store_requests.outcome_for_report(store.snapshot())
        assert (outcome['id'], outcome['code']) == (REQUEST_ID, 'not_allowed')
        assert store_requests.view_fields(store.snapshot())['last_request']['requested_by'] == 'user id=7'
