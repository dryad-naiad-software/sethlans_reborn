# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`workers.video_probe` (Unreal sequence spec D13, U-8).

Covers: the ``framecrc`` lines are read (timebase, packets, other streams
and comments ignored); a joined MP4 or MOV passes only with the shots'
frame count, one packet duration and the exact length; uneven durations,
a short length or a wrong count fail with the manager's text; a WebM
passes within 1 ms of the expected last frame start and fails beyond it.
"""

from workers.video_probe import FrameProbe, problem


def _probe(timebase, durations, pts=None, header=True):
    probe = FrameProbe()
    if header:
        probe.line(f'#tb 0: {timebase}\n')
    probe.line('#software: Lavf\n')
    start = 0
    for i, duration in enumerate(durations):
        stamp = pts[i] if pts else start
        probe.line(f'0, {stamp}, {stamp}, {duration}, 1200, 0x1234abcd\n')
        start += duration
    probe.line('1, 0, 0, 1024, 10, 0x0\n')
    return probe


class TestTimescaleContainers:

    def test_exact_video_passes(self):
        probe = _probe('1/96000', [4004] * 23)
        assert probe.count == 23
        assert problem(probe, 23, 'mp4', (24000, 1001)) is None

    def test_uneven_durations_fail(self):
        durations = [3757, 3758] * 11 + [3757]
        assert problem(_probe('1/90000', durations), 23, 'mov', (24000, 1001)) == (
            "The joined video's frames do not all last the same time.")

    def test_wrong_length_fails(self):
        message = problem(_probe('1/90000', [3003] * 23), 23, 'mp4', (24, 1))
        assert message.startswith("The joined video lasts 0.767 s; its shots last 0.958 s.")

    def test_wrong_count_fails(self):
        assert problem(_probe('1/90000', [3750] * 22), 23, 'mp4', (24, 1)) == (
            "The joined video has 22 frames; its shots have 23.")

    def test_no_listing_fails(self):
        assert problem(_probe('1/90000', [], header=False), 0, 'mp4', (24, 1)) == (
            "FFmpeg did not list the joined video's frames.")


class TestWebm:

    def test_rounded_times_pass(self):
        pts = [round(i * 1001 / 30) for i in range(23)]
        assert problem(_probe('1/1000', [33] * 23, pts), 23, 'webm', (30000, 1001)) is None

    def test_a_late_last_frame_fails(self):
        pts = [round(i * 1001 / 30) for i in range(22)] + [800]
        assert problem(_probe('1/1000', [33] * 23, pts), 23, 'webm', (30000, 1001)).startswith(
            "The joined video's last frame starts at 0.800 s")
