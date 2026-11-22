# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Real render-per-card E2E on a machine with two graphics cards of one brand
(Unreal render per card, chunk 8, AC-E1, AC-E3, AC-E4, AC-E6, AC-E7, AC-E9).

One sequence version of two shots of the PoC sequence at 1280x720, PNG, no
sound and no videos, planned into three pieces. The shots job reads the
sequence; the pilot measures alone (slot 0, which its warm-up warms); then
the three pieces are released together. The first piece takes the first
card and slot 0 (warm: no warm-up); the second takes the other card and
slot 1 (its warm-up runs, from a cold start on a fresh cache) and the third
waits for a free card. That is how the overlap is forced: two cards free,
three pieces queued.

The pilot runs alone because the pieces are held until it has measured.
Beside another render a job is admitted only when its RAM and commit need
fit above the reserves (FR-PC-RAM-4, D9). A pair fits on a 64 GB machine
with a 32 GB page file. With a smaller page file the second piece may be
declined; the test then fails and quotes the worker's
``Skipping Unreal job`` lines.

Checks: the jobs' manager times and the cards' heartbeat holds overlap;
every Unreal job's ``card=`` is a ticked card, the card reported
"Rendering job N (Unreal)", UE chose that card's DXGI index, its own card
memory is known and other cards hold at most 256 MB (U-4); overlapping
pieces have different cards, slots and Zen ports; each log passes the
slot's Zen rules with no invalid-lock or zenserver shut-down line; every
editor and ShaderCompileWorker ran with its own slot's TEMP; the RAM
figures are stored (this machine's row in the project's measurement key,
and the worker's own ``unreal_machine_results`` entry for each piece's
card kind), and, when the cards showed no other job beside the pilot, the
pilot's farm tree never exceeded those figures; frames have the size and count;
one claim per job; isolation as found; no processes left.

