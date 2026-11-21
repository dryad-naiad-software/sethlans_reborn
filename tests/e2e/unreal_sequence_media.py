# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Download and media checks for the real Unreal sequence E2E (sequence
rendering chunk 9).

Downloads are streamed to files in the test's temp dir. Videos are read
with the manager's own FFmpeg (``GET /api/ffmpeg-status/``) as a
``framecrc`` packet list, parsed by the manager's ``FrameProbe`` (U-8): an
exact frame count, one frame duration and an exact length. Sound files are
read with the stdlib ``wave`` module, frame zips with ``zipfile``.
"""

import io
import logging
import re
import subprocess
import time
import wave
import zipfile
from fractions import Fraction

from PIL import Image

from workers.video_probe import FrameProbe, problem

logger = logging.getLogger(__name__)

FFMPEG_STATUS_URL = "/api/ffmpeg-status/"
FFMPEG_WAIT = 300
_PROBE_TIMEOUT = 600
_MIN_BRIGHTNESS = 10
# Whole-version zip entries: <index, 4 digits>_<shot slug>/<shot slug>.<frame, 7 digits>.<ext>
WHOLE_ENTRY = re.compile(r"^(\d{4})_([^/]+)/\2\.(\d{7})\.(png|exr)$")
SHOT_ENTRY = re.compile(r"^([^/]+)\.(\d{7})\.(png|exr)$")


def wait_for_ffmpeg(session, base_url, timeout=FFMPEG_WAIT):
    """The manager's FFmpeg path once its parts check is ready (videos need it)."""
    deadline = time.monotonic() + timeout
    block = {}
    while time.monotonic() < deadline:
        resp = session.get(f"{base_url}{FFMPEG_STATUS_URL}", timeout=30)
        resp.raise_for_status()
        block = resp.json().get("ffmpeg") or {}
        if block.get("status") == "ready" and block.get("path"):
            return block["path"]
        assert block.get("status") != "failed", f"The manager's FFmpeg failed: {block}"
        time.sleep(2)
    raise TimeoutError(f"The manager's FFmpeg was not ready within {timeout}s: {block}")


def download(session, url, dest):
    """Stream ``url`` to ``dest``; returns the response headers."""
    with session.get(url, stream=True, timeout=300) as resp:
        assert resp.status_code == 200, f"GET {url}: {resp.status_code} {resp.text[:500]}"
        with open(dest, "wb") as out:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                out.write(chunk)
        return dict(resp.headers)


def probe(ffmpeg, path, stream):
    """The ``framecrc`` packet list of ``stream`` (``0:v:0`` or ``0:a:0``) in ``path``."""
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
         "-map", stream, "-c", "copy", "-f", "framecrc", "-"],
        capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
    )
    assert result.returncode == 0, f"ffmpeg could not read {stream} of {path}: {result.stderr[-2000:]}"
    frames = FrameProbe()
    for line in result.stdout.splitlines():
        frames.line(line)
    return frames


def assert_video(ffmpeg, path, frames, rate, container):
    """``frames`` frames at ``rate`` (num, den), each lasting the same, spanning exactly ``frames / rate``."""
    video = probe(ffmpeg, path, "0:v:0")
    logger.info("%s: %d frames, durations %s, timebase %s", path.name, video.count, video.durations, video.timebase)
    assert video.count == frames, f"{path.name} has {video.count} frames, expected {frames}"
    assert len(video.durations) == 1, f"{path.name} frame durations vary: {video.durations}"
    assert video.span() == frames * Fraction(rate[1], rate[0]), f"{path.name} lasts {float(video.span())} s"
    found = problem(video, frames, container, rate)
    assert found is None, f"{path.name}: {found}"


def assert_merged(ffmpeg, path, frames, rate):
    """The merged copy keeps every video frame and its sound lasts as long as the video (within one frame)."""
    video = probe(ffmpeg, path, "0:v:0")
    assert video.count == frames, f"{path.name} has {video.count} frames, expected {frames}"
    audio = probe(ffmpeg, path, "0:a:0")
    assert audio.count > 0 and audio.timebase is not None, f"{path.name} has no audio packets"
    # The last audio packet's start plus the longest packet: AAC packets are all 1024 samples but the last.
    sound = (audio.last - audio.first + max(audio.durations)) * audio.timebase
    picture = (video.last - video.first + max(video.durations)) * video.timebase
    frame = Fraction(rate[1], rate[0])
    assert abs(sound - picture) <= frame, f"{path.name}: sound lasts {float(sound):.3f} s, video {float(picture):.3f} s"


def wav_params(path):
    """``(channels, sample width, rate, samples)`` of a PCM ``.wav``."""
    with wave.open(str(path), "rb") as reader:
        return reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()


def zip_frames(path, whole):
    """``{prefix: [frame, ...]}`` of a frames zip: prefix ``(index, slug)`` (whole) or ``slug`` (one shot)."""
    pattern = WHOLE_ENTRY if whole else SHOT_ENTRY
    found = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            match = pattern.match(name)
            assert match, f"Unexpected zip entry {name!r} in {path.name}"
            if whole:
                key, frame = (int(match.group(1)), match.group(2)), int(match.group(3))
            else:
                key, frame = match.group(1), int(match.group(2))
            found.setdefault(key, []).append(frame)
    return {key: sorted(frames) for key, frames in found.items()}


def zip_member(path, frame):
    """The bytes of the entry for edit frame ``frame`` in a frames zip."""
    suffix = f".{frame:07d}.png"
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(suffix)]
        assert len(names) == 1, f"{path.name}: {len(names)} entries for frame {frame}"
        return archive.read(names[0])


def assert_png(data, size, what):
    """A valid PNG of ``size`` that is not solid black (as ``verify_image_output``)."""
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG", f"{what} is {image.format}, not PNG"
    assert image.size == size, f"{what} is {image.size}, expected {size}"
    extrema = image.convert("RGB").getextrema()
    brightest = max(high for _low, high in extrema)
    assert brightest > _MIN_BRIGHTNESS, f"{what} is solid black (max brightness {brightest})"
