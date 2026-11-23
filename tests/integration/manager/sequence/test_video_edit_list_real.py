# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression for #291 with a real ffmpeg: a whole sequence video failed at
random with "The joined video's frames do not all last the same time."

Shot videos are B-frame MP4s, so the muxer writes an edit list.  Its length
was written in the movie timescale, which was left at the muxer's default
of 1000 (milliseconds); a 212-frame shot at 24 fps (8833.33 ms) could read
30 ticks short at 90 kHz and the concat demuxer then overlapped the next
shot by that much (one 3720-tick frame among 3750-tick frames).  The fix
writes ``-movie_timescale`` equal to the track timescale for MP4 and MOV.

The failure depended on the frames' content; synthetic ``testsrc2`` frames
at 640x360 reproduce it.  Two shots (518.., 212 frames and 730.., 134
frames, 24 fps) are rendered as PNG sequences, encoded with the product's
``build_sequence_video_command`` and joined with ``build_concat_command``.
The manager's own check (``video_probe``) must pass: 346 frames, every
packet 3750 ticks.  ffprobe must read each shot's length as exactly
``frames * 3750`` ticks, and each shot's ``mvhd`` and ``elst`` boxes must
count in the 90 kHz track timescale and hold exactly that length.  A last
test drops ``-movie_timescale`` at run time, encodes only the two shots and
shows their ``mvhd`` timescale is then not the track timescale and their
``mvhd`` and ``elst`` lengths are not exact.

Skipped when ffmpeg is not found the way the manager finds it
(``resolve_ffmpeg_executable``, falling back to ``ffmpeg`` on the PATH);
the ffprobe length checks also need ``ffprobe``.
"""

import os
import shutil
import struct
import subprocess
from fractions import Fraction

import pytest

from workers import video_ffmpeg
from workers.video_ffmpeg import (
    build_concat_command, build_probe_command, build_sequence_video_command, concat_list_text,
    resolve_ffmpeg_executable, track_timescale,
)
from workers.video_probe import FrameProbe, problem

FFMPEG = shutil.which(resolve_ffmpeg_executable())
FFPROBE = shutil.which('ffprobe', path=os.path.dirname(FFMPEG)) if FFMPEG else None
FFPROBE = FFPROBE or shutil.which('ffprobe')
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not available")
needs_ffprobe = pytest.mark.skipif(FFPROBE is None, reason="ffprobe is not available")

RATE = (24, 1)
SETTINGS = {'codec': 'libx264', 'container': 'mp4', 'crf': 23}
SHOTS = (('a', 518, 212), ('b', 730, 134))
TOTAL = sum(count for _, _, count in SHOTS)
TICKS = track_timescale(RATE) * RATE[1] // RATE[0]
_REAL_TIMESCALE_ARGS = video_ffmpeg.timescale_args


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, shell=False)
    assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout


def _render_frames(folder, start, count):
    """``count`` testsrc2 frames, 640x360 at 24 fps, named ``<frame, 7 digits>.png`` from ``start``."""
    os.makedirs(folder)
    _run([FFMPEG, '-v', 'error', '-y', '-nostdin', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24',
          '-frames:v', str(count), '-start_number', str(start), os.path.join(folder, '%07d.png')])
    return os.path.join(folder, '%07d.png')


def _encode_shots(frames, out):
    """Each shot video through the product's ``build_sequence_video_command``; returns their paths."""
    os.makedirs(out)
    shots = []
    for name, start, count in SHOTS:
        path = os.path.join(out, f'{name}.mp4')
        _run(build_sequence_video_command(frames[name], path, SETTINGS, RATE, start, count))
        shots.append(path)
    return shots


def _build(frames, out):
    """The shot videos, then the whole video by ``build_concat_command``; returns their paths."""
    shots = _encode_shots(frames, out)
    listing = os.path.join(out, 'concat.txt')
    with open(listing, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(concat_list_text(shots))
    whole = os.path.join(out, 'whole.mp4')
    _run(build_concat_command(listing, whole, 'mp4', RATE))
    return shots, whole


def _probe(path):
    probe = FrameProbe()
    for line in _run(build_probe_command(path)).splitlines():
        probe.line(line)
    return probe


def _duration_ts(path):
    """The video stream's length in its own ticks, as ffprobe reads the container."""
    out = _run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                'stream=duration_ts,time_base', '-of', 'default=nw=1', path])
    fields = dict(line.split('=', 1) for line in out.split())
    assert fields['time_base'] == f'1/{track_timescale(RATE)}'
    return int(fields['duration_ts'])


def _boxes(data, start, end):
    """``(type, payload start, end)`` of each ISO-BMFF box between ``start`` and ``end``."""
    while start + 8 <= end:
        size, kind = struct.unpack('>I4s', data[start:start + 8])
        header = 8
        if size == 1:
            size, header = struct.unpack('>Q', data[start + 8:start + 16])[0], 16
        elif size == 0:
            size = end - start
        yield kind, start + header, start + size
        start += size


