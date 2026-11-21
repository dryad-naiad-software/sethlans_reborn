# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sound offset of one audio run (sequence rendering D23, D24, FR-SA-10):
where the rendered ``.wav`` lines up with the mix of the sequence's raw
sound sources. Pure stdlib (``math.sumprod``); no numpy.

**Eligible shots**: the run's shot parts inside ``[r0, end)`` that last at
least ``max(60 frames, 2 s)``. Up to 16 are used, spread evenly: with more
than 16, the eligible shots are cut into 16 equal buckets and each bucket
gives its first shot whose mix has sound (RMS above -50 dBFS over the
middle 2 s), tried from the bucket's start (first bucket), its end (last
bucket) or its middle (the others), at most 3 tries per bucket; so the first
and last eligible shots are used when they have sound, and at most 48 mix
excerpts are ever made.

**Per shot**: the middle 2 s of the render (``W`` samples) against the mix
over the same 2 s plus ``M`` samples on each side (just over 1 s); both are
mono. Stage 1 correlates 1 ms RMS envelopes (``block = rate // 1000``
samples) over lags of -1 s .. +1 s in block steps; stage 2 refines the best
lag over +-64 samples on the 4,096 samples at the excerpt's centre. Every
correlation is **zero-mean normalised** (Pearson), so a steady sound does
not score high at every lag. The envelope peak is **ambiguous** when it
beats the best peak more than 20 ms away by less than 0.10 (a loop shorter
than the window, a steady tone), and also when the refined peak sits on the
edge of the +-64 window (the coarse lag missed the true one, which lies outside
it). ``lag_samples`` is the ``L`` for which
rendered sample ``p`` holds mix sample ``p + L`` (positive: the sound leads).

**The run** (D24): a shot is confident when it is not ambiguous and its
sample-stage peak is at least 0.80. ``measured`` needs 3 confident shots
(or, when fewer than 3 shots could be correlated and none was left out,
every one of them confident with a peak of at least 0.90) and at least 75 %
of the confident shots within 64 samples of their median, which is the
offset; ``low_confidence`` when shots were correlated but these rules fail;
``unavailable`` when nothing could be compared. The result is the
``offset`` object of ``shared.unreal.sound_result``.
"""
import math
import statistics
import time
from itertools import accumulate

from sethlans_worker_agent.unreal_executor import sound_mix
from shared.unreal import sound_result as sr

LAG_S, EXCERPT_S, MIN_SHOT_S, MIN_SHOT_FRAMES = 1.0, 2.0, 2.0, 60
REFINE, REFINE_WINDOW = 64, 4096
AMBIGUITY_S, AMBIGUITY_MARGIN = 0.020, 0.10
FEW_PEAK, MIN_CONFIDENT, AGREE_SHARE = 0.90, 3, 0.75
FLOOR_DBFS = -50.0
TRIES = 3


def _offset(status, reason, rows=(), offset=None, counts=(0, 0), unsupported=0, export_s=None, correlate_s=None):
    confident = [row for row in rows if sr.confident(row)]
    lags = [row['lag_samples'] for row in confident]
    return {'status': status, 'offset_samples': offset, 'reason': reason[:sr.REASON_MAX], 'shots_tried': len(rows),
            'shots_confident': counts[0], 'shots_agreeing': counts[1],
            'min_peak': min((row['peak'] for row in confident), default=None),
            'spread_samples': max(lags) - min(lags) if lags else None, 'unsupported_sections': unsupported,
            'export_s': None if export_s is None else round(export_s, 2),
            'correlate_s': None if correlate_s is None else round(correlate_s, 2), 'shots': list(rows)}


def unavailable(reason, unsupported=0, export_s=None, correlate_s=None):
    """D24: nothing could be compared."""
    return _offset(sr.UNAVAILABLE, reason, unsupported=unsupported, export_s=export_s, correlate_s=correlate_s)


def eligible(shots, r0, end, num, den):
    """The run's ``(index, start, end)`` shot parts inside ``[r0, end)`` lasting at least ``max(60 frames, 2 s)``."""
    least = max(MIN_SHOT_FRAMES, math.ceil(MIN_SHOT_S * num / den))
    return [(index, s, e) for index, s, e in shots if s >= r0 and e <= end and e - s >= least]


