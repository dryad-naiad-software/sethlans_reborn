# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ffmpeg detection utilities.
"""

import subprocess
from unittest.mock import patch, MagicMock

from workers.utils.ffmpeg_utils import ffmpeg_available, ffmpeg_path


class TestFfmpegPath:
    """Tests for ffmpeg_path()."""

    def test_returns_path_when_found(self):
        with patch('workers.utils.ffmpeg_utils.shutil.which', return_value='/usr/bin/ffmpeg'):
            assert ffmpeg_path() == '/usr/bin/ffmpeg'

    def test_returns_none_when_not_found(self):
        with patch('workers.utils.ffmpeg_utils.shutil.which', return_value=None):
            assert ffmpeg_path() is None


class TestFfmpegAvailable:
    """Tests for ffmpeg_available()."""

    def test_returns_true_on_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch('workers.utils.ffmpeg_utils.subprocess.run', return_value=mock_result):
            assert ffmpeg_available() is True

    def test_returns_false_on_nonzero_exit(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch('workers.utils.ffmpeg_utils.subprocess.run', return_value=mock_result):
            assert ffmpeg_available() is False

    def test_returns_false_on_file_not_found(self):
        with patch(
            'workers.utils.ffmpeg_utils.subprocess.run',
            side_effect=FileNotFoundError,
        ):
            assert ffmpeg_available() is False

    def test_returns_false_on_timeout(self):
        with patch(
            'workers.utils.ffmpeg_utils.subprocess.run',
            side_effect=subprocess.TimeoutExpired('ffmpeg', 5),
        ):
            assert ffmpeg_available() is False

    def test_returns_false_on_os_error(self):
        with patch(
            'workers.utils.ffmpeg_utils.subprocess.run',
            side_effect=OSError("permission denied"),
        ):
            assert ffmpeg_available() is False

    def test_runs_with_correct_args(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch('workers.utils.ffmpeg_utils.subprocess.run', return_value=mock_result) as mock_run:
            ffmpeg_available()
            mock_run.assert_called_once_with(
                ['ffmpeg', '-version'],
                capture_output=True,
                timeout=5,
                shell=False,
            )
