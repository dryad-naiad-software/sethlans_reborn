# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_worker_agent.ui_password_env`` (setup-rebuild FR-DK-6).

The worker page password from ``SETHLANS_WORKER_UI_PASSWORD_FILE`` or
``SETHLANS_WORKER_UI_PASSWORD`` at every start. ``config.json`` lives in the
``worker_home`` scratch folder; the computer name is fixed, and
``password_hash.ITERATIONS`` is lowered to the conftest's ``FAST_ROUNDS``, so
the real hash format is written without 1,000,000 PBKDF2 rounds.
"""
import logging
import os

import pytest

from sethlans_worker_agent import agent, config_store, ui_password_env as env_pw
from sethlans_worker_agent.web_ui import auth, password_hash, password_rules
from tests.unit.worker._page_password import FAST_ROUNDS

HOSTNAME = 'render-node-07'
NEW_PASSWORD = 'Render-Page-4f2a'
CAP = env_pw.MAX_PASSWORD_FILE_BYTES


@pytest.fixture(autouse=True)
def _fast_host(monkeypatch):
    """A fixed computer name for the similarity rule, and few PBKDF2 rounds for ``password_hash.make``."""
    monkeypatch.setattr(env_pw.socket, 'gethostname', lambda: HOSTNAME)
    monkeypatch.setattr(password_hash, 'ITERATIONS', FAST_ROUNDS)


def _config_bytes(home):
    path = home / 'config.json'
    return path.read_bytes() if path.exists() else None


def _secret_file(tmp_path, data: bytes) -> str:
    path = tmp_path / 'ui_password'
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize('values, exits', [
    pytest.param({}, False, id='neither'),
    pytest.param({env_pw.PASSWORD_FILE_ENV: '', env_pw.PASSWORD_ENV: ''}, False, id='both-empty'),
    pytest.param({env_pw.PASSWORD_ENV: 'Ab1!'}, True, id='invalid'),
    pytest.param({env_pw.PASSWORD_FILE_ENV: 'MISSING'}, True, id='unreadable-file'),
])
def test_both_variables_removed_from_os_environ(no_page_password, monkeypatch, tmp_path, values, exits):
    """FR-DK-6: both variables leave os.environ every start (set, empty, invalid or unreadable); neither set or
    both empty is no password and no exit; nothing is written in any of these cases."""
    for name, value in values.items():
        monkeypatch.setenv(name, str(tmp_path / 'absent') if value == 'MISSING' else value)

    if exits:
        with pytest.raises(SystemExit) as caught:
            env_pw.apply_or_exit()
        assert caught.value.code == env_pw.UI_PASSWORD_FAILED
    else:
        assert env_pw.apply_or_exit() is None

    assert [name for name in env_pw.ENV_NAMES if name in os.environ] == []
    assert not (no_page_password / 'config.json').exists()


def test_first_start_saves_only_a_django_format_hash(no_page_password, caplog):
    """FR-DK-6, FR-WK-3: the first start saves a pbkdf2_sha256 hash that verifies; the password is in neither
    config.json nor the log."""
    environ = {env_pw.PASSWORD_ENV: NEW_PASSWORD}

    with caplog.at_level(logging.DEBUG):
        assert env_pw.apply_or_exit(environ) == env_pw.SET

    assert environ == {}
    stored = auth.current_hash()
    assert stored.startswith(f'{password_hash.ALGORITHM}${FAST_ROUNDS}$')
    assert password_hash.verify(NEW_PASSWORD, stored)
    assert NEW_PASSWORD not in (no_page_password / 'config.json').read_text(encoding='utf-8')
    assert NEW_PASSWORD not in caplog.text


def test_matching_stored_hash_writes_nothing(page_password, worker_home, mocker):
    """FR-DK-6: the stored hash already matches the password: UNCHANGED and config.json is not written."""
    before = (worker_home / 'config.json').read_bytes()
    save = mocker.spy(config_store, 'set_many')

    assert env_pw.apply_or_exit({env_pw.PASSWORD_ENV: page_password}) == env_pw.UNCHANGED

    save.assert_not_called()
    assert (worker_home / 'config.json').read_bytes() == before


def test_changed_password_replaces_hash_and_clears_auth_cache(page_password, worker_home):
    """FR-DK-6: the environment owns the password: a different one replaces the hash and the remembered
    correct password is forgotten (FR-SEC-8 cache)."""
    old = auth.current_hash()
    auth._cache.store(old, page_password)

    assert env_pw.apply_or_exit({env_pw.PASSWORD_ENV: NEW_PASSWORD}) == env_pw.SET

    new = auth.current_hash()
    assert password_hash.verify(NEW_PASSWORD, new)
    assert not password_hash.verify(page_password, new)
    assert not auth._cache.hit(old, page_password)


@pytest.mark.parametrize('data, expected', [
    pytest.param(b'File-Secret-9c1d\r\n', 'File-Secret-9c1d', id='crlf-dropped'),
    pytest.param(b'File-Secret-9c1d\n\n', 'File-Secret-9c1d\n', id='only-one-line-end-dropped'),
    pytest.param(b'x' * CAP, 'x' * CAP, id='exactly-64-KiB'),
])
def test_password_file_text(tmp_path, data, expected):
    """FR-DK-6: the file is UTF-8, one trailing line end (LF or CRLF) is dropped, 64 KiB is still accepted."""
    assert env_pw._read_password_file(_secret_file(tmp_path, data)) == expected


def test_password_file_wins_over_plain_variable(no_page_password, tmp_path, caplog):
    """FR-DK-6: both set: the file's password is saved and one WARNING says so, without either password."""
    environ = {env_pw.PASSWORD_FILE_ENV: _secret_file(tmp_path, b'File-Secret-9c1d\n'),
               env_pw.PASSWORD_ENV: 'Plain-Secret-3c4d'}

    with caplog.at_level(logging.DEBUG):
        assert env_pw.apply_or_exit(environ) == env_pw.SET

    assert password_hash.verify('File-Secret-9c1d', auth.current_hash())
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and 'using the file' in warnings[0].getMessage()
    assert 'File-Secret-9c1d' not in caplog.text and 'Plain-Secret-3c4d' not in caplog.text


