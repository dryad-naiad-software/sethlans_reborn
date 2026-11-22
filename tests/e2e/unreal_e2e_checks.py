# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Checks shared by the real Unreal E2E tests (Unreal foundation FR-T-8,
sequence rendering chunk 9).

``UnrealChecks`` is the manager + Unreal worker harness plus the checks
every real Unreal run makes: the pilot's summary and CSV peak; the
project's one pilot (found in the job list, ``unreal_kind == "pilot"``);
this machine's row in the project's measurement key (per-machine memory
test FR-MT-U-1: ``unreal_measurement.keys[*].machines``) with the
brand-independent peak bound (#281); the worker's own memory results for
a job (``unreal_machine_results``, sent only to worker-token requests:
FR-MT-T-3); the isolation snapshot (HostAuth excluded, OQ-31), no orphans,
and no farm process that would prompt the firewall.
"""

import logging

from tests.e2e import unreal_isolation
from tests.e2e.enrollment_helpers import resolve_worker_config_path, wait_for_worker_config
from tests.e2e.tls_trust import manager_session
from tests.e2e.unreal_e2e_config import (
    ENGINE_BUILD,
    MIN_PILOT_PEAK_MB,
    archive_on_share,
    peak_upper_bound_mb,
)
from tests.e2e.unreal_workflow_harness import UnrealWorkflowHarness, parse_summary

logger = logging.getLogger(__name__)

# Summary keys every Unreal render, pilot or sound job reports (FR-UE-14, FR-T-8).
COMMON_SUMMARY = {
    "engine": ENGINE_BUILD,
    "user_state": "isolated",
    "user_cvars": "0",
    "zen": "private:8668",
    "zen_ddc_guard": "ok",
    "compilation_cancelled": "0",
    "job_object": "ok",
    "survivors": "",
    "pristine_integrity": "ok",
}
# Machine row states (per-machine memory test D2): a pilot QUEUED or RENDERING, a fitting result.
TESTING, FITS = "testing", "fits"


def assert_summary(summary, expected, what):
    """Every ``expected`` key has its value in ``summary``."""
    wrong = {
        key: summary.get(key, "<missing>")
        for key, value in expected.items() if summary.get(key) != value
    }
    assert not wrong, f"{what} summary differs from {expected}: {wrong}"


class UnrealChecks(UnrealWorkflowHarness):
    """The harness plus the checks shared by every real Unreal test class."""

    def _get(self, path):
        resp = self.session.get(f"{self.base_url}{path}", timeout=30)
        assert resp.status_code == 200, f"GET {path}: {resp.status_code} {resp.text}"
        return resp.json()

    def _worker_get(self, path):
        """GET ``path`` with the worker's own token: the worker's view, worker-only fields included."""
        config = wait_for_worker_config(resolve_worker_config_path(self.temp_dir / "worker_data"), timeout=30)
        headers = {"Authorization": f"Token {config['manager']['api_token']}"}
        with manager_session(self.manager_env) as session:
            resp = session.get(f"{self.base_url}{path}", headers=headers, timeout=30)
        assert resp.status_code == 200, f"Worker GET {path}: {resp.status_code} {resp.text}"
        return resp.json()

    def _machine_result(self, job_id, kind):
        """The one ``unreal_machine_results`` entry of card kind ``kind`` the manager sends this worker for the job."""
        results = self._worker_get(f"/api/jobs/{job_id}/")["unreal_machine_results"]
        found = [entry for entry in results or [] if entry["card_kind"] == kind]
        assert len(found) == 1, f"Job {job_id}: no single result of card kind {kind!r} for this worker: {results}"
        return found[0]

    def _assert_pilot(self, pilot, expected):
        """The pilot is DONE with ``expected`` summary keys and a CSV peak within 10%."""
        assert pilot["status"] == "DONE", (
            f"Pilot did not complete. Status: {pilot['status']}, "
            f"Error: {pilot.get('error_message', 'N/A')}\n{pilot.get('last_output')}"
        )
        assert pilot["unreal_kind"] == "pilot", pilot
        summary = parse_summary(pilot["last_output"])
        assert_summary(summary, {**COMMON_SUMMARY, **expected}, "Pilot")
        peak, csv_peak = int(summary["pilot_peak_mb"]), summary["pilot_csv_peak_mb"]
        assert csv_peak, "Pilot has no CSV peak"
        assert abs(peak - int(csv_peak)) <= peak * 0.10, (
            f"CSV peak {csv_peak} MB is not within 10% of the peak {peak} MB"
        )

    def _pilots(self):
        """The project's pilot jobs (the job list, filtered by project)."""
        data = self._get(f"/api/jobs/?asset__project={self.project_id}")
        rows = data["results"] if isinstance(data, dict) else data
        return [job for job in rows if job["unreal_kind"] == "pilot"]

    def _the_pilot(self):
        """The project's one pilot (one machine, one card kind), naming its card kind."""
        pilots = self._pilots()
        assert len(pilots) == 1, (
            f"Expected one pilot: {[(job['id'], job['status'], job['render_settings']) for job in pilots]}"
        )
        assert pilots[0]["render_settings"].get("unreal.card_kind"), f"The pilot names no card kind: {pilots[0]}"
        return pilots[0]

    def _measurement_row(self, kind):
        """``(unreal_measurement, key, row)``: the project's one key and this machine's row of card kind ``kind``."""
        measurement = self._get(f"/api/projects/{self.project_id}/")["unreal_measurement"]
        keys = [key for key in measurement["keys"] or [] if str(key["asset_id"]) == str(self.asset_id)]
        assert len(keys) == 1, f"Expected one measurement key for asset {self.asset_id}: {measurement}"
        hostname = self.worker_data["hostname"]
        rows = [row for row in keys[0]["machines"] if row["hostname"] == hostname and row["card_kind"] == kind]
        assert len(rows) == 1, f"No single row of {hostname}, card kind {kind!r}: {keys[0]['machines']}"
        return measurement, keys[0], rows[0]

    def _pending_pilot(self, held):
        """Before any result: one pilot, this machine's row of its kind ``testing``, ``held`` jobs held for it."""
        pilot = self._the_pilot()
        measurement, _key, row = self._measurement_row(pilot["render_settings"]["unreal.card_kind"])
        assert row["state"] == TESTING, row
        assert measurement["held_pending"] == held, measurement
        return pilot

    def _card_total_mb(self, row):
        """The measured card's total memory from this worker's heartbeat reading."""
        hostname = self.worker_data["hostname"]
        workers = [w for w in self._get("/api/heartbeat/") if w.get("hostname") == hostname]
        gpus = (workers[0].get("gpu_memory") or {}).get("gpus") if workers else None
        logger.info("Measured card %s; worker GPU reading: %s", row["card_name"], gpus)
        return peak_upper_bound_mb(gpus, row["card_name"], row["card_total_mb"])

    def _assert_measurement(self, pilot):
        """This machine's result of the pilot's card kind fits and is plausible; nothing is held. Returns its row."""
        kind = pilot["render_settings"].get("unreal.card_kind")
        assert kind, f"The pilot names no card kind: {pilot['render_settings']}"
        # Still the project's only pilot, so this machine's result of that kind is the one it measured.
        assert self._the_pilot()["id"] == pilot["id"], pilot["id"]
        measurement, key, row = self._measurement_row(kind)
        assert row["state"] == FITS and not row["covered"], row
        # The peak varies by card and driver: at least a working measurement,
        # at most the measured card's own memory (the worker's reading).
        card_total = self._card_total_mb(row)
        assert card_total, f"No total memory for the measured card: {row}"
        assert MIN_PILOT_PEAK_MB <= row["peak_mb"] <= card_total, (
            f"Peak {row['peak_mb']} MB is outside {MIN_PILOT_PEAK_MB}..{card_total} MB: {row}"
        )
        assert key["engine_build"] == ENGINE_BUILD, key
        held = {k: measurement[k] for k in ("held_pending", "held_failed", "held_none")}
        assert held == {"held_pending": 0, "held_failed": 0, "held_none": 0}, held
        return row

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
