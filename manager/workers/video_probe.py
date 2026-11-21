# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Check a joined sequence video's frames (Unreal sequence rendering D13,
U-8) from ffmpeg's ``framecrc`` packet list
(``video_ffmpeg.build_probe_command``), read line by line so a long video
is never held in memory.

:class:`FrameProbe` collects the packet count, the timebase, the set of
packet durations and the first and last timestamps.  :func:`problem`
compares them with what the shots add up to:

* every container: the frame count equals the shots' sum;
* MP4 and MOV: every packet lasts the same number of ticks (a constant
  frame rate) and the video spans exactly ``frames * den / num`` seconds;
* WebM (1 ms timebase, durations rounded): the last frame starts within a
  quarter of a frame of ``(frames - 1) * den / num`` seconds.  Each joined
  shot can shift the next by up to a millisecond of rounding, so a long
  join drifts; past a quarter frame (8 ms at 30 fps, well under what an eye
  sees against the sound) the WebM is encoded from the frames instead (D13).
"""

from fractions import Fraction

from .constants_video import TIMESCALE_CONTAINERS

WEBM_SLACK_FRAMES = Fraction(1, 4)


class FrameProbe:
    """Feed :meth:`line` every stdout line of the ``framecrc`` run."""

    def __init__(self):
        self.count = 0
        self.timebase = None
        self.durations = set()
        self.first = None
        self.last = None

    def line(self, text):
        text = text.strip()
        if text.startswith('#tb 0:'):
            num, _, den = text.split(':', 1)[1].strip().partition('/')
            self.timebase = Fraction(int(num), int(den))
        if not text or text.startswith('#'):
            return
        fields = [field.strip() for field in text.split(',')]
        if len(fields) < 5 or fields[0] != '0':
            return
        pts, duration = int(fields[2]), int(fields[3])
        self.count += 1
        if len(self.durations) < 8:
            self.durations.add(duration)
        self.first = pts if self.first is None else min(self.first, pts)
        self.last = pts if self.last is None else max(self.last, pts)

    def span(self):
        """Seconds from the first frame's start to the last frame's end (MP4, MOV)."""
        return (self.last - self.first + next(iter(self.durations))) * self.timebase


def problem(probe, expected, container, rate):
    """``None`` when the joined video is right, else the manager's text saying what is wrong."""
    if probe.count != expected:
        return f"The joined video has {probe.count} frames; its shots have {expected}."
    if probe.timebase is None or probe.first is None:
        return "FFmpeg did not list the joined video's frames."
    frame = Fraction(rate[1], rate[0])
    if container in TIMESCALE_CONTAINERS:
        if len(probe.durations) != 1:
            return "The joined video's frames do not all last the same time."
        if probe.span() != expected * frame:
            return f"The joined video lasts {float(probe.span()):.3f} s; its shots last {float(expected * frame):.3f} s."
        return None
    start = (probe.last - probe.first) * probe.timebase
    if abs(start - (expected - 1) * frame) > WEBM_SLACK_FRAMES * frame:
        return f"The joined video's last frame starts at {float(start):.3f} s, not {float((expected - 1) * frame):.3f} s."
    return None
