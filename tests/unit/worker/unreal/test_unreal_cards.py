# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal cards in use for the reader and the heartbeat (card selection FR-CU-5, FR-CU-6, O6; AC-14).

``unreal_card_addresses()`` never holds a card without an address and is
empty when no card is ticked; the Unreal disabled reason is then the FR-CU-6
text, in place of the memory reader's ``NO_VRAM_REASON``. The compute method
never disables Unreal (C-23).
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import in_use, unreal_cards
from sethlans_worker_agent.cards.inventory import Card, Inventory
from sethlans_worker_agent.unreal_executor import capabilities, runtime
from sethlans_worker_agent.web_ui import cards_cache
from tests._unreal_worker_state import FakeSampler, enable_unreal, unreal_runtime_reset  # noqa: F401
from tests.unit.worker.unreal._card_machine import CardMachine, adapter, key

A03, A23 = '0000:03:00.0', '0000:23:00.0'
REAL_DISABLED_REASON = unreal_cards.disabled_reason


def _section(*cards, method='cpu'):
    records = tuple({'key': card.key, 'vendor_id': card.vendor_id, 'device_id': card.device_id, 'name': card.name}
                    for card in cards)
    return config_compute.ComputeSection(method=method, cards=records)


class TestAddresses:

    def test_addresses_of_the_unreal_cards_in_use(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A23), adapter(1, A03)], ticked=[A03, A23])
        inv = machine.inventory()
        assert in_use.unreal_card_addresses(inv, machine.section()) == frozenset({A03, A23})

    def test_a_card_without_an_address_is_never_in_it(self):
        apple = Card(key='apple-gpu', name='Apple M4', vendor_id=1, device_id=2, dxgi_index=0)
        odd = Card(key='pci:0000:05:00.0', name='Card', vendor_id=1, device_id=2, dxgi_index=1)
        inv = Inventory(cards=(apple, odd))
        assert in_use.unreal_card_addresses(inv, _section(apple, odd)) == frozenset({'0000:05:00.0'})

    def test_a_card_without_a_dxgi_index_is_not_an_unreal_card(self):
        linux = Card(key='pci:0000:05:00.0', name='Card', vendor_id=1, device_id=2)
        assert in_use.unreal_card_addresses(Inventory(cards=(linux,)), _section(linux)) == frozenset()

    def test_empty_when_no_card_is_ticked(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[])
        assert in_use.unreal_card_addresses(machine.inventory(), machine.section()) == frozenset()
        assert unreal_cards.evaluate(machine.section(), None) == ((), unreal_cards.NO_TICK_REASON)

    def test_without_arguments_from_the_saved_choice_and_the_report_probe(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        probe = SimpleNamespace(inventory=machine.inventory())
        asked = []
        monkeypatch.setattr(cards_cache, 'report_probe',
                            lambda max_age, probe_allowed=True: (asked.append(probe_allowed), probe)[1])
        assert in_use.unreal_card_addresses() == frozenset({A03})
        assert asked == [True]

    def test_without_arguments_empty_when_the_choice_cannot_be_read(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        machine.read_failed = True
        assert in_use.unreal_card_addresses() == frozenset()


class TestReasons:
    """FR-CU-6, checked in order: no tick, probe failed, none usable."""

    @pytest.mark.parametrize('method', ['cpu', 'gpu', 'cpu_gpu'])
    def test_a_usable_ticked_card_gives_no_reason_whatever_the_method(self, monkeypatch, method):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03], method=method)
        cards, reason = unreal_cards.evaluate(machine.section(), machine.inventory())
        assert [card.key for card in cards] == [key(A03)] and reason == ''

    def test_none_usable(self, monkeypatch):
        alert = {'id': 'a' * 32, 'kind': 'card_changed', 'key': key(A03), 'name': 'Card 0', 'detail': ''}
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03], alerts=[alert])
        assert unreal_cards.evaluate(machine.section(), machine.inventory()) == ((), unreal_cards.NONE_USABLE_REASON)

    def test_probe_failed(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        machine.dxgi_ok = False
        inv = machine.inventory()
        assert inv.os_probe_failed
        assert unreal_cards.evaluate(machine.section(), inv) == ((), unreal_cards.PROBE_FAILED_REASON)

    def test_texts_fit_the_managers_cap(self):
        for text in (unreal_cards.NO_TICK_REASON, unreal_cards.NONE_USABLE_REASON, unreal_cards.PROBE_FAILED_REASON):
            assert len(text) <= capabilities.MAX_REASON_LENGTH and text.isascii()

    def test_the_last_decided_reason_stands_while_nothing_can_be_decided(self, monkeypatch):
        answers = [((), unreal_cards.NONE_USABLE_REASON), None, (('card',), '')]
        monkeypatch.setattr(unreal_cards, 'current', lambda: answers.pop(0))
        assert unreal_cards.disabled_reason() == unreal_cards.NONE_USABLE_REASON
        assert unreal_cards.disabled_reason() == unreal_cards.NONE_USABLE_REASON
        assert unreal_cards.disabled_reason() == ''

    def test_a_failing_evaluation_keeps_the_last_reason(self, monkeypatch):
        monkeypatch.setattr(unreal_cards, 'current', lambda: 1 / 0)
        assert unreal_cards.disabled_reason() == ''

    def test_no_probe_while_an_unreal_render_holds_the_machine(self, monkeypatch):
        from sethlans_worker_agent import unreal_bridge
        CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        monkeypatch.setattr(unreal_bridge, 'drift_check_allowed', lambda: False)
        cards_cache.reset_for_tests()
        try:
            assert unreal_cards.current() is None   # nothing cached and no probe may run
        finally:
            cards_cache.reset_for_tests()


class TestCachedOnly:
    """Review round: the GPU memory sampler thread never runs the card probe nor waits on ``_probe_lock``."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self, monkeypatch):
        cards_cache.reset_for_tests()
        monkeypatch.setattr(cards_cache, 'report_probe', lambda *a, **kw: pytest.fail('report_probe called'))
        yield
        cards_cache.reset_for_tests()

    def test_nothing_cached_cannot_decide_and_probes_nothing(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        assert unreal_cards.current(cached_only=True) is None and machine.probes == 0

    def test_the_cached_probe_serves_whatever_its_age(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        cards_cache._cache = (-1e9, (None, None), SimpleNamespace(inventory=machine.inventory()))
        probes = machine.probes
        cards, reason = unreal_cards.current(cached_only=True)
        assert [card.key for card in cards] == [key(A03)] and reason == '' and machine.probes == probes

    def test_a_running_probe_is_not_waited_for(self, monkeypatch):
        machine = CardMachine(monkeypatch, [adapter(0, A03)], ticked=[A03])
        cards_cache._cache = (0.0, (None, None), SimpleNamespace(inventory=machine.inventory()))
        assert cards_cache._probe_lock.acquire(timeout=5)  # stands for a probe running on another thread
        try:
            assert unreal_cards.current(cached_only=True) is None
        finally:
            cards_cache._probe_lock.release()


class TestHeartbeat:
    """The capability report carries the card reason instead of the memory reader's (O6)."""

    @pytest.fixture(autouse=True)
    def _reset(self, unreal_runtime_reset):  # noqa: F811
        yield

    @pytest.mark.parametrize('reason', [unreal_cards.NO_TICK_REASON, unreal_cards.NONE_USABLE_REASON,
                                        unreal_cards.PROBE_FAILED_REASON])
    def test_card_reason_replaces_no_vram_reason(self, monkeypatch, reason):
        enable_unreal(monkeypatch, sampler=FakeSampler(never_succeeded=True))
        monkeypatch.setattr(unreal_cards, 'disabled_reason', REAL_DISABLED_REASON)
        monkeypatch.setattr(unreal_cards, 'current', lambda: ((), reason))
        report = capabilities.snapshot()
        assert report['enabled'] is False and report['disabled_reason'] == reason
        assert capabilities.poll_params(can_take=True) == {}

    def test_a_usable_card_leaves_the_other_reasons(self, monkeypatch):
        enable_unreal(monkeypatch, sampler=FakeSampler(never_succeeded=True))
        monkeypatch.setattr(unreal_cards, 'disabled_reason', REAL_DISABLED_REASON)
        monkeypatch.setattr(unreal_cards, 'current', lambda: (('card',), ''))
        assert capabilities.snapshot()['disabled_reason'] == runtime.NO_VRAM_REASON
