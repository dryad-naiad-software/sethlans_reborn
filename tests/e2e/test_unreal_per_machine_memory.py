# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Real per-machine memory test E2E, Part A: one machine with two Unreal cards
of one card kind (Unreal per-machine memory test chunk 9, AC-E1..AC-E6,
AC-E9; U-1, U-2, U-4).

One manager and one worker for the class, both cards ticked. Each test
method runs alone too: the shots job and the AC-E1 version are made once
per class, on first use. Methods, in the order they run:

- ``test_e1_e2_e9_own_test_one_result_two_cards``: version V1, shots 0055
  and 0060 at 640x360 with sound, videos and merged copies, two pieces.
  AC-E1: exactly one pilot, targeted at this worker, of the cards' one kind
  (``SETHLANS_UNREAL_E2E_PM_EXPECT_KIND`` also checked when set); its
  record's driver equals the ``internal:`` value of the chosen adapter in
  the pilot's UE render log, not ``Unknown`` (U-2); no job mentions the
  removed 12,478 MB minimum. AC-E2: the pieces are released together after
  the pilot (job pause), so they render at once on the two cards, each
  gated by the one result with ``vram_need_mb`` = its peak; the sound job,
  released after them, is gated by the same result; every piece reports
  ``spilled=0`` and the pilot's driver. AC-E9: frames, videos, sound offset.
- ``test_e3_coverage_subset_needs_no_pilot``: V3, shot 0060 alone at the
  same size: no pilot, a covered row, the piece gated by the derived record.
- ``test_e6_draft_covered_by_full``: V6, V1's shots as a draft (screen
  percentage 50 only, which leaves the warm-up as it is): no pilot; both
  pieces render at once on V1's warm slots, so each job peak comes from the
  render launch (``vram_job_peak_phase=render``); every piece's reported
  ``job_peak_mb`` is at most the full-settings result peak (U-4).
- ``test_e4_e5_fits_nowhere_then_self_overflow`` (last: it may reset the
  graphics driver): V4, 12 frames at the over size: the pilot ends DONE
  ``does_not_fit`` (saturated or not is recorded) and the version stops
  with the FR-MT-N-2 message naming the card and its lower bound. Then the
  test sets that record to fits with half the card as its peak and, as its
  spill reference, V1's shared peak plus frame read-back for the extra
  pixels (never the over-size pilot's own shared peak, which holds the
  overflow; ``SETHLANS_UNREAL_E2E_PM_E5_SHARED_REF_MB`` overrides it). That
  is the one DB write. V5, the same key, renders its piece with no pilot:
  its summary's ``spill_reference_mb`` is the value written; spilled and
  cause are recorded (self expected, or the piece fails, U-1); the record
  must end ``does_not_fit``, by ``spill`` when the piece spilled.

