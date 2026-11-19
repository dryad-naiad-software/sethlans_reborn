# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-CW-5: the shape checks of a card save request (``cards_view.parse_body``).

Every refusal goes through ``POST /api/control/cards`` and must answer 400
with its one plain sentence, run no probe and write nothing. The accepted
edge cases (``apple-gpu``, C-22a; 16 entries; revision 2^31-1) go to
``parse_body`` directly. A bad body under an environment override is still
400: the shape check comes before the 409.
"""
import json

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_pages import RUNTIME_POST, SETUP_PATH, body, call, fake_probes, fake_saved

NOT_OBJECT = "The request body must be a JSON object."
UNKNOWN = "The request has a field this page does not accept."
MISSING = "The request must give method, gpu_mode, cards, shown and revision."
BAD_METHOD = "The compute method must be cpu, gpu or cpu_gpu."
BAD_MODE = "The GPU mode must be split or combined."
REVISION = "The revision must be a whole number from 0 to 2147483647."
NOT_SHOWN = "Every ticked graphics card must also be in the shown list."


def _list_text(name):
    return f"The {name} list must be a list of at most 16 PCI addresses."


def _entry_text(name):
    return f"Each entry in the {name} list must be a PCI address such as 0000:01:00.0."


def _twice_text(name):
    return f"The {name} list names a graphics card more than once."


def _addresses(count):
    return [f'0000:{bus:02x}:00.0' for bus in range(1, count + 1)]


def _raw(**changes):
    return json.dumps(body(**changes)).encode()


def _without(name):
    value = body()
    del value[name]
    return json.dumps(value).encode()


REFUSED = [
    pytest.param(b'[]', NOT_OBJECT, id='array'),
    pytest.param(b'"text"', NOT_OBJECT, id='string'),
    pytest.param(b'null', NOT_OBJECT, id='null'),
    pytest.param(b'{"method": ', NOT_OBJECT, id='broken-json'),
    pytest.param(b'\xff\xfe\xfd', NOT_OBJECT, id='not-utf8'),
    pytest.param(_raw(allow_manager_changes=True), UNKNOWN, id='allow_manager_changes'),
    pytest.param(_raw(extra=1), UNKNOWN, id='unknown-field'),
    *[pytest.param(_without(name), MISSING, id=f'missing-{name}') for name in cards_view._FIELDS],
    pytest.param(_raw(method='GPU'), BAD_METHOD, id='method-case'),
    pytest.param(_raw(method=''), BAD_METHOD, id='method-empty'),
    pytest.param(_raw(method=['gpu']), BAD_METHOD, id='method-list'),
    pytest.param(_raw(method=None), BAD_METHOD, id='method-null'),
    pytest.param(_raw(gpu_mode='Combined'), BAD_MODE, id='mode-case'),
    pytest.param(_raw(gpu_mode=True), BAD_MODE, id='mode-bool'),
    pytest.param(_raw(cards='0000:23:00.0'), _list_text('cards'), id='cards-not-list'),
    pytest.param(_raw(shown=None), _list_text('shown'), id='shown-null'),
    pytest.param(_raw(cards=[], shown=_addresses(17)), _list_text('shown'), id='shown-17'),
    pytest.param(_raw(cards=_addresses(17), shown=_addresses(16)), _list_text('cards'), id='cards-17'),
    pytest.param(_raw(shown=['0000:23:00.0', '0000:23:00.0']), _twice_text('shown'), id='shown-twice'),
    pytest.param(_raw(cards=['0000:0a:00.0', '0000:0A:00.0'], shown=['0000:0a:00.0']),
                 _twice_text('cards'), id='cards-twice-after-normalising'),
    pytest.param(_raw(shown=['0000:23:00.0' + ' ' * 21]), _entry_text('shown'), id='entry-33-chars'),
    pytest.param(_raw(cards=['pci:0000:23:00.0']), _entry_text('cards'), id='pci-prefix'),
    pytest.param(_raw(cards=[35]), _entry_text('cards'), id='number'),
    pytest.param(_raw(shown=[None]), _entry_text('shown'), id='null-entry'),
    pytest.param(_raw(cards=[[0, 0x23, 0]]), _entry_text('cards'), id='d3dkmt-triple'),
    pytest.param(_raw(cards=['APPLE-GPU'], shown=['APPLE-GPU']), _entry_text('cards'), id='apple-gpu-case'),
    pytest.param(_raw(cards=['23:00.0']), _entry_text('cards'), id='no-domain'),
    pytest.param(_raw(revision=True), REVISION, id='revision-true'),
    pytest.param(_raw(revision=False), REVISION, id='revision-false'),
    pytest.param(_raw(revision=-1), REVISION, id='revision-negative'),
    pytest.param(_raw(revision=2 ** 31), REVISION, id='revision-2^31'),
    pytest.param(_raw(revision=1.0), REVISION, id='revision-float'),
    pytest.param(_raw(revision='0'), REVISION, id='revision-text'),
    pytest.param(_raw(shown=['0000:03:00.0']), NOT_SHOWN, id='ticked-not-shown'),
]


class TestRefusedOverRuntimePost:

    @pytest.fixture(autouse=True)
    def _open(self, setup_complete, fresh_shutdown_event, bearer_header, monkeypatch):
        self.auth = bearer_header
        self.saved = fake_saved(monkeypatch)
        self.probes = fake_probes(monkeypatch)

    @pytest.mark.parametrize('raw, text', REFUSED)
    def test_refused_with_its_sentence_and_nothing_saved(self, raw, text):
        assert call(RUNTIME_POST, 'POST', headers=self.auth, raw=raw) == (400, {'error': text})
        assert self.saved[1] == [] and self.probes == []
        assert self.saved[0]['compute'] == {'revision': 0}

    def test_bad_body_under_env_override_is_400_not_409(self, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        status, answer = call(RUNTIME_POST, 'POST', body(revision=-1), self.auth)
        assert (status, answer['error']) == (400, REVISION)
        assert self.saved[1] == []


class TestSetupPostOrder:

    @pytest.fixture(autouse=True)
    def _fakes(self, monkeypatch):
        self.saved = fake_saved(monkeypatch)
        self.probes = fake_probes(monkeypatch)

    def test_bad_body_under_env_override_is_400(self, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_CARDS, '0000:23:00.0')
        assert call(SETUP_PATH, 'POST', body(method='Gpu')) == (400, {'error': BAD_METHOD})
        assert self.saved[1] == [] and self.probes == []

    def test_good_body_under_env_override_is_409(self, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_GPU_MODE, 'split')
        assert call(SETUP_PATH, 'POST', body()) == (409, {'error': cards_view.ENV_OVERRIDE_TEXT})
        assert self.saved[1] == []


class TestAccepted:

    def test_valid_body_gives_normalised_keys(self):
        fields = cards_view.parse_body(_raw(cards=['0000:2A:00.0'], shown=['0000:03:00.0', '0000:2a:00.0']))
        assert fields == {'method': 'gpu', 'gpu_mode': 'split', 'cards': ('pci:0000:2a:00.0',),
                          'shown': ('pci:0000:03:00.0', 'pci:0000:2a:00.0'), 'revision': 0}

    def test_eight_digit_domain_is_normalised(self):
        fields = cards_view.parse_body(_raw(cards=[], shown=['0001000A:01:00.0']))
        assert fields['shown'] == ('pci:1000a:01:00.0',)

    def test_apple_gpu_is_accepted(self):
        fields = cards_view.parse_body(_raw(cards=['apple-gpu'], shown=['apple-gpu']))
        assert fields['cards'] == ('apple-gpu',) and fields['shown'] == ('apple-gpu',)

    def test_sixteen_entries_are_accepted(self):
        fields = cards_view.parse_body(_raw(cards=_addresses(16), shown=_addresses(16)))
        assert len(fields['cards']) == 16 and fields['cards'][0] == 'pci:0000:01:00.0'

    @pytest.mark.parametrize('revision', [0, 2 ** 31 - 1])
    def test_revision_bounds_are_accepted(self, revision):
        assert cards_view.parse_body(_raw(revision=revision))['revision'] == revision

    @pytest.mark.parametrize('method', config_compute.METHODS)
    def test_every_method_is_accepted(self, method):
        assert cards_view.parse_body(_raw(method=method))['method'] == method

    def test_empty_lists_are_accepted(self):
        fields = cards_view.parse_body(_raw(method='cpu', gpu_mode='combined', cards=[], shown=[]))
        assert (fields['cards'], fields['shown'], fields['gpu_mode']) == ((), (), 'combined')
