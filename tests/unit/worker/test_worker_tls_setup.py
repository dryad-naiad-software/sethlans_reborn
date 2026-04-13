# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``worker/sethlans_worker_agent/tls_setup.py``."""
import os
import platform
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shared.cert_utils import CertificateError


def _fake_cert():
    """Return a mock certificate object."""
    cert = MagicMock()
    cert.public_bytes = MagicMock(return_value=b'fake-der')
    return cert


FAKE_FP = 'a' * 64


# --- get_tls_config --------------------------------------------------------

class TestGetTlsConfig:

    def test_both_configured(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_CERT_FILE',
            '/some/cert.pem',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_KEY_FILE',
            '/some/key.pem',
        )
        from sethlans_worker_agent.tls_setup import get_tls_config
        cert, key = get_tls_config()
        assert cert == '/some/cert.pem'
        assert key == '/some/key.pem'

    def test_neither_configured(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_CERT_FILE', '',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_KEY_FILE', '',
        )
        from sethlans_worker_agent.tls_setup import get_tls_config
        cert, key = get_tls_config()
        assert cert is None
        assert key is None

    def test_only_cert_warns_and_falls_back(self, mocker, caplog):
        import logging
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_CERT_FILE',
            '/cert.pem',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_KEY_FILE', '',
        )
        caplog.set_level(logging.WARNING)
        from sethlans_worker_agent.tls_setup import get_tls_config
        cert, key = get_tls_config()
        assert cert is None and key is None
        assert any('key_file' in r.message for r in caplog.records)

    def test_only_key_warns_and_falls_back(self, mocker, caplog):
        import logging
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_CERT_FILE', '',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config.TLS_KEY_FILE',
            '/key.pem',
        )
        caplog.set_level(logging.WARNING)
        from sethlans_worker_agent.tls_setup import get_tls_config
        cert, key = get_tls_config()
        assert cert is None and key is None
        assert any('cert_file' in r.message for r in caplog.records)


# --- get_tls_dir -----------------------------------------------------------

class TestGetTlsDir:

    def test_default_uses_data_dir(self, mocker):
        mocker.patch.dict(os.environ, {}, clear=False)
        mocker.patch.dict(
            os.environ,
            {'SETHLANS_WORKER_TLS_DATA_DIR': ''},
            clear=False,
        )
        # Remove the env var entirely so os.getenv returns None
        os.environ.pop('SETHLANS_WORKER_TLS_DATA_DIR', None)
        mock_data_dir = MagicMock(
            return_value=Path('/fake/data'),
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.config_store.get_data_dir',
            mock_data_dir,
        )
        from sethlans_worker_agent.tls_setup import get_tls_dir
        result = get_tls_dir()
        assert result == Path('/fake/data/tls')

    def test_env_var_override(self, mocker):
        abs_path = '/custom/tls'
        if platform.system() == 'Windows':
            abs_path = 'C:\\custom\\tls'
        mocker.patch.dict(
            os.environ,
            {'SETHLANS_WORKER_TLS_DATA_DIR': abs_path},
        )
        from sethlans_worker_agent.tls_setup import get_tls_dir
        result = get_tls_dir()
        assert result == Path(abs_path)

    def test_relative_path_raises_value_error(self, mocker):
        mocker.patch.dict(
            os.environ,
            {'SETHLANS_WORKER_TLS_DATA_DIR': 'relative/path'},
        )
        from sethlans_worker_agent.tls_setup import get_tls_dir
        with pytest.raises(ValueError, match='absolute path'):
            get_tls_dir()


# --- _write_fingerprint_file ----------------------------------------------

class TestWriteFingerprintFile:

    def test_writes_fingerprint_content(self, tmp_path):
        from sethlans_worker_agent.tls_setup import _write_fingerprint_file
        _write_fingerprint_file(FAKE_FP, tmp_path)
        fp_file = tmp_path / 'ui_cert_fingerprint.txt'
        assert fp_file.exists()
        assert fp_file.read_text() == FAKE_FP

    def test_overwrites_existing_file(self, tmp_path):
        from sethlans_worker_agent.tls_setup import _write_fingerprint_file
        fp_file = tmp_path / 'ui_cert_fingerprint.txt'
        fp_file.write_text('old-value')
        _write_fingerprint_file(FAKE_FP, tmp_path)
        assert fp_file.read_text() == FAKE_FP

    def test_atomic_write_uses_os_replace(self, mocker, tmp_path):
        mock_replace = mocker.patch(
            'sethlans_worker_agent.tls_setup.os.replace',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.tempfile.mkstemp',
            return_value=(99, str(tmp_path / 'fp_tmp.tmp')),
        )
        mocker.patch('sethlans_worker_agent.tls_setup.os.write')
        mocker.patch('sethlans_worker_agent.tls_setup.os.close')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.platform.system',
            return_value='Windows',
        )
        from sethlans_worker_agent.tls_setup import _write_fingerprint_file
        _write_fingerprint_file(FAKE_FP, tmp_path)
        mock_replace.assert_called_once()


