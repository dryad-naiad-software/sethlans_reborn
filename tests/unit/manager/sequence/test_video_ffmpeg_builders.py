# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the sequence ffmpeg builders in :mod:`workers.video_ffmpeg`
(Unreal sequence spec FR-SV-3, FR-SV-4, FR-SV-6, D13, D19, AC-19, AC-40).

Covers: integer and ``(num, den)`` rates and their bounds; the shot-video
command (``-framerate num/den``, start number, frame count, the fixed
track timescale 90000 x den for MP4 and MOV, none for WebM); the concat
list quotes ``'`` as ``'\\''`` and refuses relative or multi-line paths;
every concat command with ``-safe 0`` also has ``-protocol_whitelist file``
and copies the streams; the mux maps one video and one audio stream with
the codec of each container; the frame count parser; every command is an
argument list (no shell) and allowlists still apply.
"""

import os
import sys

import pytest

from workers.constants_video import VIDEO_AUDIO_CODECS
from workers.video_ffmpeg import (
    build_concat_command, build_ffmpeg_command, build_mux_command, build_probe_command,
    build_sequence_video_command, concat_list_text, rate_text, run_ffmpeg_output, timescale_args,
    track_timescale,
)

H264 = {'container': 'mp4', 'codec': 'libx264', 'crf': 23}
VP9 = {'container': 'webm', 'codec': 'libvpx-vp9', 'crf': 31}
PRORES = {'container': 'mov', 'codec': 'prores_ks', 'crf': 0}
ABS = os.path.abspath('media')


@pytest.fixture(autouse=True)
def _ffmpeg_path(monkeypatch):
    monkeypatch.setattr('workers.video_ffmpeg.resolve_ffmpeg_executable', lambda: 'ffmpeg')


def _after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


class TestRates:

    @pytest.mark.parametrize('rate, text', [(24, '24'), (120, '120'), ((24, 1), '24/1'), ((30000, 1001), '30000/1001'),
                                            ([60000, 1001], '60000/1001')], ids=str)
    def test_valid(self, rate, text):
        assert rate_text(rate) == text

    @pytest.mark.parametrize('rate', [0, 121, True, 24.0, (1, 2), (121, 1), (30000, 1002), (24,), (24, 0), '24',
                                      None, (24.0, 1)], ids=repr)
    def test_invalid(self, rate):
        with pytest.raises(ValueError):
            rate_text(rate)

    def test_blender_command_takes_either_rate(self):
        assert _after(build_ffmpeg_command('in_%04d.png', 'out.mp4', dict(H264, framerate=24)), '-framerate') == '24'
        cmd = build_ffmpeg_command('in_%04d.png', 'out.mp4', dict(H264, framerate=(30000, 1001)))
        assert _after(cmd, '-framerate') == '30000/1001'


class TestShotVideo:

    def test_mp4_at_a_1001_rate(self):
        cmd = build_sequence_video_command('f/%07d.png', 'out.mp4', H264, (30000, 1001), start_number=120, frames=48)
        assert cmd[0] == 'ffmpeg' and isinstance(cmd, list)
        assert _after(cmd, '-framerate') == '30000/1001'
        assert _after(cmd, '-start_number') == '120'
        assert _after(cmd, '-frames:v') == '48'
        assert _after(cmd, '-video_track_timescale') == '90000'
        assert _after(cmd, '-movie_timescale') == '90000'
        assert cmd.index('-start_number') < cmd.index('-i') < cmd.index('-c:v')
        assert cmd[-1] == 'out.mp4'

    def test_mov_and_webm_timescales(self):
        mov = build_sequence_video_command('p', 'o.mov', PRORES, (24, 1))
        assert _after(mov, '-video_track_timescale') == _after(mov, '-movie_timescale') == '90000'
        webm = build_sequence_video_command('p', 'o.webm', VP9, (30000, 1001))
        assert '-video_track_timescale' not in webm and '-movie_timescale' not in webm
        assert timescale_args('mp4', 25) == ['-video_track_timescale', '90000', '-movie_timescale', '90000']
        assert timescale_args('webm', 25) == []

    @pytest.mark.parametrize('container, rate, ticks', [('mp4', (24000, 1001), '96000'), ('mov', (60000, 1001), '120000'),
                                                        ('mp4', (7, 1), '90006')], ids=str)
    def test_movie_timescale_matches_the_track_timescale(self, container, rate, ticks):
        """#291: the edit list is written in the movie timescale, so it must be as exact as the track's."""
        assert timescale_args(container, rate) == ['-video_track_timescale', ticks, '-movie_timescale', ticks]

    @pytest.mark.parametrize('rate, timescale, ticks', [
        ((24, 1), 90000, 3750), ((25, 1), 90000, 3600), ((30, 1), 90000, 3000), ((24000, 1001), 96000, 4004),
        ((30000, 1001), 90000, 3003), ((60000, 1001), 120000, 2002), ((120, 1), 90000, 750), (24, 90000, 3750),
        ((7, 1), 90006, 12858)], ids=str)
    def test_every_frame_lasts_a_whole_number_of_ticks(self, rate, timescale, ticks):
        """Review: the timescale is the smallest multiple of num of at least 90000 (a constant frame rate)."""
        num, den = rate if isinstance(rate, tuple) else (rate, 1)
        assert track_timescale(rate) == timescale and timescale % num == 0 and timescale >= 90000
        assert timescale * den // num == ticks and timescale * den % num == 0

    @pytest.mark.parametrize('settings, tagged', [(dict(H264, codec='libx265', crf=28), True), (H264, False),
                                                  (PRORES, False)], ids=['h265', 'h264', 'prores'])
    def test_h265_in_mp4_is_tagged_hvc1(self, settings, tagged):
        cmd = build_sequence_video_command('p', 'o.' + settings['container'], settings, (24, 1))
        assert (cmd[cmd.index('-tag:v') + 1] == 'hvc1') if tagged else ('-tag:v' not in cmd)
        blender = build_ffmpeg_command('p', 'o.mp4', dict(settings, framerate=24))
        assert ('-tag:v' in blender) == tagged

    def test_allowlists_still_apply(self):
        with pytest.raises(ValueError):
            build_sequence_video_command('p', 'o.mp4', dict(H264, codec='libx264; rm'), (24, 1))
        with pytest.raises(ValueError):
            build_sequence_video_command('p', 'o.mp4', H264, (24, 0))