Opt-in as the other real Unreal tests, plus two same-brand cards. GPU time
on a fresh cache, from a measured two-card run (a cold pilot's prep took
1.4 min and its warm-up 14.3 min): the shots job about 5 min; the pilot
about 19 min (prep, cold warm-up, 3 frames); then the pieces about 25 min,
set by piece B's cold slot-1 warm-up beside A (start-up is about 1.25 times
slower in a pair) plus its frames, while A and then C finish on the other
card. That is about 50 min in all; the hard cap is 100 min.
"""

import io
import logging
import time
import zipfile
from datetime import datetime
from itertools import combinations

import pytest
from PIL import Image

from tests.e2e import unreal_e2e_config as cfg
from tests.e2e import unreal_per_card_config as pcc
from tests.e2e import unreal_per_card_logs as ue_logs
from tests.e2e import unreal_sequence_media as media
from tests.e2e.log_capture import peek_log_files
from tests.e2e.unreal_e2e_checks import UnrealChecks, assert_summary
from tests.e2e.unreal_network_watch import FarmNetworkWatch
from tests.e2e.unreal_per_card_watch import EDITOR, PerCardWatch
from tests.e2e.unreal_workflow_harness import parse_summary, poll_sampling

logger = logging.getLogger(__name__)

_SKIP_REASON = pcc.skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")

VERSIONS = "/api/sequence-submissions"
VERSION_FINAL = {"DONE", "PARTLY_DONE", "ERROR", "CANCELED"}
# Every Unreal job of the run. ``compilation_cancelled`` is logged only: on
# the sample's deformer kernels cancel compiles even in a solo render.
FARM_SUMMARY = {"engine": cfg.ENGINE_BUILD, "user_state": "isolated", "user_cvars": "0", "zen_ddc_guard": "ok",
                "job_object": "ok", "survivors": "", "pristine_integrity": "ok", "user_profile_reset": "0"}
RAM_KEYS = ("ram_prep_ws_mb", "ram_prep_commit_mb", "ram_render_ws_mb", "ram_render_commit_mb")
DECLINE_MARK = "Skipping Unreal job"
_MB = 2 ** 20


def _int(value):
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _when(text):
    return datetime.fromisoformat(text).timestamp()


class TestUnrealRenderPerCard(UnrealChecks):
    """Three pieces of one version on two cards: two render at once, one per card."""

    @classmethod
    def _card_env(cls):
        from sethlans_worker_agent.cards.identity import key_address
        cards, reason = pcc.choose_cards()
        if reason:
            return None, reason
        cls.dxgi = {key_address(card.key): card.dxgi_index for card in cards}
        return pcc.card_env(cards), None

    @classmethod
    def _cache_dir(cls):
        return pcc.cache_dir()

    # -- requests ------------------------------------------------------------
    def _post(self, path, body, expected=(201,)):
        resp = self.session.post(f"{self.base_url}{path}", json=body, timeout=60)
        assert resp.status_code in expected, f"POST {path}: {resp.status_code} {resp.text}"
        return resp.json()

    def _remaining(self):
        left = self.deadline - time.monotonic()
        assert left > 0, f"The run passed its limit of {pcc.GPU_LIMIT_S // 60} minutes"
        return left

    def _target(self):
        return {"asset": self.asset_id, "map": cfg.RENDER_SETTINGS["unreal.map"],
                "sequence": cfg.RENDER_SETTINGS["unreal.sequence"], "preset": cfg.RENDER_SETTINGS["unreal.preset"]}

    def _pieces(self, version_id):
        data = self._get(f"/api/jobs/?asset__project={self.project_id}")
        rows = data["results"] if isinstance(data, dict) else data
        return sorted((job for job in rows if job["sequence_submission"] == version_id
                       and job["unreal_kind"] == "render"), key=lambda job: job["start_frame"])

    # -- plan ----------------------------------------------------------------
    def _submit_version(self, sample):
        """Shots job, then a version of three pieces held for the pilot: ``(version, pieces, pilot id)``."""
        body = {"unreal_piece_seconds": cfg.PIECE_SECONDS, "unreal_lead_in_ms": cfg.LEAD_IN_MS}
        resp = self.session.patch(f"{self.base_url}/api/projects/{self.project_id}/", json=body, timeout=30)
        assert resp.status_code == 200, f"Project settings: {resp.status_code} {resp.text}"
        record = self._post("/api/sequence-discoveries/", self._target(), expected=(200, 201))
        found = poll_sampling(self.session, f"{self.base_url}/api/sequence-discoveries/{record['id']}/",
                              self._remaining(), sample)
        assert found["status"] == "DONE", f"Discovery failed: {found.get('error_message')}"
        width, height = pcc.SIZE
        version = self._post(f"{VERSIONS}/", dict(self._target(), shots=list(cfg.SEQUENCE_SHOTS), resolution_x=width,
                                                  resolution_y=height, frame_format="PNG", video_settings=None,
                                                  sound=False, merge_sound=False))
        pieces = self._pieces(version["id"])
        planned = [(job["start_frame"], job["end_frame"], job["unreal_lead_in_frames"]) for job in pieces]
        assert planned == cfg.SEQUENCE_PIECES, planned
        return version, pieces, self._pending_pilot(held=len(pieces))["id"]

    # -- checks --------------------------------------------------------------
    def _check_pilot(self, pilot, watch):
        """AC-E6: measured, RAM figures stored; alone on the cards, the tree stayed within the figures."""
        summary = parse_summary(pilot["last_output"])
        assert pilot["status"] == "DONE", f"Pilot {pilot['status']}: {pilot.get('error_message')}"
        assert_summary(summary, {"kind": "pilot", "vram_gate": "pilot", "pilot_verdict": "fits"}, "Pilot")
        peak, csv_peak = int(summary["pilot_peak_mb"]), _int(summary.get("pilot_csv_peak_mb"))
        assert csv_peak and abs(peak - csv_peak) <= peak * 0.10, f"CSV peak {csv_peak} MB against {peak} MB"
        row = self._assert_measurement(pilot)
        ram = row["ram"]
        assert ram and all(isinstance(ram[p][k], int) and ram[p][k] >= 1 for p in ("prep", "render")
                           for k in ("ws_mb", "commit_mb")), row
        shared = watch.shared_while(pilot["id"])
        phases = [ram[p] for p in ("prep", "warmup", "render") if ram.get(p)]
        alone = watch.alone_peaks_mb(pilot["id"])
        logger.info("Pilot RAM %s; another job held a card meanwhile: %s (%s); farm tree sampled alone "
                    "(ws, commit MB): %s", ram, shared, watch.holds[-5:], alone)
        if alone is not None and not shared:
            assert alone[1] <= max(p["commit_mb"] for p in phases) * 1.02 + 64, (alone, ram)
            assert alone[0] <= max(p["ws_mb"] for p in phases) * 1.10 + 64, (alone, ram)
        return row

    def _declines(self):
        stdout, stderr = peek_log_files(self.worker_proc)
        return [line for line in (stdout + stderr).splitlines() if DECLINE_MARK in line][-12:]

    def _check_overlap(self, pieces, summaries, watch):
        """At least two pieces ran at once (manager times and card holds), each pair on its own card and slot."""
        spans = {job["id"]: (_when(job["started_at"]), _when(job["completed_at"])) for job in pieces}
        pairs = [(a, b) for a, b in combinations(pieces, 2)
                 if spans[a["id"]][0] < spans[b["id"]][1] and spans[b["id"]][0] < spans[a["id"]][1]]
        ids = {job["id"] for job in pieces}
        held = {pair for pair in watch.together() if set(pair) <= ids}
        logger.info("Piece spans %s; overlapping %s; held together %s", spans,
                    [(a["id"], b["id"]) for a, b in pairs], sorted(held))
        assert pairs and held, ("No two pieces ran at the same time. The worker's declines:\n"
                                + "\n".join(self._declines()))
        for a, b in pairs:
            sa, sb = summaries[a["id"]], summaries[b["id"]]
            assert sa["card"] != sb["card"] and sa["slot"] != sb["slot"], (a["name"], sa, b["name"], sb)
            seconds = min(spans[a["id"]][1], spans[b["id"]][1]) - max(spans[a["id"]][0], spans[b["id"]][0])
            logger.info("%s (card %s, slot %s) and %s (card %s, slot %s) overlapped %.0f s", a["name"],
                        sa["card"], sa["slot"], b["name"], sb["card"], sb["slot"], seconds)

    def _job_problems(self, job, summary, watch):
        """AC-E1, AC-E3, AC-E4 and AC-E9 for one Unreal job."""
        from sethlans_worker_agent.cards.identity import key_address
        name, card, slot = job["name"], summary.get("card", ""), _int(summary.get("slot"))
        dxgi = self.dxgi.get(key_address(card))
        if dxgi is None or slot is None:
            return [f"{name}: card {card!r} is not a ticked card or slot {summary.get('slot')!r} is unknown"]
        expected = dict(FARM_SUMMARY, zen=f"private:{pcc.BASE_ZEN_PORT + slot}")
        problems = [f"{name}: {k}={summary.get(k, '<missing>')!r}, expected {v!r}"
                    for k, v in expected.items() if summary.get(k) != v]
        if not watch.held(job["id"], card):
            problems.append(f"{name}: card {card} never reported 'Rendering job {job['id']} (Unreal)'")
        own, other = _int(summary.get("vram_job_peak_mb")), _int(summary.get("vram_job_other_cards_mb"))
        if not own or other is None or other > pcc.OTHER_CARDS_LIMIT_MB:
            problems.append(f"{name}: own card memory {own} MB, other cards {other} MB")
        problems.extend(f"{name}: {key} is not reported" for key in RAM_KEYS if not _int(summary.get(key)))
        logger.info("%s on %s slot %s (DXGI %s): own %s MB, other %s MB, compilation_cancelled=%s", name, card,
                    slot, dxgi, own, other, summary.get("compilation_cancelled"))
        return problems + ue_logs.check_job(self.cache_dir, job, summary, dxgi, pcc.BASE_ZEN_PORT + slot,
                                            self.wall_start)

    def _check_pieces(self, pieces, summaries, row):
        """Each piece kept its own frames, gated by this machine's result (measurement ``row``) with its RAM figures."""
        for job in pieces:
            kept = job["end_frame"] - job["start_frame"] + 1
            summary = summaries[job["id"]]
            expected = {"kind": "render", "vram_gate": "measured", "output": f"{kept} PNG frames",
                        "lead_in_deleted": str(job["unreal_lead_in_frames"]), "vram_card_kind": row["card_kind"]}
            assert_summary(summary, expected, job["name"])
            entry = self._machine_result(job["id"], row["card_kind"])
            assert summary.get("vram_measurement_id") == str(entry["measurement_id"]), (job["name"], entry, summary)
            assert entry["ram"] == row["ram"], (job["name"], entry["ram"], row)

    def _check_frames(self, version, pieces):
        """AC-E7: every frame of both shots, at the requested size; the first of each piece not black."""
        dest = self.temp_dir / "whole.zip"
        media.download(self.session, f"{self.base_url}{VERSIONS}/{version['id']}/download-frames/", dest)
        shots = {shot["index"]: shot for shot in version["shots"]}
        found = media.zip_frames(dest, whole=True)
        assert sorted(index for index, _slug in found) == sorted(shots), found.keys()
        for (index, _slug), frames in found.items():
            assert frames == list(range(shots[index]["start_frame"], shots[index]["end_frame"])), (index, frames)
        with zipfile.ZipFile(dest) as archive:
            sizes = {Image.open(io.BytesIO(archive.read(name))).size for name in archive.namelist()}
        assert sizes == {pcc.SIZE}, f"Frame sizes {sizes}, expected {pcc.SIZE}"
        for job in pieces:
            media.assert_png(media.zip_member(dest, job["start_frame"]), pcc.SIZE, f"frame {job['start_frame']}")

    @staticmethod
    def _check_slot_temps(watch, slots):
        """AC-E4: every editor and ShaderCompileWorker sampled ran with its own slot's TEMP and TMP."""
        logger.info("Own-slot TEMP seen: %s; unreadable: %s", dict(watch.temp_ok), dict(watch.unreadable))
        assert not watch.temp_problems, "\n".join(watch.temp_problems[:20])
        missing = [slot for slot in slots if not watch.temp_ok[(slot, EDITOR)]]
        assert not missing, f"No editor of slot(s) {missing} was seen with its own TEMP"

    def test_two_renders_one_per_card(self):
        """Pilot alone, then pieces side by side on both cards, each in its own slot."""
        started, self.wall_start = time.monotonic(), time.time()
        self.deadline = started + pcc.GPU_LIMIT_S
        net = FarmNetworkWatch(self.worker_proc.pid)
        watch = PerCardWatch(self.session, self.base_url, self.worker_data["hostname"], self.worker_proc.pid,
                             self.cache_dir, slots=len(self.dxgi))

        def sample():
            net.sample()
            watch.sample()

        version, pieces, pilot_id = self._submit_version(sample)
        pilot = poll_sampling(self.session, f"{self.base_url}/api/jobs/{pilot_id}/", self._remaining(), sample,
                              held_url=f"{self.base_url}/api/jobs/{pieces[0]['id']}/")
        logger.info("Pilot %s summary:\n%s", pilot.get("status"), pilot.get("last_output"))
        row = self._check_pilot(pilot, watch)
        version = poll_sampling(self.session, f"{self.base_url}{VERSIONS}/{version['id']}/", self._remaining(),
                                sample, terminal=VERSION_FINAL)
        logger.info("Version %s after %.1f min", version["status"], (time.monotonic() - started) / 60)
        pieces = self._pieces(version["id"])
        for job in pieces:
            logger.info("%s %s summary:\n%s", job["name"], job["status"], job.get("last_output"))
        assert version["status"] == "DONE", (version["status"], version.get("error_message"),
                                             [(job["name"], job["status"], job["error_message"]) for job in pieces])
        summaries = {job["id"]: parse_summary(job["last_output"]) for job in pieces}
        self._check_overlap(pieces, summaries, watch)
        problems = self._job_problems(pilot, parse_summary(pilot["last_output"]), watch)
        for job in pieces:
            problems += self._job_problems(job, summaries[job["id"]], watch)
        assert not problems, "\n".join(problems)
        self._check_pieces(pieces, summaries, row)
        self._check_frames(version, pieces)
        self._check_slot_temps(watch, sorted({int(s["slot"]) for s in summaries.values()}))
        self._assert_isolated()
        self._assert_no_farm_listeners(net)
        logger.info("Render-per-card run passed in %.1f min", (time.monotonic() - started) / 60)