E3, E6 and E4/E5 each end with the isolation check (no Unreal process left,
the user's Unreal state as found), as E1 does.

Every observation (pilot and job summary facts: ``pilot_others_rise_mb``,
``spill_shared_peak_mb``, ``vram_job_peak_phase``, the driver lines, the
record fields) is appended to the result file (``unreal_pm_records.record``).

Opt-in: ``SETHLANS_UNREAL_E2E=1``, ``SETHLANS_UNREAL_E2E_SHARE``,
``SETHLANS_UNREAL_E2E_PM=1`` and two cards of one kind; the other env vars
are in ``unreal_pm_config``. GPU time on a fresh cache: V1 about 40 min (a
cold pilot about 19 min, then piece B's cold slot-1 warm-up about 15 min
beside piece A, then the sound job), V3 about 3 min, V6 about 5 min (two
warm pieces at once), V4 about 5 min and V5 up to 45 min (stopped by the
render timeout if it crawls); about 60-100 min in all. E3, E6 and (without
the reference override) E4/E5 run V1 first when started alone.
"""

import logging
import os

import pytest

from tests.e2e import unreal_e2e_config as cfg
from tests.e2e import unreal_pm_config as pm
from tests.e2e import unreal_pm_outputs as outputs
from tests.e2e import unreal_pm_records as rec
from tests.e2e import unreal_sequence_media as media
from tests.e2e.unreal_network_watch import FarmNetworkWatch
from tests.e2e.unreal_pm_flow import PILOT_TIMEOUT, MachineTestFlow
from tests.e2e.unreal_workflow_harness import parse_summary

logger = logging.getLogger(__name__)

_SKIP_REASON = pm.part_a_skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")

SHOT_A, SHOT_B = list(cfg.SEQUENCE_SHOTS)


class TestUnrealPerMachineMemory(MachineTestFlow):
    """Own test, one result for two cards, coverage, draft, fits nowhere and self-overflow on one machine."""

    piece_seconds = pm.PIECE_SECONDS_A
    _full = None
    net = None

    @classmethod
    def _card_env(cls):
        cards, reason = pm.choose_one_kind(2)
        return (None, reason) if reason else (pm.worker_env(cards), None)

    @classmethod
    def _cache_dir(cls):
        return pm.cache_dir()

    def _sample(self):
        if type(self).net is None:
            type(self).net = FarmNetworkWatch(self.worker_proc.pid)
        type(self).net.sample()

    def _kind(self):
        """The one kind of this worker's ticked cards (from the API, never from a card's name)."""
        kinds = self.ticked_kinds(self._worker(self.worker_data["hostname"]))
        assert len(kinds) == 1, f"Expected the ticked cards to be of one kind: {kinds}"
        kind = next(iter(kinds))
        expected = os.environ.get(pm.EXPECT_KIND_ENV, "").strip().lower()
        assert not expected or kind.startswith(expected), f"Card kind {kind} does not start with {expected}"
        return kind

    def _one_pilot(self, before, kind, label):
        """Exactly one new pilot, targeted at this worker, of ``kind`` (D4, D5)."""
        pilots = self._new_pilots(before)
        assert len(pilots) == 1, f"{label}: expected one pilot, got {[(p['id'], p['status']) for p in pilots]}"
        pilot = pilots[0]
        assert pilot["render_settings"].get("unreal.card_kind") == kind, (label, pilot["render_settings"])
        target = rec.pilot_target(self.db_path, pilot["id"])
        assert target == self.worker_data["id"], f"{label}: pilot targets worker {target}, not {self.worker_data['id']}"
        return pilot

    def _pilot_record(self, pilot):
        return rec.pilot_record(self.db_path, pilot["id"])

    # -- V1 (AC-E1, AC-E2, AC-E9), made once per class ----------------------------------------------------
    def _full_result(self):
        cls = type(self)
        if cls._full is not None:
            return cls._full
        kind, before = self._kind(), self._pilot_ids()
        version = self._submit(pm.SMALL_SIZE, shots=[SHOT_A, SHOT_B], sound=True, video=dict(cfg.SEQUENCE_VIDEO),
                               merge=True)
        pieces, audio = self._version_jobs(version["id"])
        planned = [(job["start_frame"], job["end_frame"], job["unreal_lead_in_frames"]) for job in pieces]
        assert planned == pm.PIECES_A and len(audio) == 1, (planned, audio)
        for job in [*pieces, *audio]:
            self._pause(job["id"])  # held for the pilot anyway; released below in a fixed order
        pilot = self._one_pilot(before, kind, "V1")
        pilot = self._wait_job(pilot["id"], PILOT_TIMEOUT, held_url=f"{self.base_url}/api/jobs/{pieces[0]['id']}/")
        logger.info("V1 pilot %s summary:\n%s", pilot["status"], pilot.get("last_output"))
        assert pilot["status"] == "DONE", f"V1 pilot {pilot['status']}: {pilot.get('error_message')}"
        row = self._wait_row_state(version["id"], self.worker_data["hostname"], {"fits", "does_not_fit"})
        assert row["state"] == "fits" and not row["covered"], row
        for job in pieces:
            self._unpause(job["id"])
        self._wait_started([job["id"] for job in pieces], 1800)
        self._unpause(audio[0]["id"])
        version = self._wait_version(version["id"])
        pieces, audio = self._version_jobs(version["id"])
        cls._full = {"kind": kind, "pilot": pilot, "row": row, "record": self._pilot_record(pilot),
                     "version": version, "pieces": pieces, "audio": audio}
        return cls._full

    def _check_driver(self, pilot, record, row):
        """AC-E1, U-2: the record's driver is the chosen adapter's internal: value in the pilot's render log."""
        summary = parse_summary(pilot["last_output"])
        logs = rec.render_logs(self.cache_dir, pilot["id"], summary.get("attempt", ""))
        assert logs, f"No render log of pilot {pilot['id']} in {self.cache_dir}"
        driver, lines = rec.driver_lines(logs[-1])
        rec.record("AC-E1 driver lines", pilot=pilot["id"], log=logs[-1].name, chosen_driver=driver, lines=lines)
        assert driver and driver != "Unknown", f"No known driver for the chosen adapter in {logs[-1]}: {lines}"
        assert record["driver_version"] == driver == row["driver_version"] == summary.get("driver_version"), (
            driver, record["driver_version"], row["driver_version"], summary.get("driver_version"))
        return driver

    def test_e1_e2_e9_own_test_one_result_two_cards(self):
        full = self._full_result()
        pilot, row, record, kind = full["pilot"], full["row"], full["record"], full["kind"]
        self._record_job("AC-E1 pilot", pilot, record=record)
        summary = parse_summary(pilot["last_output"])
        assert summary.get("pilot_verdict") == "fits" and summary.get("vram_need_mb") == "", summary
        driver = self._check_driver(pilot, record, row)
        self._assert_no_old_minimum()
        version, pieces, audio = full["version"], full["pieces"], full["audio"]
        final = self.machine_row(version, self.worker_data["hostname"], kind)
        cards = set()
        for job in pieces:
            self._record_job("AC-E2 piece", job)
            facts = parse_summary(job["last_output"])
            assert job["status"] == "DONE", f"{job['name']}: {job['status']} {job.get('error_message')}"
            assert (facts.get("vram_gate"), facts.get("vram_card_kind")) == ("measured", kind), facts
            assert facts.get("vram_measurement_id") == str(record["id"]), (job["name"], record["id"], facts)
            assert int(facts["vram_need_mb"]) == row["peak_mb"], (job["name"], row["peak_mb"], facts)
            assert facts.get("spilled") == "0", f"{job['name']}: spilled={facts.get('spilled')!r} (U-1, U-3)"
            assert facts.get("driver_version") == driver and facts.get("memory_report") == "sent", facts
            cards.add(facts.get("card"))
        declines = [line for line in self._worker_log().splitlines() if "Skipping Unreal job" in line][-8:]
        assert len(cards) == len(pieces) == 2, f"The pieces did not render on different cards: {cards}; {declines}"
        self._record_job("AC-E2 sound job", audio[0])
        facts = parse_summary(audio[0]["last_output"])
        assert (facts.get("vram_gate"), facts.get("vram_measurement_id")) == ("audio", str(record["id"])), facts
        assert row["peak_mb"] <= int(facts["vram_need_mb"]) <= final["peak_mb"], (row, final, facts)
        rec.record("AC-E2 result", row_after_pilot=row, row_after_version=final)
        assert version["status"] == "DONE", (version["status"], version.get("error_message"))
        outputs.check_frames(self, version, pieces, pm.SMALL_SIZE, "V1")
        outputs.check_sound_and_videos(self, version, audio, media.wait_for_ffmpeg(self.session, self.base_url), "V1")
        self._assert_isolated()
        self._assert_no_farm_listeners(type(self).net)

    def test_e3_coverage_subset_needs_no_pilot(self):
        full = self._full_result()
        before = self._pilot_ids()
        version = self._submit(pm.SMALL_SIZE, shots=[SHOT_B])
        assert not self._new_pilots(before), "A subset of a measured version created a pilot"
        row = self.machine_row(self._version(version["id"]), self.worker_data["hostname"], full["kind"])
        assert row["state"] == "fits" and row["covered"], row
        version = self._wait_version(version["id"])
        pieces, _audio = self._version_jobs(version["id"])
        derived = rec.key_records(self.db_path, pieces[0]["id"])
        rec.record("AC-E3 derived record", records=derived)
        assert len(derived) == 1 and derived[0]["covered_by_id"] == full["record"]["id"], derived
        assert derived[0]["verdict_source"] == "covered", derived
        for job in pieces:
            self._record_job("AC-E3 piece", job)
            assert job["status"] == "DONE", f"{job['name']}: {job['status']} {job.get('error_message')}"
            facts = parse_summary(job["last_output"])
            assert facts.get("vram_measurement_id") == str(derived[0]["id"]), (derived, facts)
        assert not self._new_pilots(before), "A pilot ran for the covered version"
        assert version["status"] == "DONE", (version["status"], version.get("error_message"))
        outputs.check_frames(self, version, pieces, pm.SMALL_SIZE, "V3")
        self._assert_isolated()

    def test_e6_draft_covered_by_full(self):
        full = self._full_result()
        current = rec.record_by_id(self.db_path, full["record"]["id"])
        full_peak = max(current["peak_mb"], current["job_peak_max_mb"] or 0)
        before = self._pilot_ids()
        version = self._submit(pm.SMALL_SIZE, shots=[SHOT_A, SHOT_B], draft=pm.DRAFT)
        assert not self._new_pilots(before), "A draft of measured shots created a pilot"
        row = self.machine_row(self._version(version["id"]), self.worker_data["hostname"], full["kind"])
        assert row["state"] == "fits" and row["covered"], row
        version = self._wait_version(version["id"])
        pieces, _audio = self._version_jobs(version["id"])
        reports = rec.memory_reports(self._worker_log())
        for job in pieces:
            phase, report = reports.get(job["id"], (None, {}))
            self._record_job("AC-E6 draft piece", job, report=report, report_phase=phase, full_peak_mb=full_peak)
            assert job["status"] == "DONE", f"{job['name']}: {job['status']} {job.get('error_message')}"
            assert report.get("job_peak_mb"), f"{job['name']}: no reported job_peak_mb in the worker log"
            phase_key = parse_summary(job["last_output"]).get("vram_job_peak_phase")
            assert phase_key == "render", f"{job['name']}: job peak from the {phase_key!r} launch (warm slot expected)"
            assert report["job_peak_mb"] <= full_peak, (
                f"U-4: draft {job['name']} peaked at {report['job_peak_mb']} MB ({phase}) above the full "
                f"result's {full_peak} MB")
        assert not self._new_pilots(before), "A pilot ran for the draft version"
        outputs.check_frames(self, version, pieces, pm.SMALL_SIZE, "V6")
        self._assert_isolated()

    def _e5_reference(self, size):
        """AC-E5's spill reference: the override, else V1's shared peak plus read-back for the extra pixels (B1)."""
        text = os.environ.get(pm.E5_SHARED_REF_ENV, "").strip()
        if text:
            assert text.isdigit(), f"{pm.E5_SHARED_REF_ENV} must be a whole number of MB, got {text!r}"
            return int(text)
        base = rec.record_by_id(self.db_path, self._full_result()["record"]["id"])["shared_peak_mb"]
        assert base is not None, f"V1's result has no shared_peak_mb (no shared counter, U-3); set {pm.E5_SHARED_REF_ENV}"
        extra_mpx = (size[0] * size[1] - pm.SMALL_SIZE[0] * pm.SMALL_SIZE[1]) / 1e6
        return base + round(pm.READBACK_MB_PER_MPX * extra_mpx)

    def test_e4_e5_fits_nowhere_then_self_overflow(self):
        size = pm.over_size()
        reference = self._e5_reference(size)  # before V4, so V1 (when needed) never runs between E4 and E5
        kind, before = self._kind(), self._pilot_ids()
        version = self._submit(size, frames=pm.OVER_FRAMES)
        pilot = self._wait_job(self._one_pilot(before, kind, "V4")["id"], PILOT_TIMEOUT)
        logger.info("V4 pilot %s summary:\n%s", pilot["status"], pilot.get("last_output"))
        record = self._pilot_record(pilot)
        self._record_job("AC-E4 pilot (over size)", pilot, record=record, size=size)
        summary = parse_summary(pilot["last_output"])
        assert pilot["status"] == "DONE" and summary.get("pilot_verdict") == "does_not_fit", (pilot["status"], summary)
        if summary.get("pilot_saturated") != "1":
            logger.warning("AC-E4: the pilot is does_not_fit without a saturated sample (peak + baseline rule)")
        row = self._stopped_version(version["id"], "AC-E4")
        # AC-E5: the test sets the result peak so the gate admits a piece of the same key, and the spill
        # reference to the small size's shared peak plus read-back (never the over-size pilot's own, B1).
        rec.admit_record(self.db_path, record["id"], row["card_total_mb"] // 2, reference)
        rec.record("AC-E5 admitted record", before=record, after=rec.record_by_id(self.db_path, record["id"]))
        before = self._pilot_ids()
        version = self._wait_version(self._submit(size, frames=pm.OVER_FRAMES)["id"], 3600)
        pieces, _audio = self._version_jobs(version["id"])
        after = rec.record_by_id(self.db_path, record["id"])
        reports = rec.memory_reports(self._worker_log())
        for job in pieces:
            self._record_job("AC-E5 piece (over size)", job, report=reports.get(job["id"]), record_after=after,
                             version_status=version["status"], reference_written=reference)
        first, piece = parse_summary(pieces[0]["last_output"]), pieces[0]
        assert not self._new_pilots(before), "The admitted key created a pilot"
        assert after["verdict"] == "does_not_fit", f"AC-E5: the result stayed {after}"
        if piece["status"] == "DONE" or first.get("spill_reference_mb"):
            assert first.get("spill_reference_mb") == str(reference), (reference, rec.summary_facts(first))
        if (first.get("spilled"), first.get("spill_cause")) == ("1", "self"):  # contention never marks (D9)
            assert after["verdict_source"] == "spill", f"AC-E5: a spill marked the result by {after['verdict_source']}"
        assert piece["status"] == "ERROR" or (first.get("spilled"), first.get("spill_cause")) == ("1", "self"), (
            f"U-1: the over-size piece finished without a self spill: {rec.summary_facts(first)}")
        self._assert_isolated()
