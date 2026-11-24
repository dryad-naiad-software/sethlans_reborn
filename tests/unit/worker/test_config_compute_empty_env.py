# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Empty ``SETHLANS_COMPUTE_*`` variables count as unset (#305).

A Docker ``.env`` line ``VAR=`` passes the variable with an empty value. For
each of the four variables an empty or blank value gives the same section as
no variable at all: no override, nothing invalid, no ERROR line, and the saved
keys stay. A non-empty invalid value is still invalid and logged at ERROR;
that includes a ``SETHLANS_COMPUTE_CARDS`` of commas only, which names no card.
"""
import logging

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import os_probe

ENV_METHOD, ENV_GPU_MODE = config_compute.ENV_METHOD, config_compute.ENV_GPU_MODE
ENV_CARDS, ENV_ALLOW = config_compute.ENV_CARDS, config_compute.ENV_ALLOW
ALL = (ENV_METHOD, ENV_GPU_MODE, ENV_CARDS, ENV_ALLOW)
BLANKS = ['', ' ', '\t', ' \r\n ']
KEY_A = 'pci:0000:03:00.0'
RECORD_A = {'key': KEY_A, 'vendor_id': 0x1002, 'device_id': 0x744C, 'revision_id': 0xC8, 'name': 'RX 7900 XT'}
SAVED = {'method': 'gpu', 'gpu_mode': 'combined', 'cards': [RECORD_A], 'known': [KEY_A], 'revision': 2,
         'allow_manager_changes': False}
LOGGER = 'sethlans_worker_agent.config_compute'


@pytest.fixture(autouse=True)
def _fresh_once_log():
    os_probe.reset_logged_for_tests()
    yield
    os_probe.reset_logged_for_tests()


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]


class TestBlankIsUnset:

    @pytest.mark.parametrize('name', ALL)
    @pytest.mark.parametrize('value', BLANKS)
    def test_blank_variable_gives_the_saved_section(self, caplog, name, value):
        caplog.set_level(logging.DEBUG, logger=LOGGER)

        section = config_compute.parse(SAVED, {name: value})

        assert section == config_compute.parse(SAVED, {})
        assert (section.method, section.gpu_mode, section.cards) == ('gpu', 'combined', (RECORD_A,))
        assert section.allow_manager_changes is False
        assert (section.env_override, section.env_cards, section.allow_from_env, section.invalid) == \
            (False, False, False, ())
        assert _errors(caplog) == []

    @pytest.mark.parametrize('value', BLANKS)
    def test_all_four_blank_give_the_defaults_without_a_file(self, caplog, value):
        caplog.set_level(logging.DEBUG, logger=LOGGER)

        section = config_compute.parse(None, dict.fromkeys(ALL, value))

        assert section == config_compute.parse(None, {})
        assert (section.method, section.cards, section.allow_manager_changes) == ('cpu', (), True)
        assert _errors(caplog) == []

    def test_blank_cards_beside_a_set_method_keeps_the_saved_cards(self):
        section = config_compute.parse(SAVED, {ENV_METHOD: 'cpu_gpu', ENV_CARDS: ''})
        assert (section.method, section.cards) == ('cpu_gpu', (RECORD_A,))
        assert (section.env_override, section.env_cards) == (True, False)

    def test_blank_switch_beside_set_cards_keeps_the_saved_switch(self):
        section = config_compute.parse(SAVED, {ENV_CARDS: '0000:23:00.0', ENV_ALLOW: ' '})
        assert section.ticked_keys == ('pci:0000:23:00.0',)
        assert (section.allow_manager_changes, section.allow_from_env, section.env_cards) == (False, False, True)

    @pytest.mark.parametrize('name', ALL)
    def test_load_reads_a_blank_os_variable_as_unset(self, monkeypatch, name):
        monkeypatch.setattr(config_compute, '_read_raw', lambda: dict(SAVED))
        monkeypatch.setenv(name, '')

        section = config_compute.load()

        assert section == config_compute.parse(SAVED, {})
        assert section.env_override is False


class TestNonEmptyInvalidIsStillAnError:

    @pytest.mark.parametrize('name, value, effect', [
        (ENV_METHOD, 'turbo', 'this worker renders on the CPU only'),
        (ENV_GPU_MODE, 'both', 'this worker renders on the CPU only'),
        (ENV_CARDS, 'bogus', 'this worker renders on the CPU only'),
        (ENV_CARDS, ' 0000:03:00.0, x ', 'this worker renders on the CPU only'),
        (ENV_ALLOW, 'yes', 'the manager may not change the card choice'),
    ])
    def test_invalid_value_is_logged_and_named(self, caplog, name, value, effect):
        caplog.set_level(logging.ERROR, logger=LOGGER)

        section = config_compute.parse(SAVED, {name: value})

        assert section.invalid == (name,)
        assert _errors(caplog) == [f"Invalid graphics card setting {name}; {effect} until it is fixed."]
        if name == ENV_ALLOW:
            assert (section.method, section.allow_manager_changes) == ('gpu', False)
        else:
            assert section.method == 'cpu'

    def test_padded_valid_value_is_used(self):
        section = config_compute.parse(SAVED, {ENV_METHOD: '  cpu_gpu  ', ENV_GPU_MODE: '\tsplit\n'})
        assert (section.method, section.gpu_mode, section.invalid) == ('cpu_gpu', 'split', ())


class TestCardsNamingNoCard:

    @pytest.mark.parametrize('text', [',', ' , ,', ',,,', '\t,\t'])
    def test_commas_only_is_invalid_and_runs_on_cpu(self, caplog, text):
        caplog.set_level(logging.ERROR, logger=LOGGER)

        section = config_compute.parse(SAVED, {ENV_CARDS: text})

        assert (section.method, section.cards, section.env_cards) == ('cpu', (), True)
        assert section.invalid == (ENV_CARDS,)
        assert _errors(caplog) == [f"Invalid graphics card setting {ENV_CARDS}; this worker renders on the CPU "
                                   f"only until it is fixed."]

    @pytest.mark.parametrize('text, keys', [('pci:0000:01:00.0,', ('pci:0000:01:00.0',)),
                                            (',0000:03:00.0, ,', (KEY_A,))])
    def test_a_named_card_with_stray_commas_is_valid(self, caplog, text, keys):
        caplog.set_level(logging.ERROR, logger=LOGGER)

        section = config_compute.parse(SAVED, {ENV_CARDS: text})

        assert section.ticked_keys == keys
        assert (section.method, section.invalid, section.env_cards) == ('gpu', (), True)
        assert _errors(caplog) == []

    @pytest.mark.parametrize('text', BLANKS)
    def test_blank_cards_is_unset_not_invalid(self, text):
        section = config_compute.parse(SAVED, {ENV_CARDS: text})
        assert (section.cards, section.env_cards, section.invalid) == ((RECORD_A,), False, ())
