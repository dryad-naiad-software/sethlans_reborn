# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Synthetic sound for the sound-offset tests (sequence rendering AC-44..49): seeded noise bursts with tones, in-memory
``.wav`` files and ``sound_mix`` readers over them. No real audio file, no machine data. Not a test module.
"""
import io
import math
import random
import wave
from array import array

from sethlans_worker_agent.unreal_executor import sound_mix

RATE, FPS = 48_000, 30
SPF = RATE // FPS  # samples per frame at 48 kHz and 30 fps


def program(n, rate=RATE, seed=1, burst_s=0.1, dc=0.0):
    """``n`` samples of noise whose level changes every ``burst_s`` seconds, plus a 440 Hz tone (mono, in [-1, 1])."""
    rnd = random.Random(seed)
    step, out, level = max(1, round(burst_s * rate)), [], 0.3
    tone = [0.15 * math.sin(2 * math.pi * 440 * i / rate) for i in range(rate)]
    for i in range(n):
        if i % step == 0:
            level = rnd.uniform(0.02, 0.5)
        out.append(level * (rnd.random() * 2 - 1) + tone[i % rate] + dc)
    return out


def tone(n, rate=RATE, hz=440.0, level=0.4):
    return [level * math.sin(2 * math.pi * hz * i / rate) for i in range(n)]


def shifted(src, lag, n, snr_db=None, seed=7, dc=0.0):
    """``n`` rendered samples where sample ``p`` holds ``src[p + lag]`` (0 outside), plus noise at ``snr_db``."""
    out = [src[p + lag] if 0 <= p + lag < len(src) else 0.0 for p in range(n)]
    if snr_db is not None:
        power = sum(v * v for v in out) / max(1, n)
        sigma = math.sqrt(power / 10 ** (snr_db / 10))
        rnd = random.Random(seed)
        out = [v + rnd.gauss(0.0, sigma) for v in out]
    return [v + dc for v in out]


def wav_bytes(samples, rate=RATE, channels=2, width=2):
    """A PCM ``.wav`` (``width`` bytes per sample) with every channel holding ``samples``."""
    full = (1 << (8 * width - 1)) - 1
    ints = [max(-full - 1, min(full, int(round(v * full)))) for v in samples]
    if width == 1:
        data = bytes(v + 128 for v in ints for _ in range(channels))  # 8-bit PCM is unsigned
    elif width == 3:
        data = b''.join(v.to_bytes(3, 'little', signed=True) for v in ints for _ in range(channels))
    else:
        data = array('h' if width == 2 else 'i', [v for v in ints for _ in range(channels)]).tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(data)
    return buffer.getvalue()


def source(samples, rate=RATE, channels=2, width=2):
    """A ``sound_mix.Source`` over an in-memory ``.wav`` of ``samples``."""
    return sound_mix.Source(wave.open(io.BytesIO(wav_bytes(samples, rate, channels, width)), 'rb'))


def section(src, start=0, end=10_000, offset=0, volume=1.0, looping=False):
    return sound_mix.Section(start, end, offset, volume, looping, src)


def shots(count, frames, first=0, start_index=1):
    """``count`` back-to-back shot parts ``(index, start, end)`` of ``frames`` frames each."""
    return [(start_index + i, first + i * frames, first + (i + 1) * frames) for i in range(count)]
