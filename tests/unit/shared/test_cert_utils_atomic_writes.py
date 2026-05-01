# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Atomic-write tests for ``shared/cert_utils.py``.

Spec 2 introduced a short-lived ``apply_pending_setup`` subprocess
that calls ``setup_certificates`` via
``prime_runtime_state_for_auto_enroll``. A SIGKILL between the key
write and the cert write previously left a partial pair on disk that
the next apply attempt could not recover from. The atomic helper
writes via tempfile + fsync + ``os.replace`` so a crash mid-write
leaves the target either fully present or fully absent. These tests
cover the helper itself and the wiring that makes
``generate_self_signed_cert`` route both files through it.
"""
import os
import stat

import pytest

import shared.cert_utils as cert_utils
from shared.cert_utils import (
    _atomic_write_bytes,
    generate_self_signed_cert,
)


class TestAtomicWriteBytes:
    """Atomic-write helper used by ``generate_self_signed_cert``."""

    def test_writes_bytes_to_target(self, tmp_path):
        target = tmp_path / "out.bin"
        _atomic_write_bytes(target, b"hello world")
        assert target.read_bytes() == b"hello world"

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "out.bin"
        target.write_bytes(b"old contents")
        _atomic_write_bytes(target, b"new contents")
        assert target.read_bytes() == b"new contents"

    def test_uses_os_replace_for_atomicity(self, tmp_path, mocker):
        """The rename MUST go through ``os.replace`` (atomic on
        POSIX/NTFS). If a future refactor regresses to direct
        ``write_bytes``, this test catches it."""
        mock_replace = mocker.patch(
            'shared.cert_utils.os.replace',
            wraps=os.replace,
        )
        target = tmp_path / "out.bin"
        _atomic_write_bytes(target, b"data")
        assert mock_replace.call_count == 1
        # Second positional arg is the final destination.
        _src, dst = mock_replace.call_args.args
        assert dst == str(target)

    def test_posix_chmod_on_tempfile_before_rename(self, mocker, tmp_path):
        """When a posix_mode is set, chmod hits the TMP path (not the
        final path) so the final file is born with the restrictive
        mode — no world-readable window between rename and chmod."""
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Linux',
        )
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        target = tmp_path / "key.pem"
        _atomic_write_bytes(target, b"PEM", posix_mode=0o600)
        mock_chmod.assert_called_once()
        args, _ = mock_chmod.call_args
        chmod_path, chmod_mode = args
        assert chmod_mode == 0o600
        assert chmod_path != str(target)

    def test_no_chmod_on_windows(self, mocker, tmp_path):
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Windows',
        )
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        target = tmp_path / "key.pem"
        _atomic_write_bytes(target, b"PEM", posix_mode=0o600)
        mock_chmod.assert_not_called()

    def test_no_chmod_when_mode_is_none(self, mocker, tmp_path):
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Linux',
        )
        mock_chmod = mocker.patch('shared.cert_utils.os.chmod')
        target = tmp_path / "cert.pem"
        _atomic_write_bytes(target, b"PEM")
        mock_chmod.assert_not_called()

    def test_crash_mid_write_leaves_target_untouched(
        self, mocker, tmp_path,
    ):
        """If ``os.replace`` fails (simulating a crash after the temp
        file is written but before the rename completes), the target
        file MUST NOT exist as a partial copy."""
        mocker.patch(
            'shared.cert_utils.os.replace',
            side_effect=OSError("simulated crash"),
        )
        target = tmp_path / "out.bin"
        with pytest.raises(OSError, match="simulated crash"):
            _atomic_write_bytes(target, b"data")
        assert not target.exists()
        # Tempfile cleanup happens in the except branch — no leftover
        # files in the parent directory.
        leftovers = [
            p for p in tmp_path.iterdir() if p.name != target.name
        ]
        assert leftovers == []

    def test_crash_does_not_clobber_existing_target(
        self, mocker, tmp_path,
    ):
        target = tmp_path / "out.bin"
        target.write_bytes(b"original")
        mocker.patch(
            'shared.cert_utils.os.replace',
            side_effect=OSError("simulated crash"),
        )
        with pytest.raises(OSError):
            _atomic_write_bytes(target, b"new bytes")
        # Original contents preserved — the rename never landed.
        assert target.read_bytes() == b"original"


class TestGenerateSelfSignedCertAtomicity:
    """Spec 2 concurrency MED — verify ``generate_self_signed_cert``
    routes both writes through the atomic helper."""

    @pytest.fixture(autouse=True)
    def _mock_env(self, mocker):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value={'10.0.0.1'},
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='testhost',
        )
        mocker.patch(
            'shared.cert_utils.platform.system',
            return_value='Linux',
        )

    def test_both_files_use_atomic_helper(self, mocker, tmp_path):
        spy = mocker.patch(
            'shared.cert_utils._atomic_write_bytes',
            wraps=cert_utils._atomic_write_bytes,
        )
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        # Two atomic writes: one per PEM file.
        assert spy.call_count == 2
        # Key first, cert second — preserves "if cert write fails,
        # cert.pem.exists() returns False and the next run regenerates"
        # recovery semantics.
        first_path = spy.call_args_list[0].args[0]
        second_path = spy.call_args_list[1].args[0]
        assert first_path == k
        assert second_path == c
        # Key is written with 0o600; cert has no posix mode.
        assert spy.call_args_list[0].kwargs.get('posix_mode') == 0o600
        assert spy.call_args_list[1].kwargs.get('posix_mode') is None

    def test_both_files_present_after_success(self, tmp_path):
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        assert c.exists()
        assert k.exists()
        assert c.stat().st_size > 0
        assert k.stat().st_size > 0

    @pytest.mark.skipif(
        os.name == 'nt',
        reason='POSIX-only stat.st_mode bits',
    )
    def test_key_born_with_0o600_on_posix(self, mocker, tmp_path):
        # Use the real platform.system (skip the autouse Linux mock for
        # this test by re-patching).
        mocker.patch(
            'shared.cert_utils.platform.system', return_value='Linux',
        )
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'
        generate_self_signed_cert(c, k)
        mode = stat.S_IMODE(k.stat().st_mode)
        assert mode == 0o600

    def test_no_partial_cert_when_cert_write_fails(
        self, mocker, tmp_path,
    ):
        """If the cert write fails after the key landed, ``cert.pem``
        MUST NOT exist as a partial / empty file. The next apply run
        sees the missing cert and regenerates a fresh keypair."""
        c = tmp_path / 'tls' / 'cert.pem'
        k = tmp_path / 'tls' / 'key.pem'

        original = cert_utils._atomic_write_bytes
        call_count = {'n': 0}

        def fake_atomic(path, data, posix_mode=None):
            call_count['n'] += 1
            if call_count['n'] == 1:
                # First call: write the key normally.
                original(path, data, posix_mode=posix_mode)
            else:
                # Second call: simulate a crash mid-cert-write.
                raise OSError("simulated cert-write crash")

        mocker.patch(
            'shared.cert_utils._atomic_write_bytes',
            side_effect=fake_atomic,
        )
        with pytest.raises(OSError, match="simulated cert-write crash"):
            generate_self_signed_cert(c, k)
        # Key landed but cert did NOT — clean recovery state.
        assert k.exists()
        assert not c.exists()
