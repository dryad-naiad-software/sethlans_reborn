# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_worker --setup-apply`` (setup-rebuild FR-WK-1, FR-WK-3, FR-WK-5, FR-SEC-8).

Everything outside ``apply_cmd`` and its field checks is replaced: the card
probe (two AMD cards), the computer name, the hash (no PBKDF2 here; the hash
has its own tests), the stored hash, the enrollment
(``setup_cli.enroll.enroll_pairs``, the one function ``enrollment_start``
uses too) and ``config_store.set_many``. The saved ``compute`` section is
empty (the worker conftest).
"""
import io
import json
import logging
import sys

import pytest

from sethlans_worker_agent import config_compute, config_store, enrollment_client, setup_cli, unreal_switch
from sethlans_worker_agent.cards.in_use import record_of
from sethlans_worker_agent.setup_cli import apply_cmd, cards_cmd, enroll, stdio
from sethlans_worker_agent.setup_cli.stdio import CliError
from sethlans_worker_agent.web_ui import auth, password_hash
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, inv, xt_03, xt_23

PASSWORD = 'correct-horse-battery'
KEY = 'QX7M-4F9K-2PBR-N8TW'
MANAGER_ID = '00000000-0000-0000-0000-000000000042'
FINGERPRINT = 'ab' * 32
ENROLL_PAIRS = [('manager.api_token', 'token-from-the-manager'), ('manager.cert_fingerprint', FINGERPRINT),
                ('manager.manager_id', MANAGER_ID), ('manager.host', 'manager.example'), ('manager.port', 8080)]


def _request(**changes):
    value = {'compute': 'gpu', 'cards': ['0000:23:00.0'], 'combine': False, 'unreal_enabled': True,
             'ui_password': PASSWORD, 'manager_url': 'https://manager.example:8080', 'enrollment_key': KEY,
             'manager_name': 'Studio'}
    value.update(changes)
    return {name: field for name, field in value.items() if field is not _DROP}


_DROP = object()


@pytest.fixture
def world(monkeypatch, mocker):
    """The collaborators of ``apply_cmd.run``; returns the mocks a test inspects."""
    monkeypatch.delenv(unreal_switch.ENV_VAR, raising=False)
    monkeypatch.setattr(apply_cmd.socket, 'gethostname', lambda: 'render-node')
    monkeypatch.setattr(cards_cmd, 'probe', lambda: inv(xt_03(), xt_23()))
    monkeypatch.setattr(password_hash, 'make', lambda password: 'HASH-OF-NEW')
    mocks = mocker.MagicMock()
    mocks.current_hash = mocker.patch.object(auth, 'current_hash', return_value='STORED-HASH')
    mocks.set_many = mocker.patch.object(config_store, 'set_many')
    mocks.clear_cache = mocker.patch.object(auth, 'clear_cache')
    mocks.enroll = mocker.patch.object(enroll, 'enroll_pairs', return_value=(
        {'api_token': 'token-from-the-manager', 'cert_fingerprint': FINGERPRINT, 'manager_id': MANAGER_ID},
        list(ENROLL_PAIRS)))
    return mocks


class TestSuccess:

    def test_enrolls_then_writes_everything_in_one_set_many(self, world):
        """FR-WK-1: checks, then enrollment, then exactly one ``set_many``; the result cache is cleared (FR-SEC-8)."""
        answer = apply_cmd.run(_request())

        assert answer == {'enrolled': True, 'manager_id': MANAGER_ID, 'manager_name': 'Studio',
                          'cert_fingerprint': FINGERPRINT, 'status': None}
        world.enroll.assert_called_once_with('manager.example', 8080, KEY, 'render-node', 'Studio')
        [(pairs,), _kwargs] = world.set_many.call_args
        assert world.set_many.call_count == 1
        assert pairs == [
            ('compute.method', 'gpu'), ('compute.gpu_mode', 'split'), ('compute.cards', [record_of(xt_23())]),
            ('compute.known', [KEY_XT_03, KEY_XT_23]), ('compute.revision', 1),
            ('unreal.enabled', True), ('worker.ui_password_hash', 'HASH-OF-NEW'), *ENROLL_PAIRS,
        ]
        check = _kwargs['expect']['compute.revision']  # chunk 4 review: compare-and-set on the revision read
        assert check(0) is True and check(1) is False
        world.clear_cache.assert_called_once_with()

    @pytest.mark.parametrize('sent, shown', [('pending', 'pending'), (_DROP, None)], ids=['status-sent', 'no-status'])
    def test_status_of_the_enroll_answer_is_passed_through_or_null(self, world, sent, shown):
        """FR-WK-5: the enroll answer's ``status`` comes back as is, else ``null``."""
        result = {'api_token': 't', 'cert_fingerprint': FINGERPRINT, 'manager_id': MANAGER_ID}
        if sent is not _DROP:
            result['status'] = sent
        world.enroll.return_value = (result, list(ENROLL_PAIRS))
        assert apply_cmd.run(_request())['status'] == shown

    @pytest.mark.parametrize('name', [_DROP, 'Other'], ids=['no-name', 'name-without-key'])
    def test_without_a_key_and_a_new_password_only_the_choice_is_saved(self, world, name):
        """FR-WK-1, FR-WK-3: no enrollment; ``ui_password`` null keeps the stored hash. FR-RS-3: no ``manager.*``
        is written, so a stored ``manager.name`` stays, even when a ``manager_name`` is sent."""
        answer = apply_cmd.run(_request(manager_url=_DROP, enrollment_key=_DROP, ui_password=None,
                                        manager_name=name))
        assert answer == {'enrolled': False, 'manager_id': None, 'manager_name': None, 'cert_fingerprint': None,
                          'status': None}
        world.enroll.assert_not_called()
        dotted = [key for key, _value in world.set_many.call_args.args[0]]
        assert 'worker.ui_password_hash' not in dotted
        assert not any(key.startswith('manager.') for key in dotted)

    def test_environment_set_choices_are_not_saved(self, world, monkeypatch):
        """FR-WK-1: the card choice and the Unreal switch are left alone while environment variables set them."""
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        monkeypatch.setenv(unreal_switch.ENV_VAR, 'true')
        apply_cmd.run(_request())
        dotted = [name for name, _value in world.set_many.call_args.args[0]]
        assert not any(name.startswith('compute.') for name in dotted) and 'unreal.enabled' not in dotted
        assert 'worker.ui_password_hash' in dotted
        assert world.set_many.call_args.kwargs['expect'] is None  # no card choice written, nothing to compare