def _child(data, parent, kind):
    return next((box for box in _boxes(data, *parent) if box[0] == kind), None)


def _movie_lengths(path):
    """``(mvhd timescale, mvhd duration, [elst segment durations])`` of a one-track MP4."""
    with open(path, 'rb') as handle:
        data = handle.read()
    moov = _child(data, (0, len(data)), b'moov')[1:]
    _, at, _ = _child(data, moov, b'mvhd')
    if data[at] == 1:
        timescale, duration = struct.unpack('>IQ', data[at + 20:at + 32])
    else:
        timescale, duration = struct.unpack('>II', data[at + 12:at + 20])
    edts = _child(data, _child(data, moov, b'trak')[1:], b'edts')
    edits = []
    if edts is not None:
        _, at, _ = _child(data, edts[1:], b'elst')
        wide = data[at] == 1
        entry, layout = (20, '>Qq') if wide else (12, '>Ii')
        for index in range(struct.unpack('>I', data[at + 4:at + 8])[0]):
            seg, media_time = struct.unpack(layout, data[at + 8 + index * entry:at + 8 + index * entry + entry - 4])
            if media_time != -1:
                edits.append(seg)
    return timescale, duration, edits


def _without_movie_timescale(container, rate):
    """``timescale_args`` as it was before #291: the track timescale only."""
    args = list(_REAL_TIMESCALE_ARGS(container, rate))
    if '-movie_timescale' in args:
        index = args.index('-movie_timescale')
        del args[index:index + 2]
    return args


@pytest.fixture(scope='module')
def frames(tmp_path_factory):
    root = tmp_path_factory.mktemp('frames')
    return {name: _render_frames(str(root / name), start, count) for name, start, count in SHOTS}


@pytest.fixture(scope='module')
def built(frames, tmp_path_factory):
    """The two shot videos and the whole video, built once with the real builders."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(video_ffmpeg, 'resolve_ffmpeg_executable', lambda: FFMPEG)
        return _build(frames, str(tmp_path_factory.mktemp('built') / 'out'))


class TestSequenceVideoEditListIntegration:
    """#291: shot edit lists in the track timescale, so the joined video has constant frames."""

    def test_each_shot_keeps_its_frames(self, built):
        shots, _ = built
        for path, (_, _, count) in zip(shots, SHOTS):
            probe = _probe(path)
            assert probe.count == count
            assert probe.durations == {TICKS}

    @needs_ffprobe
    def test_each_shot_lasts_exactly_its_frames(self, built):
        shots, _ = built
        assert [_duration_ts(path) for path in shots] == [count * TICKS for _, _, count in SHOTS]

    def test_joined_video_passes_the_frame_check(self, built):
        _, whole = built
        probe = _probe(whole)
        assert problem(probe, TOTAL, 'mp4', RATE) is None
        assert probe.count == TOTAL == 346
        assert probe.durations == {TICKS} == {3750}
        assert probe.last - probe.first + TICKS == TOTAL * TICKS

    @needs_ffprobe
    def test_joined_video_lasts_exactly_its_frames(self, built):
        _, whole = built
        assert _duration_ts(whole) == TOTAL * TICKS

    def test_each_shot_writes_its_length_in_the_track_timescale(self, built):
        """The movie header and the edit list count in 90 kHz ticks and hold exactly ``frames * 3750``."""
        shots, _ = built
        for path, (_, _, count) in zip(shots, SHOTS):
            timescale, duration, edits = _movie_lengths(path)
            assert timescale == track_timescale(RATE)
            assert duration == count * TICKS
            assert edits and sum(edits) == count * TICKS

    def test_without_movie_timescale_the_lengths_are_not_exact(self, frames, tmp_path, monkeypatch):
        """Guard: with the fix reverted at run time the movie timescale is not the track's.

        Left to the muxer the movie timescale is 1000, and neither 212 nor
        134 frames at 24 fps is a whole number of milliseconds, so the movie
        and edit list lengths cannot be exact whichever way they round
        (which way decided whether the join failed, #291).
        """
        monkeypatch.setattr(video_ffmpeg, 'resolve_ffmpeg_executable', lambda: FFMPEG)
        monkeypatch.setattr(video_ffmpeg, 'timescale_args', _without_movie_timescale)
        shots = _encode_shots(frames, str(tmp_path / 'out'))
        for path, (_, _, count) in zip(shots, SHOTS):
            timescale, duration, edits = _movie_lengths(path)
            assert timescale != track_timescale(RATE)
            assert Fraction(duration, timescale) != Fraction(count * RATE[1], RATE[0])
            assert Fraction(sum(edits), timescale) != Fraction(count * RATE[1], RATE[0])
