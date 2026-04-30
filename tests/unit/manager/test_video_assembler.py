# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for video assembly command building and validation.
"""

import os
import tempfile

import pytest

from workers.video_assembler import (
    build_ffmpeg_command,
    _validate_path_in_media_root,
    _prepare_temp_frames,
)


class TestBuildFfmpegCommand:
    """Tests for build_ffmpeg_command()."""

    def test_h264_command(self):
        cmd = build_ffmpeg_command(
            '/tmp/frame_%04d.png', '/tmp/out.mp4',
            {'codec': 'libx264', 'container': 'mp4', 'framerate': 24, 'crf': 23},
        )
        # Spec wizard-ffmpeg-rewrite: first argv element is the
        # parts-check resolved path (or 'ffmpeg' fallback when no
        # resolved path is published yet).  The rest of the command
        # is asserted exactly.
        assert cmd[1:] == [
            '-y',
            '-framerate', '24',
            '-i', '/tmp/frame_%04d.png',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-crf', '23',
            '/tmp/out.mp4',
        ]

    def test_h265_command(self):
        cmd = build_ffmpeg_command(
            '/tmp/frame_%04d.png', '/tmp/out.mp4',
            {'codec': 'libx265', 'container': 'mp4', 'framerate': 30, 'crf': 28},
        )
        assert '-c:v' in cmd
        assert cmd[cmd.index('-c:v') + 1] == 'libx265'
        assert '-crf' in cmd
        assert cmd[cmd.index('-crf') + 1] == '28'

    def test_vp9_command_has_bitrate_zero(self):
        cmd = build_ffmpeg_command(
            '/tmp/frame_%04d.png', '/tmp/out.webm',
            {'codec': 'libvpx-vp9', 'container': 'webm', 'framerate': 24, 'crf': 31},
        )
        assert '-b:v' in cmd
        assert cmd[cmd.index('-b:v') + 1] == '0'
        assert '-crf' in cmd

    def test_prores_command_no_crf(self):
        cmd = build_ffmpeg_command(
            '/tmp/frame_%04d.png', '/tmp/out.mov',
            {'codec': 'prores_ks', 'container': 'mov', 'framerate': 24, 'crf': 0},
        )
        assert '-crf' not in cmd
        assert '-profile:v' in cmd
        assert cmd[cmd.index('-profile:v') + 1] == '3'
        assert '-pix_fmt' in cmd
        assert cmd[cmd.index('-pix_fmt') + 1] == 'yuva444p10le'

    def test_rejects_invalid_codec(self):
        with pytest.raises(ValueError, match="Invalid codec"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.mp4',
                {'codec': 'libfake', 'container': 'mp4', 'framerate': 24, 'crf': 23},
            )

    def test_rejects_invalid_container(self):
        with pytest.raises(ValueError, match="Invalid container"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.avi',
                {'codec': 'libx264', 'container': 'avi', 'framerate': 24, 'crf': 23},
            )

    def test_rejects_framerate_zero(self):
        with pytest.raises(ValueError, match="Invalid framerate"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.mp4',
                {'codec': 'libx264', 'container': 'mp4', 'framerate': 0, 'crf': 23},
            )

    def test_rejects_framerate_too_high(self):
        with pytest.raises(ValueError, match="Invalid framerate"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.mp4',
                {'codec': 'libx264', 'container': 'mp4', 'framerate': 121, 'crf': 23},
            )

    def test_rejects_framerate_non_int(self):
        with pytest.raises(ValueError, match="Invalid framerate"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.mp4',
                {'codec': 'libx264', 'container': 'mp4', 'framerate': 'fast', 'crf': 23},
            )

    def test_rejects_crf_too_high(self):
        with pytest.raises(ValueError, match="Invalid crf"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.mp4',
                {'codec': 'libx264', 'container': 'mp4', 'framerate': 24, 'crf': 52},
            )

    def test_rejects_crf_negative(self):
        with pytest.raises(ValueError, match="Invalid crf"):
            build_ffmpeg_command(
                '/tmp/f.png', '/tmp/o.mp4',
                {'codec': 'libx264', 'container': 'mp4', 'framerate': 24, 'crf': -1},
            )

    def test_shell_false_explicit(self):
        """Verify the spec requirement that shell=False is explicit."""
        # The build_ffmpeg_command doesn't call subprocess, but verify
        # the returned list is suitable for shell=False
        cmd = build_ffmpeg_command(
            '/tmp/f.png', '/tmp/o.mp4',
            {'codec': 'libx264', 'container': 'mp4', 'framerate': 24, 'crf': 23},
        )
        assert isinstance(cmd, list)
        assert all(isinstance(arg, str) for arg in cmd)


class TestValidatePathInMediaRoot:
    """Tests for MEDIA_ROOT containment check."""

    def test_path_inside_media_root(self, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        test_file = tmp_path / "test.png"
        test_file.touch()
        assert _validate_path_in_media_root(str(test_file)) is True

    def test_path_outside_media_root(self, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path / "media")
        (tmp_path / "media").mkdir()
        outside = tmp_path / "outside.png"
        outside.touch()
        assert _validate_path_in_media_root(str(outside)) is False


class TestPrepareFrames:
    """Tests for frame file preparation/normalization."""

    def test_creates_sequential_filenames(self):
        with tempfile.TemporaryDirectory() as src_dir:
            # Create source files with non-contiguous numbering
            src1 = os.path.join(src_dir, "original_001.png")
            src2 = os.path.join(src_dir, "original_003.png")
            src3 = os.path.join(src_dir, "original_005.png")
            for f in [src1, src2, src3]:
                with open(f, 'w') as fp:
                    fp.write("test")

            with tempfile.TemporaryDirectory() as temp_dir:
                frame_files = [
                    (1, src1),
                    (2, src2),
                    (3, src3),
                ]
                pattern = _prepare_temp_frames(frame_files, temp_dir, '.png')

                assert 'frame_%04d.png' in pattern
                assert os.path.exists(os.path.join(temp_dir, 'frame_0001.png'))
                assert os.path.exists(os.path.join(temp_dir, 'frame_0002.png'))
                assert os.path.exists(os.path.join(temp_dir, 'frame_0003.png'))