class TestNothingWrittenOnFailure:

    @pytest.mark.parametrize('changes', [
        {'compute': 'GPU'}, {'cards': ['pci:0000:23:00.0']}, {'cards': ['0000:09:00.0']}, {'cards': '0000:23:00.0'},
        {'combine': 'yes'}, {'unreal_enabled': None}, {'ui_password': 'short'}, {'ui_password': 'render-node'},
        {'manager_url': _DROP}, {'enrollment_key': _DROP}, {'enrollment_key': '  '},
        {'manager_url': 'http://manager.example'}, {'extra': 1}, {'manager_name': 'x' * 65},
    ], ids=['method', 'card-prefix', 'card-not-here', 'cards-not-list', 'combine', 'unreal', 'password-short',
            'password-hostname', 'key-without-url', 'url-without-key', 'blank-key', 'http-url', 'unknown-field',
            'long-name'])
    def test_a_failed_check_writes_nothing_and_does_not_enroll(self, world, changes):
        """FR-WK-1: every field is checked before anything is sent or written."""
        with pytest.raises(CliError) as caught:
            apply_cmd.run(_request(**changes))
        assert caught.value.code == 'bad_input'
        world.enroll.assert_not_called()
        world.set_many.assert_not_called()

    def test_a_card_an_alert_holds_is_card_held(self, world, monkeypatch):
        """FR-WK-1: a card held by an open alert is refused (``card_held``), as on the settings page."""
        alert = {'id': 'a' * 32, 'kind': 'card_lost', 'key': KEY_XT_23}
        monkeypatch.setattr(config_compute, '_read_raw', lambda: {'revision': 0, 'alerts': [alert]})
        with pytest.raises(CliError) as caught:
            apply_cmd.run(_request())
        assert caught.value.code == 'card_held'
        world.set_many.assert_not_called()

    @pytest.mark.parametrize('stored, code', [
        (None, 'bad_input'), (config_store.ConfigReadError('broken'), 'config_unreadable'),
    ], ids=['none-stored', 'unreadable'])
    def test_keeping_a_password_needs_one_stored(self, world, stored, code):
        """FR-WK-4: ``ui_password`` null with no stored password (or an unreadable file) is refused."""
        if isinstance(stored, Exception):
            world.current_hash.side_effect = stored
        else:
            world.current_hash.return_value = stored
        with pytest.raises(CliError) as caught:
            apply_cmd.run(_request(ui_password=None))
        assert caught.value.code == code
        world.enroll.assert_not_called()
        world.set_many.assert_not_called()

    def test_a_failed_enrollment_writes_nothing(self, world):
        """FR-WK-1, OQ-7: an enrollment failure gives its code and nothing is saved."""
        world.enroll.side_effect = enrollment_client.InvalidKeyError('The enrollment key is not valid.')
        with pytest.raises(CliError) as caught:
            apply_cmd.run(_request())
        assert (caught.value.code, caught.value.message) == ('invalid_key', 'The enrollment key is not valid.')
        world.set_many.assert_not_called()
        world.clear_cache.assert_not_called()

    @pytest.mark.parametrize('error, code', [
        (config_store.ConfigLockTimeoutError('lock busy'), 'save_failed'),
        (OSError(28, 'No space left on device'), 'save_failed'),
        (config_store.ConfigChangedError('compute.revision', 2), 'card_changed'),
    ], ids=['lock-timeout', 'oserror', 'card-choice-changed'])
    def test_a_failed_save_has_its_code(self, world, error, code):
        """FR-WK-1: a write that fails answers ``save_failed``; chunk 4 review (concurrency): a card choice the
        worker page saved meanwhile answers ``card_changed``. Nothing is saved either way."""
        world.set_many.side_effect = error
        with pytest.raises(CliError) as caught:
            apply_cmd.run(_request())
        assert caught.value.code == code
        world.clear_cache.assert_not_called()

    def test_card_changed_is_exit_1_with_one_json_answer(self, world, run_main):
        """FR-WK-1: ``card_changed`` reaches stdout as ``{"error": "card_changed", "message": ...}`` with exit 1."""
        world.set_many.side_effect = config_store.ConfigChangedError('compute.revision', 2)
        code, out = run_main(_request())
        answer = json.loads(out)
        assert (code, set(answer), answer['error']) == (1, {'error', 'message'}, 'card_changed')
        assert 'nothing was saved' in answer['message']


