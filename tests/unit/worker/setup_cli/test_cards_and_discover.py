# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``--setup-cards`` and ``--setup-discover`` (setup-rebuild FR-WK-1).

The OS card probe, the saved ``compute`` section and the multicast listener
are replaced; no Blender, card or network is touched. ``config.json`` is a
file in an isolated worker folder (``worker_home``).
"""
import json

import pytest

from sethlans_worker_agent import config_compute, unreal_switch
from sethlans_worker_agent.cards import inventory, os_probe, screens
from sethlans_worker_agent.setup_cli import cards_cmd, discover_cmd
from sethlans_worker_agent.setup_cli.stdio import CliError
from tests.unit.worker._card_choice import AMD, KEY_XT_23, card, inv, xt_23
from tests.unit.worker._page_password import fast_hash

TOKEN, FINGERPRINT = 't' * 40, 'ab' * 32
NAME_64 = 'Studio ' + 'x' * 57  # the longest name --setup-apply takes
MANAGER = {'api_token': TOKEN, 'cert_fingerprint': FINGERPRINT, 'manager_id': '00000000-0000-0000-0000-000000000042',
           'host': 'manager.example', 'port': 8443, 'name': NAME_64}
ENROLLMENT = {'enrolled': True, 'manager_id': MANAGER['manager_id'], 'manager_name': NAME_64,
              'host': 'manager.example', 'port': 8443}


class TestCards:

    def test_probe_runs_no_blender(self, monkeypatch):
        """FR-WK-1: the card list is the OS probe joined with no Blender device list (Blender may be missing)."""
        built = []
        monkeypatch.setattr(os_probe, 'probe', lambda: ('os-card',))
        monkeypatch.setattr(inventory, 'build', lambda *args: built.append(args) or inv())
        monkeypatch.setattr(screens, 'marked', lambda found: found)
        assert cards_cmd.probe() == inv()
        assert built == [(('os-card',), None, None)]

    def test_answer_lists_cards_and_the_saved_choice(self, monkeypatch, worker_home):
        """FR-WK-1: each card with the address ``--setup-apply`` takes, plus the saved choice in its field names."""
        monkeypatch.delenv(unreal_switch.ENV_VAR, raising=False)
        monkeypatch.setattr(unreal_switch, 'saved', lambda: True)
        apple = card('apple-gpu', 'Apple M2 Max')
        monkeypatch.setattr(cards_cmd, 'probe', lambda: inv(xt_23(), apple))
        monkeypatch.setattr(config_compute, '_read_raw', lambda: {
            'method': 'gpu', 'gpu_mode': 'combined', 'revision': 3, 'cards': [{'key': KEY_XT_23}]})

        answer = cards_cmd.run({})

        first, second = answer['cards']
        assert (first['address'], first['key'], first['pci'], first['brand'], first['vendor_id']) == \
            ('0000:23:00.0', KEY_XT_23, '23:00.0', 'AMD', AMD)
        assert (second['address'], second['pci']) == ('apple-gpu', None)
        assert answer['saved'] == {'compute': 'gpu', 'cards': ['0000:23:00.0'], 'combine': True,
                                   'env_override': False, 'config_read_failed': False}
        assert (answer['os_probe_failed'], answer['unreal_enabled'], answer['unreal_enabled_env']) == \
            (False, True, False)

    def test_input_must_be_empty(self):
        """FR-WK-1: ``--setup-cards`` takes ``{}``; any field is ``bad_input``."""
        with pytest.raises(CliError) as caught:
            cards_cmd.run({'seconds': 5})
        assert caught.value.code == 'bad_input'


@pytest.fixture
def stored(worker_home, monkeypatch):
    """``write(data)`` puts ``data`` in the isolated ``config.json``; the card probe finds no card."""
    monkeypatch.setattr(cards_cmd, 'probe', lambda: inv())

    def write(data):
        (worker_home / 'config.json').write_text(json.dumps(data), encoding='utf-8')
    return write


class TestStoredForReset:
    """``enrollment`` and ``ui_password_set``: what Reset setup may keep (FR-RS-3, FR-WK-1)."""

    @pytest.mark.parametrize('section', [
        None, {k: v for k, v in MANAGER.items() if k != 'api_token'}, dict(MANAGER, api_token=''),
        dict(MANAGER, api_token=7), {k: v for k, v in MANAGER.items() if k != 'cert_fingerprint'},
    ], ids=['no-file', 'no-token', 'empty-token', 'token-not-text', 'token-without-fingerprint'])
    def test_enrollment_is_null_without_a_token_and_fingerprint(self, stored, section):
        """FR-RS-3: only a stored non-empty ``manager.api_token`` with a ``cert_fingerprint`` counts as enrolled
        (a token without a pinned certificate cannot be used)."""
        if section is not None:
            stored({'manager': section})
        assert cards_cmd.run({})['enrollment'] is None

    def test_enrollment_holds_the_stored_values_and_no_secret(self, stored):
        """FR-RS-3, FR-WZ-1: the stored manager is shown (64-character name kept); never token or fingerprint."""
        stored({'manager': MANAGER})
        answer = cards_cmd.run({})
        assert answer['enrollment'] == ENROLLMENT
        assert TOKEN not in json.dumps(answer) and FINGERPRINT not in json.dumps(answer)

    @pytest.mark.parametrize('key, value, field', [
        ('name', 'x' * 65, 'manager_name'), ('name', 'Stu\ndio', 'manager_name'), ('name', 42, 'manager_name'),
        ('port', '8443', 'port'), ('port', True, 'port'), ('port', 0, 'port'), ('port', 65536, 'port'),
        ('manager_id', 42, 'manager_id'), ('manager_id', 'x' * 65, 'manager_id'), ('manager_id', 'id\x00', 'manager_id'),
        ('host', '', 'host'), ('host', 'h' * 254, 'host'),
    ], ids=['name-65', 'name-control-char', 'name-not-text', 'port-text', 'port-bool', 'port-0', 'port-65536',
            'id-not-text', 'id-65', 'id-control-char', 'host-empty', 'host-254'])
    def test_an_unusable_stored_value_is_null(self, stored, key, value, field):
        """FR-RS-3: a stored value of the wrong type, too long (name 64, host 253, manager id 64), not printable,
        or a port outside 1-65535 is ``null``."""
        stored({'manager': dict(MANAGER, **{key: value})})
        assert cards_cmd.run({})['enrollment'] == dict(ENROLLMENT, **{field: None})

    @pytest.mark.parametrize('has_hash', [True, False], ids=['stored', 'none'])
    def test_ui_password_set_tells_whether_a_hash_is_stored(self, stored, has_hash):
        """FR-RS-3, FR-WZ-1: Reset setup may keep the worker page password only when one is stored."""
        stored({'worker': {'ui_password_hash': fast_hash('pw')}} if has_hash else {'worker': {}})
        assert cards_cmd.run({})['ui_password_set'] is has_hash

    def test_unreadable_config_is_config_unreadable_before_the_probe(self, stored, worker_home, mocker):
        """FR-WK-1: a ``config.json`` that cannot be read answers ``config_unreadable``; no card is probed."""
        (worker_home / 'config.json').write_text('{"manager": ', encoding='utf-8')
        probe = mocker.patch.object(cards_cmd, 'probe')
        with pytest.raises(CliError) as caught:
            cards_cmd.run({})
        assert caught.value.code == 'config_unreadable'
        probe.assert_not_called()


class FakeListener:
    """``MulticastListener`` stand-in: records the timeout, answers ``found`` or raises ``error``."""

    found = {}
    error = None
    timeouts = []

    def __init__(self, timeout):
        FakeListener.timeouts.append(timeout)

    def discover(self):
        if FakeListener.error is not None:
            raise FakeListener.error
        return FakeListener.found


@pytest.fixture
def listener(monkeypatch):
    monkeypatch.setattr(discover_cmd, 'MulticastListener', FakeListener)
    monkeypatch.setattr(FakeListener, 'found', {})
    monkeypatch.setattr(FakeListener, 'error', None)
    monkeypatch.setattr(FakeListener, 'timeouts', [])
    return FakeListener


class TestDiscover:

    @pytest.mark.parametrize('request_body, seconds', [({}, 5.0), ({'seconds': 15}, 15.0), ({'seconds': 0.5}, 0.5)],
                             ids=['default', 'max', 'fraction'])
    def test_listens_for_the_asked_time(self, listener, request_body, seconds):
        """FR-WK-1: ``seconds`` defaults to 5 and may be at most 15."""
        assert discover_cmd.run(request_body) == {'managers': []}
        assert listener.timeouts == [seconds]

    @pytest.mark.parametrize('value', [0, 15.5, True, '5', None], ids=['zero', 'over-15', 'bool', 'text', 'null'])
    def test_other_seconds_are_bad_input(self, listener, value):
        """FR-WK-1: ``seconds`` must be a number more than 0 and at most 15; nothing is listened for otherwise."""
        with pytest.raises(CliError) as caught:
            discover_cmd.run({'seconds': value})
        assert caught.value.code == 'bad_input' and listener.timeouts == []

    def test_managers_sorted_by_name_then_id(self, listener):
        """FR-WK-1, FR-SEC-9: each manager with name, host, id and port, in a stable order."""
        listener.found = {
            'id-b': {'manager_id': 'id-b', 'name': 'Studio', 'host': 's.example', 'ip': '10.0.0.2', 'port': 8443,
                     'version': '1.0'},
            'id-a': {'manager_id': 'id-a', 'name': 'Studio'},
            'id-c': {'manager_id': 'id-c', 'name': 'Lab', 'ip': '10.0.0.1'},
        }
        managers = discover_cmd.run({})['managers']
        assert [m['manager_id'] for m in managers] == ['id-c', 'id-a', 'id-b']
        assert managers[1] == {'name': 'Studio', 'host': '', 'ip': '', 'port': 8080, 'manager_id': 'id-a',
                               'version': ''}

    def test_text_fields_are_printable_and_clipped(self, listener):
        """FR-WK-1, FR-SEC-9 [security review N3]: announcement text reaches the setup form, so control
        characters are removed and each field is cut (name 64, host 253, ip 45, manager id 64, version 32);
        not text is ``''``."""
        listener.found = {'x': {'name': 'N\n' + 'n' * 99, 'host': 'h' * 300, 'ip': '\x1b' + 'i' * 99,
                                'manager_id': 'm' * 99, 'version': 'v' * 99, 'port': 8443},
                          'y': {'name': 42, 'manager_id': ['id']}}
        assert discover_cmd.run({})['managers'] == [
            {'name': '', 'host': '', 'ip': '', 'port': 8080, 'manager_id': '', 'version': ''},
            {'name': 'N' + 'n' * 63, 'host': 'h' * 253, 'ip': 'i' * 45, 'port': 8443, 'manager_id': 'm' * 64,
             'version': 'v' * 32}]

    @pytest.mark.parametrize('port, expected', [(1, 1), (65535, 65535), (0, None), (65536, None), ('8443', None),
                                                (True, None)],
                             ids=['1', '65535', '0', '65536', 'text', 'bool'])
    def test_port_outside_1_to_65535_is_null(self, listener, port, expected):
        """FR-WK-1, FR-SEC-9 [security review N3]: ``port`` is a number 1-65535 or ``null`` (8080 only when missing)."""
        listener.found = {'id': {'manager_id': 'id', 'name': 'Studio', 'port': port}}
        assert discover_cmd.run({})['managers'][0]['port'] == expected

    def test_socket_that_cannot_open_is_discovery_failed(self, listener):
        """FR-WK-1: a listening socket that cannot be opened is exit 1 ``discovery_failed``."""
        listener.error = OSError('address in use')
        with pytest.raises(CliError) as caught:
            discover_cmd.run({})
        assert caught.value.code == 'discovery_failed'
