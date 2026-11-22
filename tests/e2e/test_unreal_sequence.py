# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Real Unreal sequence render E2E (Unreal sequence rendering chunk 9,
AC-28, U-6).

One version of two adjacent shots of the PoC sequence, end to end against
a real manager, a real worker and UE 5.8.4: shot discovery; a submission
at 640x360, PNG, ``web_h264``, sound on and merged, planned into three
pieces with a lead-in each (one boundary inside a shot, one at a cut);
the pilot measures; the pieces render and upload their kept frames only;
the audio-only job measures the sound offset (about +9,418 samples on this
preset, U-9); shot and whole videos are built with an exact frame count
and constant frame duration; the merged copies, the sound and the frame
zips download; the version ends DONE, isolated, with no orphans.

U-6 is checked by eye: the first kept frame of each piece is saved to
``SETHLANS_UNREAL_E2E_KEEP_DIR`` (default: a folder in the temp dir) and
logged.

Opt-in exactly as ``test_unreal_workflow``: ``SETHLANS_UNREAL_E2E=1`` and
``SETHLANS_UNREAL_E2E_SHARE``; elsewhere the module is skipped before any
fixture runs. About 25 minutes of GPU time cold, about 12 warm.
"""

import logging
import os
import tempfile
from pathlib import Path

import pytest

from tests.e2e import unreal_e2e_config as cfg
from tests.e2e import unreal_sequence_media as media
from tests.e2e.unreal_e2e_checks import COMMON_SUMMARY, UnrealChecks, assert_summary
from tests.e2e.unreal_network_watch import FarmNetworkWatch
from tests.e2e.unreal_workflow_harness import parse_summary, poll_sampling

logger = logging.getLogger(__name__)

_SKIP_REASON = cfg.unreal_skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")

# The first Unreal job also copies and extracts the 3.7 GB archive.
DISCOVERY_TIMEOUT = 3600
# FR-T-8: the pilot within 120 min (a cold whole-sequence pilot took ~23 min);
# the audio job usually runs first (about 5 min, U-9).
PILOT_TIMEOUT = 7200
# Three pieces of about 130 frames at 640x360 (under 0.6 s a frame, which is
# the 1080p figure from chunk 4), about a minute of start-up each, and the builds.
FINISH_TIMEOUT = 3600
VERSION_FINAL = {"DONE", "PARTLY_DONE", "ERROR", "CANCELED"}
KEEP_DIR_ENV = "SETHLANS_UNREAL_E2E_KEEP_DIR"
VERSIONS = "/api/sequence-submissions"


def _rows(data):
    return data["results"] if isinstance(data, dict) else data


class TestUnrealSequence(UnrealChecks):
    """One small sequence version: discovery, plan, pilot, pieces, sound, videos, downloads."""

    def _post(self, path, body, expected=(201,)):
        resp = self.session.post(f"{self.base_url}{path}", json=body, timeout=60)
        assert resp.status_code in expected, f"POST {path}: {resp.status_code} {resp.text}"
        return resp.json()

    def _set_piece_length(self):
        body = {"unreal_piece_seconds": cfg.PIECE_SECONDS, "unreal_lead_in_ms": cfg.LEAD_IN_MS}
        resp = self.session.patch(f"{self.base_url}/api/projects/{self.project_id}/", json=body, timeout=30)
        assert resp.status_code == 200, f"Project settings: {resp.status_code} {resp.text}"

    def _target(self):
        return {"asset": self.asset_id, "map": cfg.RENDER_SETTINGS["unreal.map"],
                "sequence": cfg.RENDER_SETTINGS["unreal.sequence"], "preset": cfg.RENDER_SETTINGS["unreal.preset"]}

    def _discover(self, watch):
        record = self._post("/api/sequence-discoveries/", self._target(), expected=(200, 201))
        found = poll_sampling(self.session, f"{self.base_url}/api/sequence-discoveries/{record['id']}/",
                              DISCOVERY_TIMEOUT, watch.sample)
        job = self._get(f"/api/jobs/{found['job']}/") if found.get("job") else {}
        logger.info("Shots job summary:\n%s", job.get("last_output"))
        assert found["status"] == "DONE", f"Discovery failed: {found.get('error_message')}"
        assert found["shot_source"] == "shot_tracks", found["shot_source"]
        assert (found["playback_start"], found["playback_end"]) == cfg.SEQUENCE_FRAMES, found
        assert (found["frame_rate_num"], found["frame_rate_den"]) == cfg.SEQUENCE_RATE, found
        assert len(found["shots"]) == cfg.SEQUENCE_SHOT_COUNT, found["shots"]
        bounds = {shot["name"]: (shot["start"], shot["end"]) for shot in found["shots"]}
        for name, expected in cfg.SEQUENCE_SHOTS.items():
            assert bounds.get(name) == expected, f"Shot {name}: {bounds.get(name)}, expected {expected}"
        facts = found["preset_facts"]
        assert facts["can_png"] and facts["can_sound"], facts
        assert facts["custom_frame_rate"] in (None, list(cfg.SEQUENCE_RATE)), facts

    def _submit(self):
        width, height = cfg.SEQUENCE_SIZE
        body = dict(self._target(), shots=list(cfg.SEQUENCE_SHOTS), resolution_x=width, resolution_y=height,
                    frame_format="PNG", video_settings=dict(cfg.SEQUENCE_VIDEO), sound=True, merge_sound=True)
        version = self._post(f"{VERSIONS}/", body)
        assert version["status"] == "QUEUED", version
        assert (version["piece_seconds"], version["lead_in_ms"]) == (cfg.PIECE_SECONDS, cfg.LEAD_IN_MS), version
        assert version["pilot_frames"] == cfg.SEQUENCE_PILOT_FRAMES, version["pilot_frames"]
        assert version["pieces_total"] == len(cfg.SEQUENCE_PIECES), version
        shots = {shot["name"]: (shot["start_frame"], shot["end_frame"]) for shot in version["shots"]}
        assert shots == cfg.SEQUENCE_SHOTS, shots
        return version

    def _jobs(self, version_id):
        """``(pieces, audio jobs)`` of the version, in edit order."""
        rows = _rows(self._get(f"/api/jobs/?asset__project={self.project_id}"))
        mine = sorted((job for job in rows if job["sequence_submission"] == version_id),
                      key=lambda job: job["start_frame"])
        return ([job for job in mine if job["unreal_kind"] == "render"],
                [job for job in mine if job["unreal_kind"] == "audio"])

    def _assert_planned(self, pieces, audio):
        """FR-SP: three pieces, each with a lead-in, held for the pilot; one audio job for the run."""
        planned = [(job["start_frame"], job["end_frame"], job["unreal_lead_in_frames"]) for job in pieces]
        assert planned == cfg.SEQUENCE_PIECES, planned
        assert all(job["status"] == "QUEUED" for job in pieces), [job["status"] for job in pieces]
        for job in pieces:
            assert job["render_settings"].get("unreal.resolution") == list(cfg.SEQUENCE_SIZE), job["render_settings"]
        runs = [(job["start_frame"], job["end_frame"], job["unreal_lead_in_frames"]) for job in audio]
        assert runs == [cfg.SEQUENCE_AUDIO], runs
        return self._pending_pilot(held=len(pieces))["id"]

    def _assert_pieces(self, pieces):
        """Every piece rendered on the measured gate and kept only its own frames (lead-in deleted)."""
        for job in pieces:
            assert job["status"] == "DONE", f"Piece {job['name']}: {job['status']} {job.get('error_message')}"
            kept = job["end_frame"] - job["start_frame"] + 1
            expected = {"kind": "render", "vram_gate": "measured", "cache": "hit",
                        "lead_in_deleted": str(job["unreal_lead_in_frames"]), "output": f"{kept} PNG frames"}
            assert_summary(parse_summary(job["last_output"]), {**COMMON_SUMMARY, **expected}, job["name"])

    def _assert_sound(self, version, audio):
        """The audio job measured the offset near U-9's +9,418 samples and the cuts used it."""
        job = audio[0]
        assert job["status"] == "DONE", f"Audio job: {job['status']} {job.get('error_message')}"
        expected = {"kind": "audio", "vram_gate": "audio", "sound_offset": "measured"}
        assert_summary(parse_summary(job["last_output"]), {**COMMON_SUMMARY, **expected}, "Audio job")
        runs = version["sound_runs"]
        assert len(runs) == 1, runs
        run = runs[0]
        start, end, lead = cfg.SEQUENCE_AUDIO
        assert (run["start"], run["end"], run["r0"]) == (start, end + 1, start - lead), run
        offset = run["offset"]
        assert offset["status"] == "measured", offset
        assert abs(offset["offset_samples"] - cfg.SOUND_OFFSET_SAMPLES) <= cfg.SOUND_OFFSET_TOLERANCE, offset
        assert (run["applied_source"], run["applied_samples"]) == ("measured", offset["offset_samples"]), run
        assert not run["silent"] and not run["latency_compensation_skipped"], run
        expected_samples = (end - start + 1 + lead) * self._spf(run)
        assert abs(run["samples"] - expected_samples) <= cfg.WAV_LENGTH_TOLERANCE, (run["samples"], expected_samples)
        offset_warnings = [w for w in version["warnings"] if w.startswith(("Sound for shots", "Sound offset:"))]
        assert not offset_warnings, offset_warnings

    @staticmethod
    def _spf(run):
        num, den = cfg.SEQUENCE_RATE
        assert run["rate"] * den % num == 0, run["rate"]
        return run["rate"] * den // num

    def _assert_version(self, version, pieces):
        assert version["status"] == "DONE", (
            f"Version {version['status']}: {version.get('error_message')}; shots "
            f"{[(s['name'], s['status'], s['error_message']) for s in version['shots']]}")
        assert (version["shots_done"], version["pieces_done"]) == (len(cfg.SEQUENCE_SHOTS), len(pieces)), version
        items = ("video_status", "sound_status", "sound_video_status")
        for row in [version, *version["shots"]]:
            assert all(row[item] == "DONE" for item in items), {item: row[item] for item in items}
            assert all(row["downloads"].values()), row["downloads"]
        piece_ids = {job["id"] for job in pieces}
        for shot in version["shots"]:
            timings = shot["timings"]
            assert timings and {t["job"] for t in timings} <= piece_ids, timings
            assert sum(t["frames"] for t in timings) == shot["end_frame"] - shot["start_frame"], timings
            assert all(t["worker"] == self.worker_data["hostname"] and t["s_per_frame"] for t in timings), timings

    def _fetch(self, path, name):
        dest = self.temp_dir / name
        headers = media.download(self.session, f"{self.base_url}{path}", dest)
        assert "attachment" in headers.get("Content-Disposition", ""), headers
        return dest

    def _assert_item(self, base, label, frames, spf, ffmpeg):
        """The silent and merged videos, the sound and the frames zip of the version or one shot."""
        video = self._fetch(f"{base}download-video/", f"{label}.mp4")
        media.assert_video(ffmpeg, video, frames, cfg.SEQUENCE_RATE, "mp4")
        merged = self._fetch(f"{base}download-video/?sound=1", f"{label}_sound.mp4")
        media.assert_merged(ffmpeg, merged, frames, cfg.SEQUENCE_RATE)
        channels, width, rate, samples = media.wav_params(self._fetch(f"{base}download-sound/", f"{label}.wav"))
        assert width == 2 and channels >= 1, (channels, width)
        assert samples == frames * spf, f"{label}.wav has {samples} samples, expected {frames * spf}"
        return self._fetch(f"{base}download-frames/", f"{label}.zip")

    def _assert_downloads(self, version, ffmpeg):
        spf = self._spf(version["sound_runs"][0])
        root = f"{VERSIONS}/{version['id']}/"
        shots = {shot["index"]: shot for shot in version["shots"]}
        total = sum(shot["end_frame"] - shot["start_frame"] for shot in shots.values())
        whole = media.zip_frames(self._assert_item(root, "whole", total, spf, ffmpeg), whole=True)
        assert sorted(index for index, _slug in whole) == sorted(shots), whole.keys()
        for (index, _slug), frames in whole.items():
            shot = shots[index]
            assert frames == list(range(shot["start_frame"], shot["end_frame"])), f"Shot {shot['name']}: {frames}"
        for index, shot in shots.items():
            count = shot["end_frame"] - shot["start_frame"]
            archive = self._assert_item(f"{root}shots/{index}/", f"shot_{index}", count, spf, ffmpeg)
            frames = list(media.zip_frames(archive, whole=False).values())
            assert frames == [list(range(shot["start_frame"], shot["end_frame"]))], f"Shot {shot['name']} zip"
        return self.temp_dir / "whole.zip"

    def _keep_first_frames(self, archive, pieces):
        """U-6 by eye: the first kept frame of each piece (after its lead-in), saved and checked."""
        keep = Path(os.environ.get(KEEP_DIR_ENV) or Path(tempfile.gettempdir(), "sethlans-unreal-seq-e2e"))
        keep.mkdir(parents=True, exist_ok=True)
        for job in pieces:
            frame = job["start_frame"]
            data = media.zip_member(archive, frame)
            media.assert_png(data, cfg.SEQUENCE_SIZE, f"frame {frame}")
            (keep / f"piece_first_frame_{frame:07d}.png").write_bytes(data)
        logger.info("U-6: check the first kept frame of each piece for a double exposure in %s", keep)

    def test_sequence_version_end_to_end(self):
        """Discovery, plan, pilot, pieces, sound, videos and downloads of one small version."""
        ffmpeg = media.wait_for_ffmpeg(self.session, self.base_url)
        self._set_piece_length()
        watch = FarmNetworkWatch(self.worker_proc.pid)
        self._discover(watch)
        version = self._submit()
        pieces, audio = self._jobs(version["id"])
        pilot_id = self._assert_planned(pieces, audio)

        pilot = poll_sampling(self.session, f"{self.base_url}/api/jobs/{pilot_id}/", PILOT_TIMEOUT,
                              watch.sample, held_url=f"{self.base_url}/api/jobs/{pieces[0]['id']}/")
        # Before any check can fail: the pilot summary in the console.
        logger.info("Pilot %s summary:\n%s", pilot.get("status"), pilot.get("last_output"))
        self._assert_pilot(pilot, {"kind": "pilot", "vram_gate": "pilot",
                                   "pilot_frames": str(len(cfg.SEQUENCE_PILOT_FRAMES))})
        self._assert_measurement(pilot)

        version = poll_sampling(self.session, f"{self.base_url}{VERSIONS}/{version['id']}/", FINISH_TIMEOUT,
                                watch.sample, terminal=VERSION_FINAL)
        pieces, audio = self._jobs(version["id"])
        for job in [*audio, *pieces]:
            logger.info("%s %s summary:\n%s", job["name"], job["status"], job.get("last_output"))
        logger.info("Version %s warnings: %s; sound runs: %s", version["status"], version["warnings"],
                    version["sound_runs"])
        self._assert_pieces(pieces)
        self._assert_sound(version, audio)
        self._assert_version(version, pieces)
        self._keep_first_frames(self._assert_downloads(version, ffmpeg), pieces)
        self._assert_isolated()
        self._assert_no_farm_listeners(watch)