def buckets(items, k=sr.MAX_SHOTS, tries=TRIES):
    """At most ``k`` lists of candidates, spread evenly, each in the order it is tried."""
    n = len(items)
    if n <= k:
        return [[item] for item in items]
    out = []
    for i in range(k):
        part = items[i * n // k:(i + 1) * n // k]
        if i == k - 1:
            part = part[::-1]
        elif i:
            mid = len(part) // 2
            part = [part[j] for j in sorted(range(len(part)), key=lambda j, m=mid: abs(j - m))]
        out.append(part[:tries])
    return out


def envelope(x, block):
    """RMS of each whole block of ``block`` samples."""
    return [math.sqrt(math.sumprod(seg, seg) / block)
            for seg in (x[i:i + block] for i in range(0, len(x) - block + 1, block))]


def scan(ref, sig, starts):
    """The zero-mean normalised correlation of ``ref`` with ``sig[s:s + len(ref)]`` for each start ``s``."""
    n = len(ref)
    mean = math.fsum(ref) / n
    centred = [v - mean for v in ref]
    norm = math.sqrt(math.sumprod(centred, centred))
    s1 = list(accumulate(sig, initial=0.0))
    s2 = list(accumulate((v * v for v in sig), initial=0.0))
    out = []
    for s in starts:
        total = s1[s + n] - s1[s]
        var = s2[s + n] - s2[s] - total * total / n
        if norm <= 1e-12 or var <= 1e-12:
            out.append(0.0)
            continue
        out.append(math.sumprod(centred, sig[s:s + n]) / (norm * math.sqrt(var)))
    return out


def _clamp(value, high=1.0):
    return round(min(max(value, 0.0), high), 4)


def correlate(render_ex, mix_ex, rate, margin_blocks):
    """One shot: ``render_ex`` (``W`` samples) against ``mix_ex`` (``W + 2 M`` samples, ``M = margin_blocks x
    block``, starting ``M`` samples before it); returns the per-shot row without its ``shot``."""
    block = max(1, rate // 1000)
    lag_blocks, m = round(LAG_S * rate / block), margin_blocks * block
    scores = scan(envelope(render_ex, block), envelope(mix_ex, block),
                  [margin_blocks + k for k in range(-lag_blocks, lag_blocks + 1)])
    best = max(range(len(scores)), key=scores.__getitem__)
    apart = math.ceil(AMBIGUITY_S * rate / block)
    second = max((v for j, v in enumerate(scores) if abs(j - best) > apart), default=-1.0)
    coarse = (best - lag_blocks) * block
    reach = REFINE
    centre = len(render_ex) // 2 - REFINE_WINDOW // 2
    low = centre + m + coarse - reach
    fine = scan(render_ex[centre:centre + REFINE_WINDOW], mix_ex[low:low + REFINE_WINDOW + 2 * reach + 1],
                range(2 * reach + 1))
    at = max(range(len(fine)), key=fine.__getitem__)
    margin = scores[best] - second
    edge = at in (0, 2 * reach)  # the true peak lies outside the window: not confident
    return {'lag_samples': coarse - reach + at, 'envelope_peak': _clamp(scores[best]),
            'envelope_margin': _clamp(margin, 2.0), 'peak': _clamp(fine[at]), 'ambiguous': margin < AMBIGUITY_MARGIN or edge}


def _dbfs(values):
    rms = math.sqrt(math.sumprod(values, values) / len(values)) if values else 0.0
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def _decide(rows, few):
    """``(status, reason, offset, (confident, agreeing))`` by D24."""
    confident = [row for row in rows if sr.confident(row)]
    if not confident:
        median, agreeing = None, []
    else:
        median = statistics.median_low(row['lag_samples'] for row in confident)
        agreeing = [row for row in confident if abs(row['lag_samples'] - median) <= sr.AGREE_SAMPLES]
    counts = (len(confident), len(agreeing))
    enough = len(confident) >= MIN_CONFIDENT or (few and confident and len(confident) == len(rows)
                                                 and all(row['peak'] >= FEW_PEAK for row in rows))
    if not enough:
        ambiguous = sum(row['ambiguous'] for row in rows)
        reason = f"only {len(confident)} of {len(rows)} shots lined up"
        if ambiguous:
            reason += f" ({ambiguous} ambiguous: a repeating or steady sound)"
        return sr.LOW_CONFIDENCE, reason, median, counts
    if len(agreeing) < AGREE_SHARE * len(confident):
        return (sr.LOW_CONFIDENCE, f"the shots disagree ({len(agreeing)} of {len(confident)} within "
                                   f"{sr.AGREE_SAMPLES} samples of their median)", median, counts)
    return sr.MEASURED, '', median, counts


def measure(render, sections, shots, r0, end, num, den, unsupported=0, export_s=None, clock=time.monotonic):
    """The run's ``offset`` object; ``render`` and every section's source are ``sound_mix.Source``."""
    started = clock()
    if not sections:
        reason = (f"every audio section is unsupported ({unsupported})" if unsupported
                  else "no enabled audio section in the run")
        return unavailable(reason, unsupported, export_s, 0.0)
    candidates = eligible(shots, r0, end, num, den)
    rate = render.rate
    width, block = round(EXCERPT_S * rate), max(1, rate // 1000)
    margin_blocks = round(LAG_S * rate / block) + (REFINE + block) // block + 2
    m = margin_blocks * block
    rows = []
    for bucket in buckets(candidates):
        for index, s, e in bucket:
            p0 = round(((s + e) / 2 - r0) * den / num * rate) - width // 2
            mixed = sound_mix.mix(sections, r0, rate, num, den, p0 - m, width + 2 * m)
            if _dbfs(mixed[m:m + width]) <= FLOOR_DBFS:
                continue
            rows.append(dict({'shot': index}, **correlate(render.excerpt(p0, p0 + width), mixed, rate, margin_blocks)))
            break
    if not rows:
        reason = ("no shot of the run lasts 2 s" if not candidates else "no eligible shot has sound in the mix")
        return unavailable(reason, unsupported, export_s, clock() - started)
    status, reason, offset, counts = _decide(rows, few=len(candidates) <= sr.MAX_SHOTS)
    return _offset(status, reason, rows, offset, counts, unsupported, export_s, clock() - started)
