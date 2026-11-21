# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cut and join sound with the stdlib ``wave`` module (Unreal sequence
rendering FR-SV-5, D15).  Pure functions on files the manager made; no
database access, no ffmpeg.

A run's ``.wav`` is kept as rendered (R27).  For a shot part ``[s, e)`` in
a run rendered from frame ``r0`` at ``num/den`` fps, with ``spf = rate *
den / num`` samples per frame and the run's ``applied_samples`` correction
``offset`` (positive = the sound leads the picture), the cut is the samples::

    [round((s - r0) * spf) - offset, round((e - r0) * spf) - offset)

Samples before the file's start or after its end are silence, so every
cut has exactly its window's length and the cuts of a run join without a
gap.  A skipped latency compensation needs no adjustment here: the
measured offset already includes it (D15).  Files are streamed in chunks,
never read whole.
"""

import wave

CHUNK_SAMPLES = 1 << 16


def samples_per_frame(rate, num, den):
    return rate * den / num


def cut_window(start, end, r0, rate, num, den, offset):
    """``(first, last)`` sample indexes (``last`` exclusive) of the shot part ``[start, end)``."""
    spf = samples_per_frame(rate, num, den)
    return round((start - r0) * spf) - offset, round((end - r0) * spf) - offset


def _silence(writer, samples, frame_bytes):
    block = bytes(frame_bytes * min(samples, CHUNK_SAMPLES))
    while samples > 0:
        count = min(samples, CHUNK_SAMPLES)
        writer.writeframesraw(block[:count * frame_bytes])
        samples -= count


def _copy(reader, writer, first, count):
    reader.setpos(first)
    while count > 0:
        data = reader.readframes(min(count, CHUNK_SAMPLES))
        if not data:
            raise wave.Error("The sound file ends before its header says.")
        writer.writeframesraw(data)
        count -= len(data) // (reader.getnchannels() * reader.getsampwidth())


def _open_writer(target, params):
    writer = wave.open(target, 'wb')
    writer.setnchannels(params.nchannels)
    writer.setsampwidth(params.sampwidth)
    writer.setframerate(params.framerate)
    return writer


def write_cut(source, target, first, last):
    """Write the samples ``[first, last)`` of ``source`` to ``target``, silence outside the file.

    Returns:
        The number of samples written (``last - first``).
    """
    length = max(0, last - first)
    with wave.open(source, 'rb') as reader:
        params = reader.getparams()
        frame_bytes = params.nchannels * params.sampwidth
        before = max(0, min(last, 0) - first)
        low, high = max(first, 0), min(last, params.nframes)
        inside = max(0, high - low)
        writer = _open_writer(target, params)
        try:
            _silence(writer, before, frame_bytes)
            if inside:
                _copy(reader, writer, low, inside)
            _silence(writer, length - before - inside, frame_bytes)
        finally:
            writer.close()
    return length


def cut_shot(source, target, part, run, rate_fraction, offset):
    """Cut one shot part ``(start, end)`` of a run ``r0`` from ``source``; returns the samples written.

    ``rate_fraction`` is the sequence's ``(num, den)``; the sample rate is read from ``source``.
    """
    with wave.open(source, 'rb') as reader:
        rate = reader.getframerate()
    first, last = cut_window(part[0], part[1], run, rate, rate_fraction[0], rate_fraction[1], offset)
    return write_cut(source, target, first, last)


def join(sources, target):
    """Join ``sources`` (same channels, width and rate) into ``target`` in order; returns the samples.

    Raises:
        ValueError: no source, or a source whose format differs from the first.
    """
    if not sources:
        raise ValueError("Nothing to join.")
    total, writer, shape = 0, None, None
    try:
        for source in sources:
            with wave.open(source, 'rb') as reader:
                params = reader.getparams()
                if writer is None:
                    writer, shape = _open_writer(target, params), params[:3]
                elif params[:3] != shape:
                    raise ValueError("The sound files to join differ in format.")
                _copy(reader, writer, 0, params.nframes)
                total += params.nframes
    finally:
        if writer is not None:
            writer.close()
    return total
