# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``manager/workers/services/setup.py``."""

import configparser
import subprocess
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError

from workers.services.setup import (
    create_admin_user,
    generate_enrollment_key,
    generate_secret_key,
    set_worker_ui_password,
    validate_db_connection,
    verify_admin_authenticates,
    verify_ffmpeg_runs,
    write_manager_ini,
)


# ---- generate_secret_key() ------------------------------------------------

class TestGenerateSecretKey:

    def test_returns_string(self):
        assert isinstance(generate_secret_key(), str)

    def test_sufficient_length(self):
        key = generate_secret_key()
        # token_urlsafe(50) produces ~67 chars; ensure >= 50
        assert len(key) >= 50

    def test_unique_per_call(self):
        keys = {generate_secret_key() for _ in range(50)}
        assert len(keys) == 50


# ---- generate_enrollment_key() -------------------------------------------

class TestGenerateEnrollmentKey:

    def test_returns_crockford_base32(self, mocker):
        mocker.patch(
            'workers.enrollment_key.generate_key',
            return_value='4F9XK2PBQ7M3N8RT',
        )
        mock_row = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.get_or_create.return_value = (mock_row, True)
        mock_model = MagicMock()
        mock_model.objects = mock_mgr
        mocker.patch(
            'workers.models.ManagerSettings', mock_model,
        )
        key = generate_enrollment_key()
        assert key == '4F9XK2PBQ7M3N8RT'
        # Crockford base32 excludes I, L, O, U
        for ch in key:
            assert ch not in 'ILOU'

    def test_persists_to_manager_settings(self, mocker):
        mocker.patch(
            'workers.enrollment_key.generate_key',
            return_value='ABCDEFGH12345678',
        )
        mock_row = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.get_or_create.return_value = (mock_row, True)
        mock_model = MagicMock()
        mock_model.objects = mock_mgr
        mocker.patch(
            'workers.models.ManagerSettings', mock_model,
        )
        generate_enrollment_key()
        assert mock_row.enrollment_key == 'ABCDEFGH12345678'
        mock_row.save.assert_called_once()


# ---- create_admin_user() -------------------------------------------------

class TestCreateAdminUser:

    @pytest.mark.django_db
    def test_creates_superuser(self):
        user = create_admin_user(
            'admin', 'admin@test.com', 'Str0ng!Pass99',
        )
        assert user.is_superuser
        assert user.username == 'admin'

    @pytest.mark.django_db
    def test_raises_on_duplicate_username(self):
        create_admin_user(
            'admin', 'a@test.com', 'Str0ng!Pass99',
        )
        with pytest.raises(ValidationError, match="already taken"):
            create_admin_user(
                'admin', 'b@test.com', 'Str0ng!Pass99',
            )

    @pytest.mark.django_db
    def test_rejects_short_password(self):
        with pytest.raises(ValidationError):
            create_admin_user('admin', 'a@test.com', 'ab')

    @pytest.mark.django_db
    def test_rejects_numeric_only_password(self):
        with pytest.raises(ValidationError):
            create_admin_user('admin', 'a@test.com', '123456789')

    @pytest.mark.django_db
    def test_rejects_common_password(self):
        with pytest.raises(ValidationError):
            create_admin_user('admin', 'a@test.com', 'password')


# ---- write_manager_ini() -------------------------------------------------

class TestWriteManagerIni:

    def test_creates_file(self, tmp_path):
        ini = tmp_path / 'manager.ini'
        result = write_manager_ini(
            {'server.port': '8080'}, ini,
        )
        assert result == ini
        assert ini.exists()

    def test_dot_notation_parsed(self, tmp_path):
        ini = tmp_path / 'manager.ini'
        write_manager_ini(
            {'server.host': '0.0.0.0', 'setup.token': 'abc'},
            ini,
        )
        config = configparser.ConfigParser()
        config.read(ini)
        assert config.get('server', 'host') == '0.0.0.0'
        assert config.get('setup', 'token') == 'abc'

    def test_merges_with_existing(self, tmp_path):
        ini = tmp_path / 'manager.ini'
        write_manager_ini({'server.port': '8080'}, ini)
        write_manager_ini({'server.host': '0.0.0.0'}, ini)
        config = configparser.ConfigParser()
        config.read(ini)
        assert config.get('server', 'port') == '8080'
        assert config.get('server', 'host') == '0.0.0.0'


# ---- validate_db_connection() ---------------------------------------------

