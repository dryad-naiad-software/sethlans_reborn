# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Excerpts of ``.wav`` files and the mix of a run's sound sources (sequence
rendering D23, FR-SA-10). Pure stdlib (``wave``, ``array``, ``math``); no
numpy, no new dependency.

``Source`` reads **excerpts only** of one open ``.wav`` (``wave.setpos``),
downmixed to mono floats in [-1, 1]: integer PCM of 8, 16, 24 or 32 bits.
It records the largest read it made (``largest_read``, in sample frames),
so a test can bound the memory a measurement uses.

``mix(sections, r0, rate, num, den, n0, count)`` is the mono mix of the
supported sections for render samples ``n0 .. n0 + count - 1``. Render
sample ``n`` (at the render's rate ``R``) lies ``t = n / R`` seconds after
frame ``r0``; a section starting at edit frame ``s`` plays source position
``x = (t - (s - r0) x den / num) x Rs + start_offset`` (``Rs`` the source's
own rate) while ``t`` lies in the section; looping wraps ``x`` by the
wave's length, else positions outside the wave are silence. The value is
the linear interpolation ``a + (b - a) x (x - i)`` with ``i = floor(x)``,
which also resamples a source at another rate (44.1 kHz into a 48 kHz
render). Values are scaled by the section's volume, summed and clipped.
"""
import math
import sys
from array import array
from dataclasses import dataclass
from itertools import islice
from operator import add

SCALES = {1: 1 / 128, 2: 1 / 32768, 3: 1 / 2 ** 31, 4: 1 / 2 ** 31}


class Source:
    """One ``.wav`` (an open ``wave`` reader) read as mono excerpts."""

    def __init__(self, reader):
        self.reader = reader
        self.channels, self.width, self.rate, self.frames = reader.getparams()[:4]
        if self.width not in SCALES or reader.getcomptype() != 'NONE':
            raise ValueError("not integer PCM of 8, 16, 24 or 32 bits")
        self.largest_read = 0

    def _ints(self, data: bytes):
        if self.width == 1:
            return [v - 128 for v in data]
        if self.width == 3:  # 24-bit: shift each sample into the top of a 32-bit int (C-speed slicing)
            padded = bytearray(len(data) // 3 * 4)
            padded[1::4], padded[2::4], padded[3::4] = data[0::3], data[1::3], data[2::3]
            data = bytes(padded)
        values = array('h' if self.width == 2 else 'i')
        values.frombytes(data[:len(data) - len(data) % values.itemsize])
        if sys.byteorder == 'big':
            values.byteswap()
        return values

    def read(self, start: int, count: int) -> list:
        """Mono floats of the sample frames ``start .. start + count - 1`` (inside the file)."""
        if count <= 0:
            return []
        self.largest_read = max(self.largest_read, count)
        self.reader.setpos(start)
        ints = self._ints(self.reader.readframes(count))
        ch, scale = self.channels, SCALES[self.width]
        if ch == 1:
            out = [v * scale for v in ints]
        else:
            scale /= ch
            out = [math.fsum(t) * scale for t in zip(*(ints[c::ch] for c in range(ch)))] if ch > 2 else \
                [(a + b) * scale for a, b in zip(ints[0::2], ints[1::2])]
        return out + [0.0] * (count - len(out))  # a short file reads as silence at its end

    def excerpt(self, lo: int, hi: int, looping: bool = False) -> list:
        """Mono floats for the positions ``lo .. hi - 1``: wrapped by the length when ``looping``, else 0 outside."""
        n, length = self.frames, hi - lo
        if n <= 0 or length <= 0:
            return [0.0] * max(length, 0)
        if looping:
            if length >= n:
                whole = self.read(0, n)
                return [whole[i % n] for i in range(lo, hi)]
            start = lo % n
            if start + length <= n:
                return self.read(start, length)
            return self.read(start, n - start) + self.read(0, length - (n - start))
        a, b = max(lo, 0), min(hi, n)
        if a >= b:
            return [0.0] * length
        return [0.0] * (a - lo) + self.read(a, b - a) + [0.0] * (hi - b)


@dataclass(frozen=True)
class Section:
    """One supported audio section: edit frames ``[start, end)``, its source and how it plays."""
    start: int
    end: int
    offset: int          # the start offset in source samples, at the source's own rate
    volume: float
    looping: bool
    source: Source


def _section(sec: Section, r0: int, rate: int, num: int, den: int, n0: int, count: int):
    """``(first render sample, values)`` of one section inside ``n0 .. n0 + count - 1``, or ``None``."""
    t_start, t_end = (sec.start - r0) * den / num, (sec.end - r0) * den / num
    lo, hi = max(n0, math.ceil(t_start * rate)), min(n0 + count, math.ceil(t_end * rate))
    if lo >= hi:
        return None
    src = sec.source
    step = src.rate / rate
    x0 = (lo / rate - t_start) * src.rate + sec.offset
    first = math.floor(x0)
    last = math.floor(x0 + (hi - lo - 1) * step) + 1
    seg = src.excerpt(first, last + 2, sec.looping)  # one spare position against float rounding at the end
    volume = sec.volume
    if src.rate == rate:  # one fraction for every sample: interpolate by zip (fast path)
        frac = x0 - first
        keep = 1.0 - frac
        values = [(a * keep + b * frac) * volume for a, b in zip(seg, islice(seg, 1, hi - lo + 1))]
        return lo, values
    values = []
    base = x0 - first
    for k in range(hi - lo):
        x = base + k * step
        i = int(x)
        a = seg[i]
        values.append((a + (seg[i + 1] - a) * (x - i)) * volume)
    return lo, values


def mix(sections, r0: int, rate: int, num: int, den: int, n0: int, count: int) -> list:
    """The clipped mono mix of ``sections`` for render samples ``n0 .. n0 + count - 1`` (FR-SA-10)."""
    out = [0.0] * count
    for sec in sections:
        part = _section(sec, r0, rate, num, den, n0, count)
        if part is None:
            continue
        lo, values = part
        at = lo - n0
        out[at:at + len(values)] = list(map(add, out[at:at + len(values)], values))
    return [1.0 if v > 1.0 else -1.0 if v < -1.0 else v for v in out]
