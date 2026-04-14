# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``verify_blender_runs()`` in setup service."""

import subprocess
from unittest.mock import MagicMock

import pytest

from workers.services.setup import verify_blender_runs


class TestVerifyBlenderRuns:

    def test_returns_version_string(self, mocker, tmp_path):
        fake_bin = tmp_path / 'blender'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            return_value=MagicMock(
                returncode=0,
                stdout='Blender 4.3.0\n  build date: 2024-11-19',
                stderr='',
            ),
        )
        result = verify_blender_runs(fake_bin)
        assert result == 'Blender 4.3.0'

    def test_raises_on_missing_binary(self, tmp_path):
        missing = tmp_path / 'no_blender'
        with pytest.raises(RuntimeError, match="not found"):
            verify_blender_runs(missing)

    def test_raises_on_nonzero_exit(self, mocker, tmp_path):
        fake_bin = tmp_path / 'blender'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            return_value=MagicMock(
                returncode=1, stdout='', stderr='error msg',
            ),
        )
        with pytest.raises(RuntimeError, match="exited with code"):
            verify_blender_runs(fake_bin)

    def test_raises_on_timeout(self, mocker, tmp_path):
        fake_bin = tmp_path / 'blender'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            side_effect=subprocess.TimeoutExpired('blender', 30),
        )
        with pytest.raises(RuntimeError, match="timed out"):
            verify_blender_runs(fake_bin)

    def test_raises_on_os_error(self, mocker, tmp_path):
        fake_bin = tmp_path / 'blender'
        fake_bin.write_text('binary')
        mocker.patch(
            'workers.services.setup.subprocess.run',
            side_effect=OSError("Permission denied"),
        )
        with pytest.raises(RuntimeError, match="Failed to execute"):
            verify_blender_runs(fake_bin)

    def test_resolves_path(self, mocker, tmp_path):
        fake_bin = tmp_path / 'blender'
        fake_bin.write_text('binary')
        mock_run = mocker.patch(
            'workers.services.setup.subprocess.run',
            return_value=MagicMock(
                returncode=0,
                stdout='Blender 4.3.0\n',
                stderr='',
            ),
        )
        verify_blender_runs(fake_bin)
        called_path = mock_run.call_args[0][0][0]
        assert called_path == str(fake_bin.resolve())