class TestValidateDbConnection:

    def test_success(self, mocker):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = (
            lambda s: mock_cursor
        )
        mock_conn.cursor.return_value.__exit__ = (
            lambda s, *a: None
        )
        mock_connections = MagicMock()
        mock_connections.__getitem__ = lambda s, k: mock_conn
        mock_connections.databases = {}
        mocker.patch(
            'django.db.connections', mock_connections,
        )
        validate_db_connection(
            'django.db.backends.sqlite3', ':memory:',
        )

    def test_raises_connection_error_on_failure(self, mocker):
        from django.db.utils import OperationalError
        mock_conn = MagicMock()
        mock_conn.ensure_connection.side_effect = (
            OperationalError("refused")
        )
        mock_connections = MagicMock()
        mock_connections.__getitem__ = lambda s, k: mock_conn
        mock_connections.databases = {}
        mocker.patch(
            'django.db.connections', mock_connections,
        )
        with pytest.raises(ConnectionError, match="refused"):
            validate_db_connection(
                'django.db.backends.postgresql', 'mydb',
                host='bad-host',
            )


# ---- verify_admin_authenticates() ----------------------------------------

class TestVerifyAdminAuthenticates:

    def test_returns_true_for_valid(self, mocker):
        mock_user = MagicMock(is_active=True)
        mocker.patch(
            'workers.services.setup.authenticate',
            return_value=mock_user,
        )
        assert verify_admin_authenticates('admin', 'pass') is True

    def test_returns_false_for_invalid(self, mocker):
        mocker.patch(
            'workers.services.setup.authenticate',
            return_value=None,
        )
        assert verify_admin_authenticates('bad', 'bad') is False


# ---- set_worker_ui_password() --------------------------------------------

class TestSetWorkerUiPassword:

    def test_writes_hash_and_salt(self, tmp_path):
        cfg = tmp_path / 'config.ini'
        set_worker_ui_password(cfg, 'mypassword')
        parser = configparser.ConfigParser()
        parser.read(cfg)
        assert parser.has_option('worker', 'ui_password_hash')
        assert parser.has_option('worker', 'ui_password_salt')
        # PBKDF2 format: hex string
        assert len(parser.get('worker', 'ui_password_hash')) == 64
        assert len(parser.get('worker', 'ui_password_salt')) == 32

    def test_removes_legacy_fields(self, tmp_path):
        cfg = tmp_path / 'config.ini'
        parser = configparser.ConfigParser()
        parser.add_section('worker')
        parser.set('worker', 'ui_token', 'old')
        parser.set('worker', 'ui_password', 'old')
        with open(cfg, 'w') as f:
            parser.write(f)
        set_worker_ui_password(cfg, 'new')
        parser2 = configparser.ConfigParser()
        parser2.read(cfg)
        assert not parser2.has_option('worker', 'ui_token')
        assert not parser2.has_option('worker', 'ui_password')


# ---- verify_ffmpeg_runs() ------------------------------------------------

class TestVerifyFfmpegRuns:

    def test_returns_version_string(self, mocker, tmp_path):
        fake_bin = tmp_path / 'ffmpeg'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            return_value=MagicMock(
                returncode=0,
                stdout='ffmpeg version 6.0\nmore info',
                stderr='',
            ),
        )
        result = verify_ffmpeg_runs(fake_bin)
        assert result == 'ffmpeg version 6.0'

    def test_raises_on_missing_binary(self, tmp_path):
        missing = tmp_path / 'no_ffmpeg'
        with pytest.raises(RuntimeError, match="not found"):
            verify_ffmpeg_runs(missing)

    def test_raises_on_nonzero_exit(self, mocker, tmp_path):
        fake_bin = tmp_path / 'ffmpeg'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            return_value=MagicMock(
                returncode=1, stdout='', stderr='error msg',
            ),
        )
        with pytest.raises(RuntimeError, match="exited with code"):
            verify_ffmpeg_runs(fake_bin)

    def test_raises_on_timeout(self, mocker, tmp_path):
        fake_bin = tmp_path / 'ffmpeg'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            side_effect=subprocess.TimeoutExpired('ffmpeg', 30),
        )
        with pytest.raises(RuntimeError, match="timed out"):
            verify_ffmpeg_runs(fake_bin)

    def test_raises_on_os_error(self, mocker, tmp_path):
        fake_bin = tmp_path / 'ffmpeg'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            side_effect=OSError("Permission denied"),
        )
        with pytest.raises(RuntimeError, match="Failed to execute"):
            verify_ffmpeg_runs(fake_bin)