def _plain(password):
    return lambda home, tmp_path, mocker: {env_pw.PASSWORD_ENV: password}


def _file(data):
    return lambda home, tmp_path, mocker: {env_pw.PASSWORD_FILE_ENV: _secret_file(tmp_path, data)}


def _missing_file(home, tmp_path, mocker):
    return {env_pw.PASSWORD_FILE_ENV: str(tmp_path / 'absent')}


def _broken_config(home, tmp_path, mocker):
    (home / 'config.json').write_text('{not json', encoding='utf-8')
    return {env_pw.PASSWORD_ENV: 'Secret-7e8f-ok'}


def _save_fails(error):
    def setup(home, tmp_path, mocker):
        mocker.patch.object(config_store, 'set_many', side_effect=error)
        return {env_pw.PASSWORD_ENV: 'Secret-7e8f-ok'}
    return setup


@pytest.mark.parametrize('setup, secret, reason', [
    pytest.param(_plain('Ab1!x'), 'Ab1!x', password_rules.TOO_SHORT, id='rule-too-short'),
    pytest.param(_plain(HOSTNAME + '!'), HOSTNAME + '!', password_rules.TOO_SIMILAR, id='rule-like-computer-name'),
    pytest.param(_missing_file, None, 'cannot be read', id='file-missing'),
    pytest.param(_file(b'Secret-7e8f' + b'x' * CAP), 'Secret-7e8f', f'is larger than {CAP} bytes',
                 id='file-over-64-KiB'),
    pytest.param(_file(b'Secret-7e8f\xff'), 'Secret-7e8f', 'is not UTF-8 text', id='file-not-utf8'),
    pytest.param(_broken_config, 'Secret-7e8f', "settings file could not be read", id='config-unreadable'),
    pytest.param(_save_fails(OSError(28, 'No space left on device')), 'Secret-7e8f', 'could not be saved',
                 id='save-oserror'),
    pytest.param(_save_fails(config_store.ConfigLockTimeoutError('lock busy')), 'Secret-7e8f',
                 'could not be saved', id='save-lock-timeout'),
])
def test_unusable_password_exits_5_with_reason(no_page_password, tmp_path, mocker, caplog, setup, secret, reason):
    """FR-DK-6: a rule break, a missing, too large or non-UTF-8 file, an unreadable config.json or a failed save
    stops the worker with exit 5 and the reason, never the password, and saves no hash."""
    environ = setup(no_page_password, tmp_path, mocker)
    config_before = _config_bytes(no_page_password)

    with caplog.at_level(logging.DEBUG), pytest.raises(SystemExit) as caught:
        env_pw.apply_or_exit(environ)

    assert caught.value.code == env_pw.UI_PASSWORD_FAILED == 5
    critical = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1 and reason in critical[0]
    if secret is not None:
        assert secret not in caplog.text
    assert _config_bytes(no_page_password) == config_before
    assert environ == {}


@pytest.mark.parametrize('password_exit, expected', [
    pytest.param(True, ['configure_logging', 'apply_or_exit'], id='exit-5-stops-there'),
    pytest.param(False, ['configure_logging', 'apply_or_exit', 'take_key', 'signal', 'session_monitor',
                         'setup_phase', 'graceful_shutdown'], id='then-the-enrollment-key'),
])
def test_agent_main_takes_secrets_from_environment_first(mocker, password_exit, expected):
    """FR-DK-6: agent.main applies the environment password after logging is set up and before signal handlers,
    the session monitor, TLS, Waitress, Caddy and enrollment (all in _run_setup_phase); exit 5 stops it there.
    FR-WK-6, FR-SEC-6: the enrollment key is taken out of os.environ right after it, before anything starts."""
    from sethlans_worker_agent import enrollment_start
    order = mocker.Mock()
    mocker.patch.object(agent, 'configure_logging', order.configure_logging)
    mocker.patch.object(env_pw, 'apply_or_exit', order.apply_or_exit)
    if password_exit:
        order.apply_or_exit.side_effect = SystemExit(env_pw.UI_PASSWORD_FAILED)
    mocker.patch.object(enrollment_start, 'take_key', order.take_key)
    mocker.patch.object(agent.signal, 'signal', order.signal)
    mocker.patch('sethlans_worker_agent.idle_detection.session_win32.start_session_monitor', order.session_monitor)
    mocker.patch.object(agent, '_run_setup_phase', order.setup_phase)
    order.setup_phase.return_value = False  # shutdown before enrollment: main ends there
    mocker.patch.object(agent, '_graceful_shutdown', order.graceful_shutdown)

    if password_exit:
        with pytest.raises(SystemExit) as caught:
            agent.main(['--loglevel', 'INFO'])
        assert caught.value.code == env_pw.UI_PASSWORD_FAILED
    else:
        agent.main(['--loglevel', 'INFO'])

    assert list(dict.fromkeys(call[0] for call in order.mock_calls)) == expected
