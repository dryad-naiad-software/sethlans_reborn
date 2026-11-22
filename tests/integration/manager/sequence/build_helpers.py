# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the Unreal sequence build tests (chunk 6: the work
queue, videos, sound cuts, merged copies).  Not a test module.

:func:`manual_queue` lets the queue accept keys without its thread;
:func:`build_all` then advances the submission and runs the queued tasks
in the test's thread, round after round, with the ``on_commit`` callbacks
executed, until nothing is left.  :class:`FakeFfmpeg` stands in for
``run_ffmpeg_output``: it writes each command's output file, records the
command (and the concat list's text) and reports a frame count.
"""

import contextlib
import os

from workers import video_queue
from workers.services.sequence_assembly import advance

from ..unreal_pilot_helpers import claim, ready_worker
from .sound_helpers import audio_jobs_of, post_sound
from .submission_helpers import (
    done_discovery, ffmpeg_ready, measure_submission, piece_body, pieces_of, post_piece, submit, upload_all,
)

BOOT = 'test-boot-0001'
SMALL_FRAMES = 14  # SMALL_SHOTS: 4 + 6 + 4
RUN_FFMPEG = 'workers.video_sequence_build.run_ffmpeg_output'


def version(admin_client, asset, worker, client, body=None, **overrides):
    """Read the shots and submit; ``overrides`` go to the submission body."""
    done_discovery(admin_client, asset, worker, client, body)
    with ffmpeg_ready():
        submission, _ = submit(admin_client, asset, **overrides)
    return submission


def items(row):
    """``(video, sound, merged)`` statuses of a shot or a submission."""
    return row.video_status, row.sound_status, row.sound_video_status


@contextlib.contextmanager
def manual_queue(boot=BOOT):
    video_queue.stop_for_tests()
    video_queue.start_manual_for_tests(boot)
    try:
        with ffmpeg_ready():
            yield boot
    finally:
        video_queue.stop_for_tests()


def build_all(capture, submission_id, rounds=12):
    """Advance and run the queue until no task runs; returns how many tasks ran."""
    total = 0
    for _ in range(rounds):
        with capture(execute=True):
            advance(submission_id)
        with capture(execute=True):
            ran = video_queue.run_pending_for_tests()
        if not ran:
            return total
        total += ran
    raise AssertionError("The builds did not settle.")


class FakeFfmpeg:
    """A ``run_ffmpeg_output`` stand-in; ``code`` makes every build command fail.

    A probe (``-f framecrc``) lists ``frames`` packets (an int, or a list used
    one per probe) at the rate and timescale of the last build command, each
    lasting the same number of ticks unless ``uneven``.
    """

    def __init__(self, frames=SMALL_FRAMES, code=0, uneven=False):
        self.frames = list(frames) if isinstance(frames, (list, tuple)) else frames
        self.code = code
        self.uneven = uneven
        self.commands = []
        self.lists = []
        self.patterns = []
        self.rate = (30, 1)
        self.timescale = None

    def _probe(self, on_line):
        count = self.frames.pop(0) if isinstance(self.frames, list) else self.frames
        num, den = self.rate
        if self.timescale is None:  # WebM: 1 ms timebase, rounded times
            on_line('#tb 0: 1/1000\n')
            pts = [round(i * 1000 * den / num) for i in range(count)]
            durations = [round(1000 * den / num)] * count
        else:
            step = self.timescale * den // num
            on_line(f'#tb 0: 1/{self.timescale}\n')
            durations = [step + (i % 2 if self.uneven else 0) for i in range(count)]
            pts = [sum(durations[:i]) for i in range(count)]
        for i in range(count):
            on_line(f'0, {pts[i]}, {pts[i]}, {durations[i]}, 100, 0x0000abcd\n')

    def _remember(self, cmd):
        if '-framerate' in cmd:
            num, _, den = cmd[cmd.index('-framerate') + 1].partition('/')
            self.rate = (int(num), int(den or 1))
            pattern = cmd[cmd.index('-i') + 1]
            self.patterns.append((pattern, sorted(os.listdir(os.path.dirname(pattern)))))
        if '-framerate' in cmd or 'concat' in cmd:
            flag = '-video_track_timescale'
            self.timescale = int(cmd[cmd.index(flag) + 1]) if flag in cmd else None

    def __call__(self, cmd, _subject, timeout=None, on_line=None):
        self.commands.append(list(cmd))
        if 'framecrc' in cmd:
            self._probe(on_line)
            return 0, ''
        self._remember(cmd)
        if '-f' in cmd and cmd[cmd.index('-f') + 1] == 'concat':
            with open(cmd[cmd.index('-i') + 1], encoding='utf-8') as handle:
                self.lists.append(handle.read())
        if self.code:
            return self.code, 'Error while encoding'
        with open(cmd[-1], 'wb') as handle:
            handle.write(b'fake video ' + os.path.basename(cmd[-1]).encode())
        return 0, ''

    def of_kind(self, kind):
        """Commands by kind: ``frames`` (-framerate), ``concat``, ``mux`` (two inputs) or ``probe``."""
        def kind_of(cmd):
            if 'framecrc' in cmd:
                return 'probe'
            if '-framerate' in cmd:
                return 'frames'
            if 'concat' in cmd:
                return 'concat'
            return 'mux' if cmd.count('-i') == 2 else 'other'
        return [cmd for cmd in self.commands if kind_of(cmd) == kind]


def finish_pieces(client, worker, submission, fail_last=False):
    """Measure the key, then claim, upload and finish every piece (the last fails when asked)."""
    measure_submission(submission.asset)
    pieces = pieces_of(submission)
    for number, piece in enumerate(pieces, 1):
        if fail_last and number == len(pieces):
            piece.refresh_from_db()
            piece.status, piece.error_message = 'ERROR', 'GPU lost'
            piece.save(update_fields=['status', 'error_message'])
            continue
        ready_worker(worker)
        assert claim(client, piece, worker).status_code == 200
        piece.refresh_from_db()
        upload_all(client, piece)
        assert post_piece(client, piece, piece_body(piece)).status_code == 200


def finish_sound(client, worker, submission):
    """Claim every audio job and post its ``.wav`` (a measured run; per-machine D10: a fits result first)."""
    measure_submission(submission.asset, worker=worker, submission=submission)
    for job in audio_jobs_of(submission):
        ready_worker(worker)
        assert claim(client, job, worker).status_code == 200
        job.refresh_from_db()
        assert post_sound(client, job).status_code == 200
