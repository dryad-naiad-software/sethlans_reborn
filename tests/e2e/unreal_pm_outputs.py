# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
AC-E9 output checks of the per-machine memory test E2E (chunk 9), the same
checks as the sequence E2E (``test_unreal_sequence``), as functions on a
``MachineTestFlow`` instance:

- ``check_frames``: the version's frames zip holds every frame of every
  shot (``end`` exclusive) and every frame has the requested size; the first
  kept frame of each piece is a valid, not black PNG.
- ``check_sound_and_videos``: the audio job measured the offset near U-9's
  +9,418 samples and the cut used it; the whole version's and each shot's
  silent and merged videos have the exact frame count and one frame
  duration, and each ``.wav`` has frames x samples-per-frame samples.
"""

import io
import logging
import zipfile

from PIL import Image

from tests.e2e import unreal_e2e_config as cfg
from tests.e2e import unreal_sequence_media as media
from tests.e2e.unreal_pm_flow import VERSIONS

logger = logging.getLogger(__name__)


def _fetch(test, path, name):
    dest = test.temp_dir / name
    headers = media.download(test.session, f"{test.base_url}{path}", dest)
    assert "attachment" in headers.get("Content-Disposition", ""), headers
    return dest


def check_frames(test, version, pieces, size, label):
    """Every frame of every shot at ``size``; the first kept frame of each piece not black."""
    dest = _fetch(test, f"{VERSIONS}/{version['id']}/download-frames/", f"{label}_frames.zip")
    shots = {shot["index"]: shot for shot in version["shots"]}
    found = media.zip_frames(dest, whole=True)
    assert sorted(index for index, _slug in found) == sorted(shots), (label, found.keys())
    for (index, _slug), frames in found.items():
        expected = list(range(shots[index]["start_frame"], shots[index]["end_frame"]))
        assert frames == expected, f"{label} shot {index}: frames {frames[:3]}..{frames[-3:]}"
    with zipfile.ZipFile(dest) as archive:
        sizes = {Image.open(io.BytesIO(archive.read(name))).size for name in archive.namelist()}
    assert sizes == {tuple(size)}, f"{label}: frame sizes {sizes}, expected {tuple(size)}"
    for job in pieces:
        media.assert_png(media.zip_member(dest, job["start_frame"]), tuple(size), f"{label} frame {job['start_frame']}")
    logger.info("%s: %d frames of %d shots at %s", label, sum(len(f) for f in found.values()), len(shots), size)


def _spf(run):
    num, den = cfg.SEQUENCE_RATE
    assert run["rate"] * den % num == 0, run["rate"]
    return run["rate"] * den // num


def _check_sound_run(version, audio):
    job = audio[0]
    assert job["status"] == "DONE", f"Audio job: {job['status']} {job.get('error_message')}"
    runs = version["sound_runs"]
    assert len(runs) == 1, runs
    run = runs[0]
    offset = run["offset"]
    assert offset["status"] == "measured", offset
    assert abs(offset["offset_samples"] - cfg.SOUND_OFFSET_SAMPLES) <= cfg.SOUND_OFFSET_TOLERANCE, offset
    assert (run["applied_source"], run["applied_samples"]) == ("measured", offset["offset_samples"]), run
    assert not run["silent"] and not run["latency_compensation_skipped"], run
    lead = run["start"] - run["r0"]
    expected = (run["end"] - run["start"] + lead) * _spf(run)
    assert abs(run["samples"] - expected) <= cfg.WAV_LENGTH_TOLERANCE, (run["samples"], expected)
    return _spf(run)


def _check_item(test, base, label, frames, spf, ffmpeg):
    video = _fetch(test, f"{base}download-video/", f"{label}.mp4")
    media.assert_video(ffmpeg, video, frames, cfg.SEQUENCE_RATE, "mp4")
    merged = _fetch(test, f"{base}download-video/?sound=1", f"{label}_sound.mp4")
    media.assert_merged(ffmpeg, merged, frames, cfg.SEQUENCE_RATE)
    channels, width, _rate, samples = media.wav_params(_fetch(test, f"{base}download-sound/", f"{label}.wav"))
    assert width == 2 and channels >= 1, (channels, width)
    assert samples == frames * spf, f"{label}.wav has {samples} samples, expected {frames * spf}"


def check_sound_and_videos(test, version, audio, ffmpeg, label):
    """The sound offset and the whole and per-shot videos, merged copies and sound files."""
    spf = _check_sound_run(version, audio)
    for row in [version, *version["shots"]]:
        items = {item: row[item] for item in ("video_status", "sound_status", "sound_video_status")}
        assert set(items.values()) == {"DONE"}, (label, items)
    root = f"{VERSIONS}/{version['id']}/"
    total = sum(shot["end_frame"] - shot["start_frame"] for shot in version["shots"])
    _check_item(test, root, f"{label}_whole", total, spf, ffmpeg)
    for shot in version["shots"]:
        count = shot["end_frame"] - shot["start_frame"]
        _check_item(test, f"{root}shots/{shot['index']}/", f"{label}_shot_{shot['index']}", count, spf, ffmpeg)
