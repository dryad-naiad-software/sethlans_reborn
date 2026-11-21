# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
U-8 with a real ffmpeg (Unreal sequence spec D13, FR-SV-3, FR-SV-4,
FR-SV-6): skipped when ``ffmpeg`` is not on the PATH (the per-packet
duration check also needs ``ffprobe``).

For each of the four video presets at 24, 24000/1001, 30000/1001 and
60000/1001 fps, three shot videos (7, 11 and 5 frames, from made-up PNGs)
are made with the shot command shape and joined by the concat demuxer with
stream copy.  The whole video passes the manager's own check
(``video_probe``: frame count, and for MP4 and MOV one packet duration and
the exact length), and ffprobe agrees that every MP4 and MOV frame lasts
the same number of ticks (a constant frame rate).  A path with a single
quote goes through the concat list.  The merged copy has one audio stream.
"""

import os
import shutil
import subprocess
import wave

import pytest
from PIL import Image

from workers.constants_video import VIDEO_PRESETS
from workers.video_ffmpeg import (
    build_concat_command, build_mux_command, build_probe_command, build_sequence_video_command,
    concat_list_text, track_timescale,
)
from workers.video_probe import FrameProbe, problem

FFMPEG = shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on the PATH")
SHOT_FRAMES = (7, 11, 5)
RATES = [(24, 1), (24000, 1001), (30000, 1001), (60000, 1001)]


@pytest.fixture(autouse=True)
def _ffmpeg_path(monkeypatch):
    monkeypatch.setattr('workers.video_ffmpeg.resolve_ffmpeg_executable', lambda: FFMPEG)


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, shell=False)
    assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout


def _probe(path):
    probe = FrameProbe()
    for line in _run(build_probe_command(path)).splitlines():
        probe.line(line)
    return probe


def _shot(folder, number, frames, settings, rate):
    """Frames ``100 ..`` named like the stored ones (``<frame, 7 digits>.png``), then the shot video."""
    os.makedirs(folder)
    for frame in range(frames):
        Image.new('RGB', (64, 36), (frame * 23 % 256, number * 70, 40)).save(
            os.path.join(folder, f'{100 + frame:07d}.png'))
    out = os.path.join(folder, f"video.{settings['container']}")
    _run(build_sequence_video_command(os.path.join(folder, '%07d.png'), out, settings, rate, 100, frames))
    return out


def _wav(path, seconds):
    with wave.open(path, 'wb') as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(48_000)
        writer.writeframes(b'\x10\x00\xf0\xff' * int(48_000 * seconds))
    return path


def _ffprobe_durations(path):
    out = _run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'packet=duration',
                '-of', 'csv=p=0', path])
    return [int(value) for value in out.split()]


@pytest.mark.parametrize('rate', RATES, ids=lambda r: f'{r[0]}-{r[1]}')
@pytest.mark.parametrize('preset', sorted(VIDEO_PRESETS))
def test_concat_keeps_every_frame_at_a_constant_rate(tmp_path, preset, rate):
    settings = VIDEO_PRESETS[preset]
    container = settings['container']
    root = tmp_path / "it's here"
    shots = [_shot(str(root / f'{i:04d}'), i, n, settings, rate) for i, n in enumerate(SHOT_FRAMES, 1)]
    assert [_probe(path).count for path in shots] == list(SHOT_FRAMES)
    listing = tmp_path / 'concat.txt'
    listing.write_text(concat_list_text(shots), encoding='utf-8', newline='\n')
    whole = str(tmp_path / f'whole.{container}')
    _run(build_concat_command(str(listing), whole, container, rate))
    assert problem(_probe(whole), sum(SHOT_FRAMES), container, rate) is None
    if container != 'webm' and FFPROBE:
        ticks = track_timescale(rate) * rate[1] // rate[0]
        assert set(_ffprobe_durations(whole)) == {ticks}
    merged = str(tmp_path / f'merged.{container}')
    seconds = sum(SHOT_FRAMES) * rate[1] / rate[0]
    _run(build_mux_command(whole, _wav(str(tmp_path / 'sound.wav'), seconds), merged, container, rate))
    _run([FFMPEG, '-v', 'error', '-nostdin', '-i', merged, '-map', '0:a:0', '-c', 'copy', '-f', 'null', '-'])
    assert problem(_probe(merged), sum(SHOT_FRAMES), container, rate) is None
