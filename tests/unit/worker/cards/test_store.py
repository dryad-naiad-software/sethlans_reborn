# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.store.save`` (FR-CC-4, AC-3, AC-26, AC-27, AC-43, D19, D20).

The saved ``compute`` section lives in a dict: ``config_compute._read_raw``
reads it and a fake ``config_store.set_many`` records each write and applies
it, so a refused save is seen as "no write".
"""
import copy
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import store
from sethlans_worker_agent.cards.in_use import COMBINE_TEXT, record_of
from tests.unit.worker._card_choice import (
    KEY_IGPU, KEY_RTX_A, KEY_RTX_B, KEY_XT_03, KEY_XT_23, card, igpu, inv, rtx_a, rtx_b, xt_03, xt_23,
)

KEY_NO_BLENDER = 'pci:0000:41:00.0'
KEY_ELSEWHERE = 'pci:0000:99:00.0'


def _no_blender():
    return card(KEY_NO_BLENDER, 'Card Blender does not offer', vendor_id=0x1002, device_id=0x7480,
                note='not offered by Blender 4.5.0')


@pytest.fixture
def saved(monkeypatch):
    state = {'compute': {'revision': 0}}
    writes = []

    def set_many(pairs, expect=None):
        pairs = list(pairs)
        for dotted, check in (expect or {}).items():  # as the real one: a failing check writes nothing
            section, name = dotted.split('.')
            if not check(state.get(section, {}).get(name)):
                raise config_store.ConfigChangedError(dotted, state.get(section, {}).get(name))
        writes.append(SimpleNamespace(pairs=pairs, locked=store._section_lock.locked()))
        for dotted, value in pairs:
            section, name = dotted.split('.')
            state.setdefault(section, {})[name] = copy.deepcopy(value)

    monkeypatch.setattr(config_compute, '_read_raw', lambda: copy.deepcopy(state['compute']))
    monkeypatch.setattr(config_store, 'set_many', set_many)
    return SimpleNamespace(state=state, writes=writes, compute=state['compute'])


@pytest.fixture
def full():
    return inv(rtx_a(), rtx_b(), xt_03(), xt_23(), igpu(), _no_blender())


class TestSaved:

    def test_ticked_records_known_and_revision_in_one_write(self, saved, full):
        """AC-3: records with ids and name, ``known`` = shown keys in the inventory, revision + 1."""
        revision = store.save('gpu', 'split', [KEY_XT_23, KEY_XT_03], [KEY_XT_23, KEY_IGPU, KEY_XT_03, KEY_ELSEWHERE],
                              full, 0)

        assert revision == 1
        assert len(saved.writes) == 1
        assert saved.writes[0].locked is True
        assert dict(saved.writes[0].pairs) == {
            'compute.method': 'gpu',
            'compute.gpu_mode': 'split',
            'compute.cards': [
                {'key': KEY_XT_03, 'vendor_id': 0x1002, 'device_id': 0x744C, 'revision_id': 0xC8,
                 'name': 'AMD Radeon RX 7900 XT'},
                {'key': KEY_XT_23, 'vendor_id': 0x1002, 'device_id': 0x744C, 'revision_id': 0xCC,
                 'name': 'AMD Radeon RX 7900 XT'},
            ],
            'compute.known': [KEY_XT_03, KEY_XT_23, KEY_IGPU],
            'compute.revision': 1,
        }

    def test_saved_choice_reads_back_as_saved(self, saved, full):
        store.save('cpu_gpu', 'combined', [KEY_XT_03, KEY_XT_23], [KEY_XT_03, KEY_XT_23], full, 0)

        section = store.snapshot()

        assert (section.method, section.gpu_mode, section.revision) == ('cpu_gpu', 'combined', 1)
        assert section.ticked_keys == (KEY_XT_03, KEY_XT_23)
        assert section.invalid == ()

    def test_revision_counts_up_from_the_saved_one(self, saved, full):
        saved.compute['revision'] = 41
        assert store.save('cpu', 'split', [], [], full, 41) == 42
        assert store.save('cpu', 'split', [], [], full, 42) == 43
        assert saved.state['compute']['revision'] == 43

    def test_last_request_goes_in_the_same_write(self, saved, full):
        request = {'id': 'r-7', 'outcome': 'applied', 'requested_by': 'user id=3'}
        store.save('gpu', 'split', [KEY_RTX_A], [KEY_RTX_A], full, 0, last_request=request)
        assert len(saved.writes) == 1
        assert dict(saved.writes[0].pairs)['compute.last_request'] == request

    def test_no_last_request_leaves_it_alone(self, saved, full):
        saved.compute['last_request'] = {'id': 'old'}
        store.save('cpu', 'split', [], [], full, 0)
        assert 'compute.last_request' not in dict(saved.writes[0].pairs)
        assert saved.state['compute']['last_request'] == {'id': 'old'}

    def test_known_drops_a_shown_card_that_is_gone(self, saved, full):
        store.save('cpu', 'split', [], [KEY_ELSEWHERE, KEY_RTX_B], full, 0)
        assert saved.state['compute']['known'] == [KEY_RTX_B]

    def test_combined_with_one_ticked_card_is_saved_as_split(self, saved, full):
        store.save('gpu', 'combined', [KEY_XT_03], [KEY_XT_03], full, 0)
        assert saved.state['compute']['gpu_mode'] == 'split'

    def test_repeated_ticked_key_is_saved_once(self, saved, full):
        store.save('gpu', 'split', [KEY_XT_03, KEY_XT_03], [KEY_XT_03], full, 0)
        assert [record['key'] for record in saved.state['compute']['cards']] == [KEY_XT_03]

    def test_cpu_with_no_ticked_card(self, saved, full):
        store.save('cpu', 'split', [], [KEY_RTX_A], full, 0)
        assert saved.state['compute']['cards'] == []


class TestOneFamily:
    """FR-CC-4: combined needs two or more Blender cards of one backend family (AC-43)."""

    @pytest.mark.parametrize('ticked', [
        [KEY_XT_03, KEY_XT_23],
        [KEY_RTX_A, KEY_RTX_B],
        [KEY_XT_03, KEY_XT_23, KEY_NO_BLENDER],   # a card without a backend is ignored by the rule
    ])
    def test_one_family_is_saved_combined(self, saved, full, ticked):
        store.save('gpu', 'combined', ticked, ticked, full, 0)
        assert saved.state['compute']['gpu_mode'] == 'combined'

    def test_cuda_and_optix_are_one_family(self, saved):
        cuda = card('pci:0000:05:00.0', 'NVIDIA GeForce GTX 1080', 'CUDA_NVIDIA GeForce GTX 1080_0000:05:00',
                    'CUDA', 0x10DE, 0x1B80)
        store.save('gpu', 'combined', [KEY_RTX_A, cuda.key], [KEY_RTX_A, cuda.key], inv(rtx_a(), cuda), 0)
        assert saved.state['compute']['gpu_mode'] == 'combined'

    def test_combined_is_refused(self, saved, full):
        ticked = [KEY_XT_03, KEY_NO_BLENDER]      # only one card counted
        with pytest.raises(ValueError) as caught:
            store.save('gpu', 'combined', ticked, ticked, full, 0)
        assert str(caught.value) == COMBINE_TEXT
        assert saved.writes == []

    @pytest.mark.parametrize('gpu_mode', ['split', 'combined'])
    def test_two_families_are_two_brands_and_refused(self, saved, full, gpu_mode):
        """unreal-render-per-card FR-PC-BR-2 narrows FR-CC-4: CUDA/OptiX beside HIP is NVIDIA beside AMD."""
        with pytest.raises(ValueError, match=r'^Graphics cards of more than one brand \(AMD, NVIDIA\)'):
            store.save('gpu', gpu_mode, [KEY_RTX_A, KEY_XT_03], [KEY_RTX_A, KEY_XT_03], full, 0)
        assert saved.writes == []


class TestRefused:

    @pytest.mark.parametrize('env, value', [(config_compute.ENV_METHOD, 'gpu'),
                                            (config_compute.ENV_GPU_MODE, 'split'),
                                            (config_compute.ENV_CARDS, '0000:03:00.0')])
    def test_env_override(self, saved, full, monkeypatch, env, value):
        monkeypatch.setenv(env, value)
        with pytest.raises(store.EnvOverride):
            store.save('gpu', 'split', [KEY_XT_03], [KEY_XT_03], full, 0)
        assert saved.writes == []

    def test_env_override_is_checked_before_the_revision(self, saved, full, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        with pytest.raises(store.EnvOverride):
            store.save('gpu', 'split', [KEY_XT_03], [KEY_XT_03], full, 99)

    def test_switch_variable_alone_is_no_override(self, saved, full, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_ALLOW, 'false')
        assert store.save('cpu', 'split', [], [], full, 0) == 1

    def test_stale_revision(self, saved, full):
        saved.compute['revision'] = 5
        with pytest.raises(store.StaleRevision) as caught:
            store.save('gpu', 'split', [KEY_XT_03], [KEY_XT_03], full, 4)
        assert (caught.value.current, caught.value.base) == (5, 4)
        assert saved.writes == []

    def test_revision_saved_by_another_process_before_the_write_is_stale(self, saved, full, monkeypatch):
        """Chunk 4 review (concurrency): ``--setup-apply`` saved revision 3 after this save read revision 0; the
        check inside the file lock refuses the write as ``StaleRevision`` (current 3, base 0)."""
        monkeypatch.setattr(config_compute, '_read_raw', lambda: {'revision': 0})  # what this save read
        saved.compute['revision'] = 3                                                # what the file now holds
        with pytest.raises(store.StaleRevision) as caught:
            store.save('gpu', 'split', [KEY_XT_03], [KEY_XT_03], full, 0)
        assert (caught.value.current, caught.value.base) == (3, 0)
        assert saved.writes == []

    @pytest.mark.parametrize('kind', ['card_lost', 'card_changed', 'card_lost_blender'])
    def test_newly_ticked_held_card(self, saved, full, kind):
        saved.compute['alerts'] = [{'id': 'b' * 32, 'kind': kind, 'key': KEY_XT_23, 'name': 'x'}]
        with pytest.raises(store.CardHeld) as caught:
            store.save('gpu', 'split', [KEY_XT_03, KEY_XT_23], [KEY_XT_03, KEY_XT_23], full, 0)
        assert (caught.value.key, caught.value.name) == (KEY_XT_23, 'AMD Radeon RX 7900 XT')
        assert saved.writes == []

    def test_adapter_mismatch_holds_nothing(self, saved, full):
        saved.compute['alerts'] = [{'id': 'b' * 32, 'kind': 'adapter_mismatch', 'key': KEY_XT_23, 'name': 'x'}]
        assert store.save('gpu', 'split', [KEY_XT_23], [KEY_XT_23], full, 0) == 1

    def test_keeping_a_held_card_ticked_is_accepted(self, saved, full):
        """AC-26: the card was already ticked with the same identity."""
        saved.compute['cards'] = [record_of(xt_23())]
        saved.compute['alerts'] = [{'id': 'b' * 32, 'kind': 'card_lost', 'key': KEY_XT_23, 'name': 'x'}]
        assert store.save('gpu', 'split', [KEY_XT_03, KEY_XT_23], [KEY_XT_03, KEY_XT_23], full, 0) == 1

    def test_held_card_saved_as_another_model_is_newly_ticked(self, saved, full):
        saved.compute['cards'] = [dict(record_of(xt_23()), device_id=0x7448)]
        saved.compute['alerts'] = [{'id': 'b' * 32, 'kind': 'card_changed', 'key': KEY_XT_23, 'name': 'x'}]
        with pytest.raises(store.CardHeld):
            store.save('gpu', 'split', [KEY_XT_23], [KEY_XT_23], full, 0)

    @pytest.mark.parametrize('method, gpu_mode, ticked, shown, text', [
        ('gpu', 'split', [KEY_ELSEWHERE], [KEY_ELSEWHERE], 'is not on this machine'),
        ('gpu', 'split', [KEY_XT_03], [KEY_XT_23], 'was ticked but not shown'),
        ('gpu', 'split', [], [KEY_XT_03], 'needs at least one ticked graphics card'),
        ('cpu_gpu', 'split', [], [], 'needs at least one ticked graphics card'),
        ('GPU', 'split', [KEY_XT_03], [KEY_XT_03], 'compute method'),
        ('gpu', 'both', [KEY_XT_03], [KEY_XT_03], 'GPU mode'),
    ])
    def test_invalid_choice(self, saved, full, method, gpu_mode, ticked, shown, text):
        with pytest.raises(ValueError, match=text):
            store.save(method, gpu_mode, ticked, shown, full, 0)
        assert saved.writes == []

    def test_more_than_16_shown_keys(self, saved, full):
        shown = [f'pci:0000:{i:02x}:00.0' for i in range(0x60, 0x71)]
        with pytest.raises(ValueError, match='At most 16'):
            store.save('cpu', 'split', [], shown, full, 0)
        assert saved.writes == []