# --- get_ui_cert_fingerprint -----------------------------------------------

class TestGetUiCertFingerprint:

    def test_returns_empty_before_setup(self, mocker):
        import sethlans_worker_agent.tls_setup as mod
        mocker.patch.object(mod, '_ui_cert_fingerprint', '')
        assert mod.get_ui_cert_fingerprint() == ''

    def test_returns_value_after_set(self, mocker):
        import sethlans_worker_agent.tls_setup as mod
        mocker.patch.object(mod, '_ui_cert_fingerprint', FAKE_FP)
        assert mod.get_ui_cert_fingerprint() == FAKE_FP


# --- setup_certificates ----------------------------------------------------

class TestSetupCertificates:

    @pytest.fixture(autouse=True)
    def _reset_fingerprint(self):
        """Reset module-level fingerprint between tests."""
        import sethlans_worker_agent.tls_setup as mod
        original = mod._ui_cert_fingerprint
        yield
        mod._ui_cert_fingerprint = original

    def _patch_common(self, mocker, tmp_path):
        """Patch dependencies shared by setup_certificates tests."""
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_config',
            return_value=(None, None),
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tmp_path,
        )
        mock_cert = _fake_cert()
        mocker.patch(
            'sethlans_worker_agent.tls_setup.load_and_validate_cert',
            return_value=mock_cert,
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.check_cert_expiry_warning',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_cert_fingerprint',
            return_value=FAKE_FP,
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup._write_fingerprint_file',
        )
        return mock_cert

    def test_autogen_when_no_certs_exist(self, mocker, tmp_path):
        self._patch_common(mocker, tmp_path)
        mock_gen = mocker.patch(
            'sethlans_worker_agent.tls_setup.generate_self_signed_cert',
        )
        # cert.pem does not exist -> triggers generation
        from sethlans_worker_agent.tls_setup import setup_certificates
        cert_p, key_p, fp = setup_certificates()
        mock_gen.assert_called_once()
        assert fp == FAKE_FP
        assert cert_p == tmp_path / 'cert.pem'

    def test_loads_existing_without_regenerating(self, mocker, tmp_path):
        self._patch_common(mocker, tmp_path)
        # Create cert.pem so it "exists"
        (tmp_path / 'cert.pem').write_text('existing')
        mock_gen = mocker.patch(
            'sethlans_worker_agent.tls_setup.generate_self_signed_cert',
        )
        from sethlans_worker_agent.tls_setup import setup_certificates
        cert_p, key_p, fp = setup_certificates()
        mock_gen.assert_not_called()
        assert fp == FAKE_FP

    def test_byo_cert_uses_configured_paths(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_config',
            return_value=('/byo/cert.pem', '/byo/key.pem'),
        )
        mock_cert = _fake_cert()
        mocker.patch(
            'sethlans_worker_agent.tls_setup.load_and_validate_cert',
            return_value=mock_cert,
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.check_cert_expiry_warning',
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_cert_fingerprint',
            return_value=FAKE_FP,
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup._write_fingerprint_file',
        )
        from sethlans_worker_agent.tls_setup import setup_certificates
        cert_p, key_p, fp = setup_certificates()
        assert cert_p == Path('/byo/cert.pem')
        assert key_p == Path('/byo/key.pem')
        assert fp == FAKE_FP

    def test_sets_module_fingerprint(self, mocker, tmp_path):
        self._patch_common(mocker, tmp_path)
        (tmp_path / 'cert.pem').write_text('existing')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.generate_self_signed_cert',
        )
        from sethlans_worker_agent import tls_setup
        tls_setup.setup_certificates()
        assert tls_setup.get_ui_cert_fingerprint() == FAKE_FP

    def test_validation_failure_propagates(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_config',
            return_value=(None, None),
        )
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tmp_path,
        )
        (tmp_path / 'cert.pem').write_text('existing')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.load_and_validate_cert',
            side_effect=CertificateError('bad cert'),
        )
        from sethlans_worker_agent.tls_setup import setup_certificates
        with pytest.raises(CertificateError, match='bad cert'):
            setup_certificates()
