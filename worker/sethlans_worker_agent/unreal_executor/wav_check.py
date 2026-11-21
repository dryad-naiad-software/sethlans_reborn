# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An audio job's output and log checks (sequence rendering FR-SA-5, D14, U-4).

* ``collect(output_dir)``: exactly one ``.wav``, directly in ``output/``,
  and no image or video file; the folder is walked without following links
  (``fs_safe.walk_tree``).
* ``check_handle(handle, frames, rate_fraction)``: the stdlib ``wave``
  module reads the **open handle** (``fs_safe.open_output_file``) and the
  shared FR-SA-5 rules (``shared.unreal.sound_result.check_wav_params``,
  the manager checks the same) apply: 16-bit PCM, 1-8 channels, 8-192 kHz,
  within 256 samples of ``frames x rate x den / num`` (7 frames more when MRG
  skipped its head compensation: it still drains the tail); the header's length
  must fit in the file. Anything else raises ``JobFailure`` with fixed text.
* ``scan_log(log_path, max_bytes)``: the render log's audio streaming fault
  lines (``seektable``, ``Decoder error``, ``preventing playback``,
  ``NumberOfLoadsInFlight``; any one fails the job with ``FAULT_MESSAGE``)
  and MRG's "Skipping compensation" warning
  (``MovieGraphDefaultAudioRenderer.cpp:156``: the first segment was shorter
  than the 7-frame latency compensation), which makes the file 7 frames
  longer. A line without a newline is scanned in pieces of at most 64 KiB
  (the tail of each piece is kept, so a word across two pieces is found), and
  the warning is two ordered plain ``find`` calls: no regular expression runs
  over untrusted text.
"""
import logging
import os
import wave
from dataclasses import dataclass
from pathlib import Path

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from shared.unreal.sound_result import SoundResultError, check_wav_params, expected_samples

logger = logging.getLogger(__name__)

FAULT_MESSAGE = "The sound render hit Unreal's audio streaming fault; retry the sound."
FAULT_WORDS = ('seektable', 'decoder error', 'preventing playback', 'numberofloadsinflight')
LATENCY_WORDS = ('audio latency compensation', 'skipping compensation')  # in this order, on one line
MEDIA = frozenset({'png', 'exr', 'jpg', 'jpeg', 'bmp', 'tga', 'tif', 'tiff', 'hdr', 'mp4', 'mov', 'avi', 'mkv',
                   'webm'})
CHUNK = 1 << 20
CARRY_MAX, CARRY_KEEP = 64 * 1024, 256


@dataclass(frozen=True)
class WavInfo:
    rate: int
    channels: int
    samples: int
    expected: int


@dataclass(frozen=True)
class LogFacts:
    fault: bool
    latency_skipped: bool


def collect(output_dir) -> Path:
    """FR-SA-5: the one ``.wav`` the render wrote, else ``JobFailure``."""
    output_dir = Path(output_dir)
    try:
        files = fs_safe.walk_tree(output_dir).files
    except OSError as exc:
        logger.error("Unreal sound output under %s is unsafe: %s", output_dir, exc)
        raise JobFailure(f"Unreal render output is unsafe ({type(exc).__name__}).") from exc
    wavs = [rel for rel in files if rel.lower().endswith('.wav')]
    media = sorted(rel for rel in files if rel.rsplit('.', 1)[-1].lower() in MEDIA)
    if media:
        raise JobFailure(f"The sound render also wrote {len(media)} image or video files; expected only a .wav.")
    if len(wavs) != 1:
        raise JobFailure(f"The sound render wrote {len(wavs)} .wav files; expected 1.")
    if '/' in wavs[0]:
        raise JobFailure("The sound render wrote its .wav to a subdirectory; preset path tokens leaked through "
                         "the override.")
    return output_dir / wavs[0]


def check_handle(handle, frames: int, rate_fraction, latency_skipped: bool = False) -> WavInfo:
    """FR-SA-5 on the open ``handle`` (positioned anywhere; left at 0); ``rate_fraction`` is ``(num, den)``."""
    num, den = rate_fraction
    try:
        size = os.fstat(handle.fileno()).st_size
        handle.seek(0)
        with wave.open(handle, 'rb') as reader:
            channels, width, rate, samples = reader.getparams()[:4]
        if samples * channels * width > size:
            raise SoundResultError('file', "The sound file is shorter than its header says.")
        expected = expected_samples(frames, rate, num, den, latency_skipped)
        check_wav_params(channels, width, rate, samples, expected)
    except SoundResultError as exc:
        raise JobFailure(str(exc)) from None
    except (wave.Error, EOFError, OSError, ValueError) as exc:
        raise JobFailure(f"The sound render wrote an unreadable .wav ({type(exc).__name__}).") from None
    finally:
        try:
            handle.seek(0)
        except (OSError, ValueError):
            pass
    return WavInfo(rate=rate, channels=channels, samples=samples, expected=expected)


def scan_log(log_path, max_bytes: int) -> LogFacts:
    """The fault lines and the latency-compensation warning in at most ``max_bytes`` of the render log."""
    fault = skipped = False
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as log:
            read, carry = 0, ''
            while read < max_bytes:
                text = log.read(min(CHUNK, max_bytes - read))
                if not text:
                    break
                read += len(text)
                lines = (carry + text).split('\n')
                carry = lines.pop()
                if len(carry) > CARRY_MAX:  # a line with no newline: scan it now, keep only its tail
                    lines.append(carry)
                    carry = carry[-CARRY_KEEP:]
                fault, skipped = _scan_lines(lines, fault, skipped)
            fault, skipped = _scan_lines([carry], fault, skipped)
    except OSError as exc:
        raise JobFailure(f"Unreal render log could not be read ({type(exc).__name__}).") from exc
    return LogFacts(fault=fault, latency_skipped=skipped)


def _scan_lines(lines, fault: bool, skipped: bool):
    for line in lines:
        lowered = line.lower()
        fault = fault or any(word in lowered for word in FAULT_WORDS)
        at = lowered.find(LATENCY_WORDS[0])
        skipped = skipped or (at >= 0 and lowered.find(LATENCY_WORDS[1], at + len(LATENCY_WORDS[0])) >= 0)
    return fault, skipped
