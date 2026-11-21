# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Check 6b of a manager card change: one brand of card (unreal-render-per-card FR-PC-BR-5, CS FR-CM-5).

Cards of two brands, or a card of unknown brand beside another card, are refused with code ``mixed_brands``
after the ids check (check 6) and before the save's own checks (one family and the rest), writing only the outcome.
"""
import pytest

from sethlans_worker_agent.cards import remote
from tests.unit.worker._card_choice import AMD, KEY_RTX_A, KEY_XT_23, NVIDIA, card, inv, rtx_a, xt_03, xt_23
from tests.unit.worker.cards._remote_helpers import OUTCOME_KEYS, REQUEST_ID, Machine, answer, request, ticked

KEY_ODD = 'pci:0000:41:00.0'
REASON = "the requested cards are of more than one brand (not supported)"
RTX = ticked(KEY_RTX_A, NVIDIA, 0x2704)
XT = ticked(KEY_XT_23, AMD, 0x744C)
ODD = ticked(KEY_ODD, None, None)


def _unknown():
    return card(KEY_ODD, 'Unknown card')


def _assert_mixed(machine):
    record = machine.last_request
    assert {name: record[name] for name in ('id', 'result', 'code', 'reason', 'revision')} == {
        'id': REQUEST_ID, 'result': 'refused', 'code': 'mixed_brands', 'reason': REASON, 'revision': 0}
    assert machine.written_keys() == [OUTCOME_KEYS]  # nothing else changed
    assert machine.rebuilds == 0


@pytest.mark.parametrize('gpu_mode', ['split', 'combined'])
def test_nvidia_and_amd_are_mixed_brands(monkeypatch, gpu_mode):
    """Combined too: refused as ``mixed_brands`` before the one-family rule could call it ``invalid``."""
    machine = Machine(monkeypatch, inventory=inv(rtx_a(), xt_23()))
    shown = [KEY_RTX_A, KEY_XT_23]
    remote.handle_response(answer(request(gpu_mode=gpu_mode, cards=[RTX, XT], shown=shown)))
    _assert_mixed(machine)


def test_an_unknown_card_beside_a_known_one_is_mixed_brands(monkeypatch):
    machine = Machine(monkeypatch, inventory=inv(xt_23(), _unknown()))
    remote.handle_response(answer(request(cards=[XT, ODD], shown=[KEY_XT_23, KEY_ODD])))
    _assert_mixed(machine)


def test_an_unknown_card_on_its_own_is_applied(monkeypatch):
    machine = Machine(monkeypatch, inventory=inv(xt_23(), _unknown()))
    remote.handle_response(answer(request(cards=[ODD], shown=[KEY_XT_23, KEY_ODD])))
    assert machine.last_request['code'] == 'applied'


def test_one_brand_on_a_two_brand_machine_is_applied(monkeypatch):
    machine = Machine(monkeypatch, inventory=inv(rtx_a(), xt_03(), xt_23()))
    remote.handle_response(answer(request(cards=[XT], shown=[KEY_RTX_A, KEY_XT_23])))
    assert machine.last_request['code'] == 'applied' and machine.rebuilds == 1


def test_the_ids_check_comes_first(monkeypatch):
    machine = Machine(monkeypatch, inventory=inv(rtx_a(), xt_23()))
    other = ticked(KEY_XT_23, AMD, 0x7448)
    remote.handle_response(answer(request(cards=[RTX, other], shown=[KEY_RTX_A, KEY_XT_23])))
    assert machine.last_request['code'] == 'identity_mismatch'
