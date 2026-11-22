# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Real Unreal render E2E on a UE 5.8 node (Unreal foundation FR-T-8).

Exercises the whole Unreal pipeline against a real manager, a real worker
and a real UE 5.8 install: a single-frame job on the PoC project
(``ACOMAnimationSample``, frame 1000) first makes the manager create the
project's pilot for this machine's card kind (FR-PM; per-machine memory
test D4, D5); the normal job is held QUEUED until the pilot records a
memory result that fits, then renders to DONE, gated by that result (D7).

The output is verified the way the Blender E2E verifies its renders
(``verify_image_output``: a valid, not solid-black image) plus the
expected resolution; there are no reference images.

Opt-in: set ``SETHLANS_UNREAL_E2E=1`` and ``SETHLANS_UNREAL_E2E_SHARE``
on a Windows node with a graphics card, UE 5.8.4 and the Sethlans share.
Elsewhere the module is skipped
before any fixture runs. The constants and gating live in
``tests/e2e/unreal_e2e_config.py``, the manager/worker harness in
``tests/e2e/unreal_workflow_harness.py``, the shared checks in
``tests/e2e/unreal_e2e_checks.py``, the isolation checks in
``tests/e2e/unreal_isolation.py`` and the firewall-prompt check in
``tests/e2e/unreal_network_watch.py``.
"""

import logging

import pytest

from tests.e2e.helpers import verify_image_output
from tests.e2e.unreal_e2e_checks import COMMON_SUMMARY, UnrealChecks, assert_summary
from tests.e2e.unreal_network_watch import FarmNetworkWatch
from tests.e2e.unreal_e2e_config import FRAME, RENDER_SETTINGS, unreal_skip_reason
from tests.e2e.unreal_workflow_harness import parse_summary, poll_sampling

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

PILOT_SUMMARY = {
    "kind": "pilot",
    "vram_gate": "pilot",
    "pilot_frames": "23",
    "warmup": "ran",
    "pilot_verdict": "fits",
}
RENDER_SUMMARY = {
    "kind": "render",
    "vram_gate": "measured",
    "cache": "hit",
    "warmup": "skipped",
    "cvars_applied": "4/4",
    "cvar_overridden": "",
}


class TestUnrealWorkflow(UnrealChecks):
    """The PoC still: pilot first, then the held job renders."""

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
        assert job["unreal_machine_results"] is None, job  # worker-token requests only (FR-MT-T-3)
        # The worker has no memory result of its own for this render yet.
        assert self._worker_get(f"/api/jobs/{job_id}/")["unreal_machine_results"] == [], job_id
        assert job["routing_note"] == PENDING_NOTE, job["routing_note"]
        measurement = self._get(f"/api/projects/{self.project_id}/")["unreal_measurement"]
        latest = measurement["latest"]
        assert measurement["held_pending"] == 1, measurement
        assert latest["status"] == "pending", latest
        assert latest["pilot_job_id"], latest
        return latest["pilot_job_id"]

    def _assert_render(self, result, latest, kind):
        """DONE, gated by this machine's result of ``kind`` (the pilot's record ``latest``), a valid image."""
        assert result["status"] == "DONE", (
            f"Job did not complete successfully. Status: {result['status']}, "
            f"Error: {result.get('error_message', 'N/A')}\n{result.get('last_output')}"
        )
        peak = latest["peak_mb"]
        summary = parse_summary(result["last_output"])
        assert_summary(summary, {**COMMON_SUMMARY, **RENDER_SUMMARY}, "Job")
        # The first job after the pilot: its gate needed exactly the pilot's peak, on a card of the pilot's kind.
        assert int(summary["vram_need_mb"]) == peak, summary
        assert summary["vram_card_kind"] == kind, (kind, summary)
        assert int(summary["vram_measurement_id"]) == latest["measurement_id"], (latest, summary)
        assert int(summary["vram_need_mb"]) <= int(summary["vram_usable_mb"]), summary
        # The job's payload for this worker: its result of that kind. The result peak is
        # max(pilot peak, job peaks reported since), so never below the pilot's (D2).
        entry = self._machine_result(result["id"], kind)
        assert entry["measurement_id"] == latest["measurement_id"], (entry, latest)
        assert entry["peak_mb"] >= peak and entry["ram"] == latest["ram"], (entry, latest)
        assert summary.get("vram_peak_mb"), summary
        assert "log_flood" not in summary, summary

        output_file = result.get("output_file")
        assert output_file, "Job completed but has no output_file"
        img = verify_image_output(self.session, output_file, self.base_url)
        assert img.size == OUTPUT_SIZE, f"Output is {img.size}, expected {OUTPUT_SIZE}"

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
        self._assert_pilot(pilot, PILOT_SUMMARY)
        latest = self._assert_measurement(pilot_id)
        kind = pilot["render_settings"].get("unreal.card_kind")
        assert kind, f"The pilot names no card kind: {pilot['render_settings']}"

        result = poll_sampling(self.session, job_url, RENDER_TIMEOUT, watch.sample)
        self._assert_render(result, latest, kind)
        self._assert_isolated()
        self._assert_no_farm_listeners(watch)
