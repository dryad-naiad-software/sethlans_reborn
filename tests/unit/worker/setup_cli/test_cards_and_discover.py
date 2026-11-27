# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``--setup-cards`` and ``--setup-discover`` (setup-rebuild FR-WK-1).

The OS card probe, the saved ``compute`` section and the multicast listener
are replaced; no Blender, card or network is touched.
"""
import pytest

from sethlans_worker_agent import config_compute, unreal_switch
from sethlans_worker_agent.cards import inventory, os_probe, screens
from sethlans_worker_agent.setup_cli import cards_cmd, discover_cmd
from sethlans_worker_agent.setup_cli.stdio import CliError
from tests.unit.worker._card_choice import AMD, KEY_XT_23, card, inv, xt_23


class TestCards:

    def test_probe_runs_no_blender(self, monkeypatch):
        """FR-WK-1: the card list is the OS probe joined with no Blender device list (Blender may be missing)."""
        built = []
        monkeypatch.setattr(os_probe, 'probe', lambda: ('os-card',))
        monkeypatch.setattr(inventory, 'build', lambda *args: built.append(args) or inv())
        monkeypatch.setattr(screens, 'marked', lambda found: found)
        assert cards_cmd.probe() == inv()
        assert built == [(('os-card',), None, None)]

    def test_answer_lists_cards_and_the_saved_choice(self, monkeypatch):
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

    def test_socket_that_cannot_open_is_discovery_failed(self, listener):
        """FR-WK-1: a listening socket that cannot be opened is exit 1 ``discovery_failed``."""
        listener.error = OSError('address in use')
        with pytest.raises(CliError) as caught:
            discover_cmd.run({})
        assert caught.value.code == 'discovery_failed'
