# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract: the worker's real ``cards`` report passes the manager's real
validation unchanged (worker card selection FR-CR-1, FR-CR-3, AC-15).

Each report is built by ``cards.payload`` from a real ``config.json`` and
faked probes (``_card_contract_harness``), sent through JSON, and fed to
``heartbeat_cards.validate_card_report``. Every entry and every field must
survive: the stored value equals the report without ``request_outcome``,
with ``raised_at`` in ``isoformat()`` form (``stored_form``). The unit
tests on either side check each half against its own fixtures; these check
the two halves against each other.
"""
import pytest

from sethlans_worker_agent.cards import payload
from sethlans_worker_agent.config_compute import ENV_METHOD, parse
from workers.views.heartbeat_cards import validate_card_report

from ._card_contract_harness import (
    ALERT_IDS, BLENDER_VERSION, KEY_BL_LOST, KEY_CHANGED, KEY_GONE, KEY_HELD, KEY_LOST, KEY_NEW, KEY_OK,
    KEY_UNTICKED, MACHINE_BLENDER, NVIDIA, RTX, alert, blender_record, machine_compute, os_card, record,
    stored_form, worker_machine,
)

LONG = 'Graphics card name with every printable character !"#$%&\'()*+,-./:;=?@[\\]^_{|}~ ' * 4


@pytest.fixture
def machine(monkeypatch, tmp_path):
    yield from worker_machine(monkeypatch, tmp_path)


def _passes_unchanged(report):
    """Validate ``report`` as the manager does; assert nothing was dropped or changed; return the stored value."""
    stored = validate_card_report(report)
    assert stored is not None
    assert set(report) - set(stored) == {'request_outcome'}
    for name in ('cards', 'alerts', 'notices', 'warnings'):
        assert len(stored[name]) == len(report[name]), name
    assert stored == stored_form(report)
    return stored


def _states(report):
    return {entry['key']: entry['state'] for entry in report['cards']}


class TestEveryStateAndKind:

    def test_a_card_in_every_state_alerts_of_each_kind_notices_and_warning(self, machine):
        machine.install()
        machine.seed(machine_compute())

        report = machine.report()

        assert _states(report) == {KEY_OK: 'ok', KEY_UNTICKED: 'unticked', KEY_CHANGED: 'changed',
                                   KEY_HELD: 'held', KEY_BL_LOST: 'blender_lost', KEY_NEW: 'unticked',
                                   KEY_LOST: 'lost'}
        assert sorted(entry['kind'] for entry in report['alerts']) == sorted(ALERT_IDS)
        assert sorted((n['kind'], n['key']) for n in report['notices']) == [('card_gone', KEY_GONE),
                                                                            ('card_new', KEY_NEW)]
        assert [w['code'] for w in report['warnings']] == ['engine_not_tied']
        assert {entry['backend'] for entry in report['cards']} == {'OPTIX', 'HIP', 'ONEAPI', None}
        stored = _passes_unchanged(report)
        lost = {entry['key']: entry for entry in stored['cards']}[KEY_LOST]
        assert (lost['vendor_id'], lost['device_id'], lost['ticked']) == (NVIDIA, 0x2204, True)
        assert stored['revision'] == 7 and stored['method'] == 'cpu_gpu'

    def test_failed_os_probe_sends_unknown_and_its_warning(self, machine):
        machine.install(os_cards=None)
        machine.seed(machine_compute())

        report = machine.report()

        states = _states(report)
        assert states[KEY_LOST] == 'unknown' and states[KEY_OK] == 'ok'
        assert all(entry['vendor_id'] is None and entry['device_id'] is None
                   for entry in report['cards'] if entry['key'] != KEY_LOST)
        assert [w['code'] for w in report['warnings']][0] == 'os_probe_failed'
        assert [n['kind'] for n in report['notices']] == []  # every Blender card is known; no card_gone
        _passes_unchanged(report)

    def test_apple_gpu_on_macos(self, machine):
        machine.install(os_cards=[os_card(None, 'Apple M2 Max', None, None)],
                        blender_cards=[{'id': 'METAL_Apple M2 Max', 'name': 'Apple M2 Max', 'type': 'METAL'}],
                        platform='darwin')
        machine.seed({'method': 'gpu', 'gpu_mode': 'split', 'revision': 2, 'known': ['apple-gpu'],
                      'cards': [record('apple-gpu', 'Apple M2 Max')], 'alerts': []})

        report = machine.report()

        assert [(c['key'], c['state'], c['backend']) for c in report['cards']] == [('apple-gpu', 'ok', 'METAL')]
        _passes_unchanged(report)

    def test_environment_override_and_manager_changes_switch(self, machine, monkeypatch):
        machine.install()
        machine.seed(machine_compute(allow_manager_changes=False))
        monkeypatch.setenv(ENV_METHOD, 'cpu')

        report = machine.report()

        assert (report['env_override'], report['manager_changes_allowed'], report['method']) == (True, False, 'cpu')
        _passes_unchanged(report)

    def test_no_saved_choice_yet(self, machine):
        machine.install()  # no config.json: a worker that never saved a choice

        report = machine.report()

        assert report['method'] == 'cpu' and report['revision'] == 0
        assert {entry['state'] for entry in report['cards']} == {'unticked'}
        _passes_unchanged(report)

    def test_raised_at_written_with_z_is_normalised(self, machine):
        machine.install()
        machine.seed(machine_compute(alerts=[alert('card_lost', KEY_HELD, 'x', raised_at='2026-11-19T08:00:00Z')]))

        stored = _passes_unchanged(machine.report())

        assert stored['alerts'][0]['raised_at'] == '2026-11-19T08:00:00+00:00'


def _sixteen_cards():
    """16 OS cards Blender offers, names over every cap; the even ones are ticked."""
    keys = [f'pci:0000:{index:02x}:00.0' for index in range(1, 17)]
    os_cards = [os_card(key, f'{index:02d} {LONG}', NVIDIA, 0x2704) for index, key in enumerate(keys)]
    blender = [blender_record(key, f'{index:02d} {LONG}'[:100], 'OPTIX') for index, key in enumerate(keys)]
    return keys, os_cards, blender


class TestAtTheCaps:

    def test_sixteen_cards_alerts_and_notices_with_every_text_at_its_cap(self, machine):
        keys, os_cards, blender = _sixteen_cards()
        ticked = keys[::2]
        kinds = sorted(ALERT_IDS)
        # One alert per (kind, key): the worker keeps one per id and per (kind, key) (FR-CK-5).
        alerts = [alert(kinds[i // 8], ticked[i % 8], LONG, LONG + LONG, alert_id=f'{i:032x}') for i in range(16)]
        machine.install(os_cards=os_cards, blender_cards=blender)
        machine.seed({'method': 'gpu', 'gpu_mode': 'combined', 'revision': 2 ** 31 - 1,
                      'cards': [record(key, f'{i * 2:02d} {LONG}', NVIDIA, 0x2704) for i, key in enumerate(ticked)],
                      'known': [f'pci:0000:{index:02x}:00.0' for index in range(0x80, 0x90)],
                      'alerts': alerts})

        report = machine.report()

        for name in ('cards', 'alerts', 'notices'):
            assert len(report[name]) == 16, name
        assert all(len(entry['name']) == 128 for entry in report['cards'] + report['alerts'] + report['notices'])
        assert all(len(entry['detail']) == 200 for entry in report['alerts'])
        _passes_unchanged(report)

    def test_sixteen_warnings_and_every_list_full_through_shape(self):
        """The worker sends two warning codes today; ``shape`` caps any number of them at 16."""
        keys = [f'pci:0000:{index:02x}:00.0' for index in range(40)]
        answer = {
            'method': 'gpu', 'gpu_mode': 'split', 'revision': 2 ** 31 - 1, 'rebuild_waiting': True, 'problem': LONG,
            'cards': [{'key': key, 'name': LONG, 'vendor_id': 0xFFFF, 'device_id': 0, 'backend': 'CUDA',
                       'note': LONG, 'ticked': True, 'state': 'ok', 'screen': True, 'primary_screen': False}
                      for key in keys],
            'missing': [],
            'alerts': [alert('card_lost_blender', key, LONG, LONG, alert_id=f'{i:032x}') for i, key in enumerate(keys)],
            'notices': [{'kind': 'card_new', 'key': key, 'name': LONG} for key in keys],
            'warnings': [{'code': 'engine_not_tied', 'key': key, 'text': LONG} for key in keys],
        }

        report = payload.shape(answer, parse({}, {}), os_probe_failed=True)

        for name in ('cards', 'alerts', 'notices', 'warnings'):
            assert len(report[name]) == 16, name
        assert report['warnings'][0]['code'] == 'os_probe_failed' and len(report['problem']) == 200
        _passes_unchanged(report)


class TestManagerNormalisation:

    def test_html_characters_in_a_probed_name_become_question_marks_and_nothing_else_changes(self, machine):
        """The manager's sanitiser also replaces ``<``, ``>`` and the backtick (FR-CR-3); the worker keeps them."""
        name = 'Card <b>`x`</b>'
        machine.install(os_cards=[os_card(KEY_OK, name, NVIDIA, 0x2704)],
                        blender_cards=[blender_record(KEY_OK, RTX, 'OPTIX')])

        report = machine.report()
        stored = validate_card_report(report)

        named = report['cards'] + report['notices']  # the card and its card_new notice
        assert [entry['name'] for entry in named] == [name, name]
        assert [entry['name'] for entry in stored['cards'] + stored['notices']] == ['Card ?b??x??/b?'] * 2
        for entry in stored['cards'] + stored['notices']:
            entry['name'] = name
        assert stored == stored_form(report)