@pytest.fixture
def run_main(monkeypatch):
    """``setup_cli.main(['--setup-apply'])`` on ``request``; ``(exit code, stdout text)``."""
    monkeypatch.setattr(stdio, 'log_to_worker_log', lambda: None)

    def run(request):
        out = io.BytesIO()
        monkeypatch.setattr(stdio, 'binary_streams', lambda: (io.BytesIO(json.dumps(request).encode()), out))
        saved = sys.stdout
        try:
            code = setup_cli.main(['--setup-apply'])
        finally:
            sys.stdout = saved
        return code, out.getvalue().decode('ascii')
    return run


class TestNoSecretsOut:

    @pytest.mark.parametrize('enroll_error', [None, enrollment_client.EnrollmentError('The manager refused.')],
                             ids=['success', 'enrollment-failed'])
    def test_password_and_key_appear_in_no_log_line_and_no_answer(self, world, run_main, caplog, enroll_error):
        """FR-WK-2, FR-SEC-5: the key and the password never reach the log or stdout."""
        world.enroll.side_effect = enroll_error
        with caplog.at_level(logging.DEBUG):
            code, out = run_main(_request())
        assert code == (0 if enroll_error is None else 1)
        assert json.loads(out)
        for secret in (PASSWORD, KEY):
            assert secret not in out and secret not in caplog.text
            assert all(secret not in str(r.args) for r in caplog.records)
