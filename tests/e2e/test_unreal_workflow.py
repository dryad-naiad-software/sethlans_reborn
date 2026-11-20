# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Real Unreal render E2E on a UE 5.8 node (Unreal foundation FR-T-8).

Exercises the whole Unreal pipeline against a real manager, a real worker
and a real UE 5.8 install: a single-frame job on the PoC project
(``ACOMAnimationSample``, frame 1000) first makes the manager create the
project's pilot (FR-PM); the normal job is held QUEUED until the pilot
records a GPU memory measurement, then renders to DONE.

The output is verified the way the Blender E2E verifies its renders
(``verify_image_output``: a valid, not solid-black image) plus the
expected resolution; there are no reference images.

Opt-in: set ``SETHLANS_UNREAL_E2E=1`` and ``SETHLANS_UNREAL_E2E_SHARE``
on a Windows node with a graphics card, UE 5.8.4 and the Sethlans share.
Elsewhere the module is skipped
before any fixture runs. The constants and gating live in
``tests/e2e/unreal_e2e_config.py``, the manager/worker harness in
``tests/e2e/unreal_workflow_harness.py``, the isolation checks in
``tests/e2e/unreal_isolation.py`` and the firewall-prompt check in
``tests/e2e/unreal_network_watch.py``.
"""

import logging

import pytest

from tests.e2e import unreal_isolation
from tests.e2e.helpers import verify_image_output
from tests.e2e.unreal_network_watch import FarmNetworkWatch
from tests.e2e.unreal_e2e_config import (
    ENGINE_BUILD,
    FRAME,
    MIN_PILOT_PEAK_MB,
    RENDER_SETTINGS,
    archive_on_share,
    peak_upper_bound_mb,
    unreal_skip_reason,
)
from tests.e2e.unreal_workflow_harness import (
    UnrealWorkflowHarness,
    parse_summary,
    poll_sampling,
)

logger = logging.getLogger(__name__)

_SKIP_REASON = unreal_skip_reason()
pytestmark = pytest.mark.skipif(
    _SKIP_REASON is not None, reason=_SKIP_REASON or "",
)

# FR-T-8: the pilot is DONE within 120 min (a cold pilot took ~23 min on
# the dev machine: copy + extract, prep, warm-up ~13 min, 23 frames ~8.5 min).
PILOT_TIMEOUT = 7200
# FR-T-8: the normal job is DONE within 120 min after the pilot (a warm
# render took ~70 s on the dev machine).
RENDER_TIMEOUT = 7200
OUTPUT_SIZE = (1920, 1080)
PENDING_NOTE = "Waiting for this project's memory measurement (pilot render)."

# Summary keys every Unreal job of this test reports (FR-UE-14, FR-T-8).
COMMON_SUMMARY = {
    "engine": ENGINE_BUILD,
    "exclusive": "1",
    "user_state": "isolated",
    "user_cvars": "0",
    "zen": "private:8668",
    "zen_ddc_guard": "ok",
    "compilation_cancelled": "0",
    "job_object": "ok",
    "survivors": "",
    "pristine_integrity": "ok",
}
PILOT_SUMMARY = {
    "kind": "pilot",
    "vram_gate": "pilot",
    "pilot_frames": "23",
    "warmup": "ran",
}
RENDER_SUMMARY = {
    "kind": "render",
    "vram_gate": "measured",
    "cache": "hit",
    "warmup": "skipped",
    "cvars_applied": "4/4",
    "cvar_overridden": "",
}


def _assert_summary(summary, expected, what):
    wrong = {
        key: summary.get(key, "<missing>")
        for key, value in expected.items() if summary.get(key) != value
    }
    assert not wrong, f"{what} summary differs from {expected}: {wrong}"


class TestUnrealWorkflow(UnrealWorkflowHarness):
    """The PoC still: pilot first, then the held job renders."""

    def _get(self, path):
        resp = self.session.get(f"{self.base_url}{path}", timeout=30)
        assert resp.status_code == 200, f"GET {path}: {resp.status_code} {resp.text}"
        return resp.json()

    def _submit_job(self):
        resp = self.session.post(
            f"{self.base_url}/api/jobs/",
            json={
                "name": "E2E Unreal PoC still",
                "asset_id": self.asset_id,
                "output_file_pattern": "renders/unreal_poc_####.png",
                "start_frame": FRAME,
                "end_frame": FRAME,
                "render_settings": RENDER_SETTINGS,
            },
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Job creation failed: {resp.status_code} {resp.text}"
        )
        return resp.json()["id"]

    def _assert_held_for_pilot(self, job_id):
        """Creating the job created the pilot; the job is held for it."""
        job = self._get(f"/api/jobs/{job_id}/")
        assert job["status"] == "QUEUED", job
        assert job["unreal_measured_peak_mb"] is None, job
        assert job["routing_note"] == PENDING_NOTE, job["routing_note"]
        measurement = self._get(f"/api/projects/{self.project_id}/")["unreal_measurement"]
        latest = measurement["latest"]
        assert measurement["held_pending"] == 1, measurement
        assert latest["status"] == "pending", latest
        assert latest["pilot_job_id"], latest
        return latest["pilot_job_id"]

    def _assert_pilot(self, pilot):
        assert pilot["status"] == "DONE", (
            f"Pilot did not complete. Status: {pilot['status']}, "
            f"Error: {pilot.get('error_message', 'N/A')}\n{pilot.get('last_output')}"
        )
        assert pilot["unreal_kind"] == "pilot", pilot
        summary = parse_summary(pilot["last_output"])
        _assert_summary(summary, {**COMMON_SUMMARY, **PILOT_SUMMARY}, "Pilot")
        peak, csv_peak = int(summary["pilot_peak_mb"]), summary["pilot_csv_peak_mb"]
        assert csv_peak, "Pilot has no CSV peak"
        assert abs(peak - int(csv_peak)) <= peak * 0.10, (
            f"CSV peak {csv_peak} MB is not within 10% of the peak {peak} MB"
        )

    def _card_total_mb(self, latest):
        """The measured card's total memory from this worker's heartbeat reading."""
        hostname = self.worker_data["hostname"]
        workers = [w for w in self._get("/api/heartbeat/") if w.get("hostname") == hostname]
        gpus = (workers[0].get("gpu_memory") or {}).get("gpus") if workers else None
        logger.info("Measured card %s; worker GPU reading: %s", latest["gpu_name"], gpus)
        return peak_upper_bound_mb(gpus, latest["gpu_name"], latest["gpu_total_mb"])

    def _assert_measurement(self, pilot_id):
        """The project's measurement is recorded and plausible; nothing is held."""
        measurement = self._get(f"/api/projects/{self.project_id}/")["unreal_measurement"]
        latest = measurement["latest"]
        assert latest["status"] == "measured", latest
        assert latest["pilot_job_id"] == pilot_id, latest
        # The peak varies by card and driver: at least a working measurement,
        # at most the measured card's own memory (the worker's reading).
        card_total = self._card_total_mb(latest)
        assert card_total, f"No total memory for the measured card: {latest}"
        assert MIN_PILOT_PEAK_MB <= latest["peak_mb"] <= card_total, (
            f"Peak {latest['peak_mb']} MB is outside {MIN_PILOT_PEAK_MB}..{card_total} MB: {latest}"
        )
        assert latest["engine_build"] == ENGINE_BUILD, latest
        assert latest["worker_hostname"] == self.worker_data["hostname"], latest
        assert latest["prep_s"] is not None, latest
        assert latest["seconds_per_frame"] is not None, latest
        held = {k: measurement[k] for k in ("held_pending", "held_failed", "held_none")}
        assert held == {"held_pending": 0, "held_failed": 0, "held_none": 0}, held
        return latest["peak_mb"]

    def _assert_render(self, result, peak):
        assert result["status"] == "DONE", (
            f"Job did not complete successfully. Status: {result['status']}, "
            f"Error: {result.get('error_message', 'N/A')}\n{result.get('last_output')}"
        )
        assert result["unreal_measured_peak_mb"] == peak, result
        summary = parse_summary(result["last_output"])
        _assert_summary(summary, {**COMMON_SUMMARY, **RENDER_SUMMARY}, "Job")
        assert int(summary["vram_measured_peak_mb"]) == peak, summary
        assert int(summary["vram_need_mb"]) <= int(summary["vram_usable_mb"]), summary
        assert summary.get("vram_peak_mb"), summary
        assert "log_flood" not in summary, summary

        output_file = result.get("output_file")
        assert output_file, "Job completed but has no output_file"
        img = verify_image_output(self.session, output_file, self.base_url)
        assert img.size == OUTPUT_SIZE, f"Output is {img.size}, expected {OUTPUT_SIZE}"

    def _assert_isolated(self):
        orphans = unreal_isolation.orphans_of(self.cache_dir)
        assert not orphans, f"Unreal processes survived the jobs: {orphans}"
        after = unreal_isolation.take_snapshot(
            self.temp_dir / "hkcu_after.reg", archive_on_share(),
        )
        problems = unreal_isolation.snapshot_differences(self.isolation_before, after)
        assert not problems, "Per-user state changed:\n" + "\n".join(problems)

    @staticmethod
    def _assert_no_farm_listeners(watch):
        """No farm process listened off loopback (but #233's) and no trace server ran."""
        logger.info("Allowed TraceLog control listeners: %s", sorted(watch.allowed))
        logger.info("Bound, not listening: %s", sorted(watch.not_listening))
        assert {"unrealeditor-cmd.exe", "zenserver.exe"} <= watch.seen, (
            f"The samples never saw UE and its Zen server running: {sorted(watch.seen)}"
        )
        assert not watch.problems, (
            "Farm processes would trigger a Windows Firewall prompt:\n"
            + "\n".join(sorted(watch.problems))
        )

    def test_poc_still_after_pilot(self):
        """Pilot first, then the held single-frame job renders a 1920x1080 PNG."""
        job_id = self._submit_job()
        pilot_id = self._assert_held_for_pilot(job_id)
        job_url = f"{self.base_url}/api/jobs/{job_id}/"
        watch = FarmNetworkWatch(self.worker_proc.pid)

        pilot = poll_sampling(
            self.session, f"{self.base_url}/api/jobs/{pilot_id}/",
            PILOT_TIMEOUT, watch.sample, held_url=job_url,
        )
        # Before any check can fail: the pilot summary in the console.
        logger.info("Pilot %s summary:\n%s", pilot.get("status"), pilot.get("last_output"))
        self._assert_pilot(pilot)
        peak = self._assert_measurement(pilot_id)

        result = poll_sampling(self.session, job_url, RENDER_TIMEOUT, watch.sample)
        self._assert_render(result, peak)
        self._assert_isolated()
        self._assert_no_farm_listeners(watch)