class TestConcat:

    def test_list_quotes_single_quotes(self):
        path = os.path.join(ABS, "it's", 'video.mp4')
        assert concat_list_text([path]) == "file '" + path.replace("'", "'\\''") + "'\n"
        assert "it'\\''s" in concat_list_text([path])

    def test_list_keeps_edit_order(self):
        paths = [os.path.join(ABS, f'{i:04d}', 'video.mp4') for i in (3, 1, 2)]
        assert [line[6:-1] for line in concat_list_text(paths).splitlines()] == paths

    @pytest.mark.parametrize('path', ['relative/video.mp4', os.path.join(ABS, 'a\nfile x.mp4'),
                                      os.path.join(ABS, 'a\rb.mp4'), os.path.join(ABS, 'a\0b.mp4')], ids=repr)
    def test_list_refuses_unsafe_paths(self, path):
        with pytest.raises(ValueError):
            concat_list_text([path])

    @pytest.mark.parametrize('container, rate', [('mp4', (24, 1)), ('mov', (30000, 1001)), ('webm', (30, 1))])
    def test_safe_zero_always_has_the_whitelist(self, container, rate):
        cmd = build_concat_command('list.txt', f'out.{container}', container, rate)
        assert _after(cmd, '-f') == 'concat' and _after(cmd, '-safe') == '0'
        assert _after(cmd, '-protocol_whitelist') == 'file'
        assert cmd.index('-protocol_whitelist') < cmd.index('-i')
        assert _after(cmd, '-c') == 'copy' and '-c:v' not in cmd
        assert ('-video_track_timescale' in cmd) == ('-movie_timescale' in cmd) == (container != 'webm')

    def test_unknown_container_is_refused(self):
        with pytest.raises(ValueError):
            build_concat_command('list.txt', 'out.avi', 'avi', (24, 1))


class TestMux:

    @pytest.mark.parametrize('container, codec', [('mp4', ['aac', '-b:a', '192k']), ('webm', ['libopus', '-b:a', '192k']),
                                                  ('mov', ['pcm_s16le'])])
    def test_one_video_one_audio_stream(self, container, codec):
        cmd = build_mux_command('v.' + container, 's.wav', 'out.' + container, container, (24, 1))
        assert cmd.count('-i') == 2 and cmd[cmd.index('-i') + 1] == 'v.' + container
        assert [cmd[i + 1] for i, arg in enumerate(cmd) if arg == '-map'] == ['0:v:0', '1:a:0']
        assert _after(cmd, '-c:v') == 'copy'
        start = cmd.index('-c:a') + 1
        assert cmd[start:start + len(codec)] == codec == list(VIDEO_AUDIO_CODECS[container])
        assert ('-movie_timescale' in cmd) == (container != 'webm')

    def test_unknown_container_is_refused(self):
        with pytest.raises(ValueError):
            build_mux_command('v', 's.wav', 'o', 'mkv', (24, 1))


class TestProbeAndRunner:

    def test_probe_lists_packets_without_progress_stats(self):
        cmd = build_probe_command('whole.mp4')
        assert cmd[-3:] == ['-f', 'framecrc', '-'] and _after(cmd, '-c') == 'copy' and _after(cmd, '-map') == '0:v:0'
        assert '-stats' not in cmd

    def test_only_the_end_of_stderr_is_kept(self):
        script = "import sys; sys.stderr.write('x' * 300000 + 'END'); sys.exit(3)"
        code, stderr = run_ffmpeg_output([sys.executable, '-c', script], 'test')
        assert code == 3 and stderr.endswith('END') and len(stderr) == 64 * 1024

    def test_stdout_lines_are_streamed(self):
        lines = []
        script = "print('#tb 0: 1/90000'); print('0, 0, 0, 3750, 10, 0x1')"
        code, _ = run_ffmpeg_output([sys.executable, '-c', script], 'test', on_line=lines.append)
        assert code == 0 and [line.strip() for line in lines] == ['#tb 0: 1/90000', '0, 0, 0, 3750, 10, 0x1']

    @pytest.mark.parametrize('stream', [False, True], ids=['plain', 'streamed'])
    def test_timeout(self, stream):
        cmd = [sys.executable, '-c', 'import time; time.sleep(30)']
        code, message = run_ffmpeg_output(cmd, 'test', timeout=0.5, on_line=(lambda _l: None) if stream else None)
        assert code is None and message.startswith('FFmpeg timed out after')