def _held_by_a_hand_edited_alert(machine, **fields):
    """The report of ``MACHINE_OS`` with one card_lost alert holding KEY_HELD, its fields changed (``None``: removed)."""
    machine.install()
    held = alert('card_lost', KEY_HELD, 'AMD Radeon RX 7900 XT')
    for name, value in fields.items():
        if value is None:
            del held[name]
        else:
            held[name] = value
    machine.seed(machine_compute(alerts=[held]))
    report = machine.report()
    assert [entry['id'] for entry in report['alerts']] == [ALERT_IDS['card_lost']]
    assert _states(report)[KEY_HELD] == 'held'  # the worker holds the card for this alert
    return report


class TestHandEditedAlerts:
    """``config_compute`` keeps an alert whatever its ``raised_at``; the worker holds the card for it."""

    def test_alert_without_raised_at_is_stored_with_null(self, machine):
        report = _held_by_a_hand_edited_alert(machine, raised_at=None)
        assert report['alerts'][0]['raised_at'] is None
        _passes_unchanged(report)

    @pytest.mark.parametrize('raised_at', ['2026-11-19T08:00:00', 'yesterday'], ids=['naive', 'not-a-date'])
    def test_alert_with_a_raised_at_that_is_not_an_aware_datetime_reaches_the_manager(self, machine, raised_at):
        """The alert is kept with ``raised_at`` null, so the admin can still see and dismiss it (FR-CR-5).

        Dropping it would leave the worker holding the card for an alert the manager never shows.
        """
        report = _held_by_a_hand_edited_alert(machine, raised_at=raised_at)

        stored = validate_card_report(report)

        assert [entry['id'] for entry in stored['alerts']] == [ALERT_IDS['card_lost']]
        assert stored['alerts'][0]['raised_at'] is None


def test_blender_version_reaches_the_note_of_a_card_blender_does_not_offer(machine):
    machine.install(blender_cards=MACHINE_BLENDER[:1])
    report = machine.report()
    note = {entry['key']: entry['note'] for entry in report['cards']}[KEY_UNTICKED]
    assert note == f'not offered by Blender {BLENDER_VERSION}'
    _passes_unchanged(report)
