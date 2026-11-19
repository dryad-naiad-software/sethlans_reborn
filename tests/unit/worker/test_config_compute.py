# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``config_compute`` (FR-CC-1, FR-CC-2): the ``compute`` section,
its ``SETHLANS_COMPUTE_*`` overrides (one key each) and the invalid-value rule:
any invalid key runs the worker on the CPU, logged at ERROR once per process,
except the switch, whose invalid value means ``false``.
"""
import logging

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import os_probe

ENV_METHOD, ENV_GPU_MODE = config_compute.ENV_METHOD, config_compute.ENV_GPU_MODE
ENV_CARDS, ENV_ALLOW = config_compute.ENV_CARDS, config_compute.ENV_ALLOW
KEY_A, KEY_B = 'pci:0000:03:00.0', 'pci:0000:23:00.0'
RECORD_A = {'key': KEY_A, 'vendor_id': 0x1002, 'device_id': 0x744C, 'revision_id': 0xC8, 'name': 'RX 7900 XT'}
RECORD_B = {'key': KEY_B, 'vendor_id': 0x1002, 'device_id': 0x744C, 'revision_id': 0xCC, 'name': 'RX 7900 XT'}
ALERT = {'id': 'a' * 32, 'kind': 'card_lost', 'key': KEY_A, 'name': 'RX 7900 XT'}
LOGGER = 'sethlans_worker_agent.config_compute'


@pytest.fixture(autouse=True)
def _fresh_once_log():
    os_probe.reset_logged_for_tests()
    yield
    os_probe.reset_logged_for_tests()


def _gpu_file(**overrides):
    raw = {'method': 'gpu', 'gpu_mode': 'combined', 'cards': [RECORD_A, RECORD_B], 'known': [KEY_A, KEY_B],
           'revision': 3, 'allow_manager_changes': True, 'last_request': None, 'alerts': []}
    raw.update(overrides)
    return raw


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]


class TestFile:

    @pytest.mark.parametrize('raw', [None, {}])
    def test_no_section_is_cpu_split_revision_0_switch_on(self, raw):
        section = config_compute.parse(raw, {})
        assert (section.method, section.gpu_mode, section.cards, section.known) == ('cpu', 'split', (), ())
        assert (section.revision, section.allow_manager_changes, section.last_request) == (0, True, None)
        assert (section.env_override, section.env_cards, section.allow_from_env, section.invalid) == \
            (False, False, False, ())

    def test_valid_section_is_taken_as_saved(self):
        request = {'id': 'r1', 'outcome': 'applied'}
        section = config_compute.parse(_gpu_file(last_request=request, alerts=[ALERT],
                                                 allow_manager_changes=False), {})
        assert (section.method, section.gpu_mode, section.revision) == ('gpu', 'combined', 3)
        assert section.cards == (RECORD_A, RECORD_B)
        assert section.ticked_keys == (KEY_A, KEY_B)
        assert section.known == (KEY_A, KEY_B)
        assert section.last_request == request
        assert section.alerts == (ALERT,)
        assert section.allow_manager_changes is False
        assert section.invalid == ()

    def test_record_without_ids_is_valid(self):
        section = config_compute.parse(_gpu_file(cards=[{'key': 'apple-gpu', 'name': 'Apple M2'}]), {})
        assert section.method == 'gpu'
        assert section.ticked_keys == ('apple-gpu',)

    @pytest.mark.parametrize('overrides, key', [
        ({'method': 'GPU'}, 'compute.method'),
        ({'method': None}, 'compute.method'),
        ({'gpu_mode': 'both'}, 'compute.gpu_mode'),
        ({'revision': -1}, 'compute.revision'),
        ({'revision': True}, 'compute.revision'),
        ({'revision': '3'}, 'compute.revision'),
        ({'revision': 2 ** 31}, 'compute.revision'),
        ({'cards': 'pci:0000:03:00.0'}, 'compute.cards'),
        ({'cards': [{'key': 'pci:0000:03:00.0', 'vendor_id': 0x10000}]}, 'compute.cards'),
        ({'cards': [{'key': 'pci:0000:03:00.0', 'device_id': True}]}, 'compute.cards'),
        ({'cards': [{'key': 'pci:0000:03:00.0', 'revision_id': 0x100}]}, 'compute.cards'),
        ({'cards': [{'key': 'pci:0000:03:00.0', 'name': 5}]}, 'compute.cards'),
        ({'cards': [{'key': 'pci:0000:0A:00.0'}]}, 'compute.cards'),
        ({'cards': [{'key': '0000:03:00.0'}]}, 'compute.cards'),
        ({'cards': [RECORD_A, dict(RECORD_A, name='again')]}, 'compute.cards'),
        ({'known': ['bogus']}, 'compute.known'),
        ({'known': [KEY_A] * 17}, 'compute.known'),
        ({'alerts': [dict(ALERT, kind='card_gone')]}, 'compute.alerts'),
        ({'alerts': [dict(ALERT, id='A' * 32)]}, 'compute.alerts'),
        ({'last_request': 'applied'}, 'compute.last_request'),
    ])
    def test_any_invalid_key_runs_on_cpu_and_names_the_key(self, caplog, overrides, key):
        caplog.set_level(logging.ERROR, logger=LOGGER)

        section = config_compute.parse(_gpu_file(**overrides), {})

        assert section.method == 'cpu'
        assert key in section.invalid
        assert _errors(caplog) == [f"Invalid graphics card setting {key}; this worker renders on the CPU only "
                                   f"until it is fixed."]

    def test_section_that_is_not_an_object_runs_on_cpu(self):
        section = config_compute.parse(['gpu'], {})
        assert section.method == 'cpu'
        assert section.invalid == ('compute',)

    def test_more_than_16_cards_is_invalid(self):
        records = [{'key': f'pci:0000:{i:02x}:00.0'} for i in range(1, 18)]
        section = config_compute.parse(_gpu_file(cards=records), {})
        assert section.method == 'cpu'
        assert len(section.cards) == 16

    def test_duplicate_card_keeps_one_record(self):
        section = config_compute.parse(_gpu_file(cards=[RECORD_A, RECORD_A]), {})
        assert section.ticked_keys == (KEY_A,)

    @pytest.mark.parametrize('value', ['true', 1, None, 'yes'])
    def test_invalid_switch_means_false_and_keeps_the_method(self, caplog, value):
        caplog.set_level(logging.ERROR, logger=LOGGER)

        section = config_compute.parse(_gpu_file(allow_manager_changes=value), {})

        assert section.allow_manager_changes is False
        assert section.method == 'gpu'
        assert section.invalid == ('compute.allow_manager_changes',)
        assert _errors(caplog) == ["Invalid graphics card setting compute.allow_manager_changes; the manager may "
                                   "not change the card choice until it is fixed."]

    def test_error_is_logged_once_per_process(self, caplog):
        caplog.set_level(logging.ERROR, logger=LOGGER)
        for _ in range(3):
            config_compute.parse(_gpu_file(method='turbo'), {})
        assert len(_errors(caplog)) == 1


class TestEnvironment:

    def test_method_override_keeps_the_other_saved_keys(self):
        section = config_compute.parse(_gpu_file(), {ENV_METHOD: ' CPU_GPU '})
        assert (section.method, section.gpu_mode, section.cards) == ('cpu_gpu', 'combined', (RECORD_A, RECORD_B))
        assert (section.env_override, section.env_cards) == (True, False)

    def test_gpu_mode_override_keeps_the_saved_method(self):
        section = config_compute.parse(_gpu_file(), {ENV_GPU_MODE: 'Split'})
        assert (section.method, section.gpu_mode) == ('gpu', 'split')
        assert section.env_override is True

    def test_cards_override_replaces_only_the_cards(self):
        section = config_compute.parse(_gpu_file(), {ENV_CARDS: '0000:23:00.0'})
        assert section.cards == ({'key': KEY_B},)
        assert (section.method, section.gpu_mode, section.known) == ('gpu', 'combined', (KEY_A, KEY_B))
        assert (section.env_override, section.env_cards) == (True, True)

    @pytest.mark.parametrize('text, keys', [
        ('apple-gpu', ('apple-gpu',)),
        ('APPLE-GPU', ('apple-gpu',)),
        ('0000:0A:00.0', ('pci:0000:0a:00.0',)),
        ('00000000:23:00.0', (KEY_B,)),
        (' 0000:23:00.0 , 0000:03:00.0 ', (KEY_B, KEY_A)),
        ('0000:03:00.0,0000:03:00.0', (KEY_A,)),
        ('pci:0000:03:00.0', (KEY_A,)),
        ('PCI:0000:23:00.0,pci:0000:03:00.0', (KEY_B, KEY_A)),
        ('', ()),
    ])
    def test_cards_accepts_apple_gpu_and_full_or_upper_case_addresses(self, text, keys):
        section = config_compute.parse({'method': 'gpu'}, {ENV_CARDS: text})
        assert section.ticked_keys == keys
        assert section.method == 'gpu'
        assert section.invalid == ()

    @pytest.mark.parametrize('text', ['RX 7900 XT', '0000:03:00.0,bogus', '0000:01:20.0',
                                      ','.join(f'0000:{i:02x}:00.0' for i in range(1, 18))])
    def test_invalid_cards_runs_on_cpu_with_no_card(self, caplog, text):
        caplog.set_level(logging.ERROR, logger=LOGGER)

        section = config_compute.parse(_gpu_file(), {ENV_CARDS: text})

        assert (section.method, section.cards) == ('cpu', ())
        assert section.invalid == (ENV_CARDS,)
        assert _errors(caplog) == [f"Invalid graphics card setting {ENV_CARDS}; this worker renders on the CPU "
                                   f"only until it is fixed."]

    @pytest.mark.parametrize('name, value', [(ENV_METHOD, 'gpu-only'), (ENV_GPU_MODE, 'all')])
    def test_invalid_method_or_mode_runs_on_cpu(self, name, value):
        section = config_compute.parse(_gpu_file(), {name: value})
        assert section.method == 'cpu'
        assert section.invalid == (name,)

    @pytest.mark.parametrize('text, expected', [('true', True), (' TRUE ', True), ('false', False),
                                                ('False', False)])
    def test_switch_override(self, text, expected):
        section = config_compute.parse(_gpu_file(allow_manager_changes=not expected), {ENV_ALLOW: text})
        assert section.allow_manager_changes is expected
        assert (section.allow_from_env, section.env_override, section.method) == (True, False, 'gpu')

    @pytest.mark.parametrize('text', ['yes', '1', 'on', ''])
    def test_invalid_switch_override_means_false_and_keeps_the_method(self, text):
        section = config_compute.parse(_gpu_file(), {ENV_ALLOW: text})
        assert section.allow_manager_changes is False
        assert section.method == 'gpu'
        assert section.invalid == (ENV_ALLOW,)

    def test_env_wins_over_an_invalid_saved_value_but_still_runs_on_cpu(self):
        section = config_compute.parse(_gpu_file(gpu_mode='both'), {ENV_GPU_MODE: 'split'})
        assert section.gpu_mode == 'split'
        assert section.method == 'cpu'

    def test_load_reads_the_saved_section_and_os_environ(self, monkeypatch):
        monkeypatch.setattr(config_compute, '_read_raw', lambda: _gpu_file())
        monkeypatch.setenv(ENV_GPU_MODE, 'split')

        section = config_compute.load()

        assert (section.method, section.gpu_mode, section.revision) == ('gpu', 'split', 3)
        assert section.env_override is True
