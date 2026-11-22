# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Real per-machine memory test E2E, Part B: two machines with different card
kinds (Unreal per-machine memory test chunk 9, AC-E7, AC-E8, AC-E9; U-1).

This machine runs the manager and one worker (the cards of one kind
ticked); a second machine runs one more worker started by hand with
``tests/e2e/unreal_pm_remote_worker.py`` (``unreal_pm_two_machines``
describes the handoff). Each method first waits, bounded, for that worker.

- ``test_e7_two_kinds_one_version``: one version of shots 0055 and 0060 at
  640x360, three pieces held (job pause) until both machines' pilots are
  DONE, then released together. Two pilots, one targeted at each machine,
  with different kinds, drivers and peaks; each piece is gated by the
  result of the machine and kind it ran on; pieces ran on both machines
  (three pieces, at most three free cards between the two machines).
- ``test_e8_routing_by_result``: 12 frames at the over size
  (``SETHLANS_UNREAL_E2E_PM_OVER_SIZE``, default 5440x3060, chosen to fit a
  32 GB card and not a 20 GB one): one machine's pilot ends ``does_not_fit``
  and the other's ``fits``; every piece renders on the machine that fits;
  the version is DONE (not stopped) and its machine list shows both states.
  Which machine has the larger card is read from the results, never assumed.

AC-E9 for both: frames of the requested size and count. Observations go to
the result file (``unreal_pm_records.record``).

Opt-in: ``SETHLANS_UNREAL_E2E=1``, ``SETHLANS_UNREAL_E2E_SHARE``,
``SETHLANS_UNREAL_E2E_PM_TWO_MACHINES=1``, ``SETHLANS_UNREAL_E2E_PM_MANAGER_HOST``,
``SETHLANS_UNREAL_E2E_PM_PORT`` (see ``unreal_pm_config``). GPU time with
cold caches: E7 about 25 min on each machine (the pilots run side by side;
a cold warm-up is most of it), then about 5 min of pieces; E8 about 5 min
of pilots on each and about 5 min of one piece on the larger card. About
35-40 min per machine in all; much less with warm caches.
"""

import logging
import time

import pytest

from tests.e2e import unreal_e2e_config as cfg
from tests.e2e import unreal_pm_config as pm
from tests.e2e import unreal_pm_outputs as outputs
from tests.e2e import unreal_pm_records as rec
from tests.e2e.helpers import POLL_INTERVAL
from tests.e2e.unreal_pm_flow import PILOT_TIMEOUT
from tests.e2e.unreal_pm_two_machines import TwoMachineFlow
from tests.e2e.unreal_workflow_harness import parse_summary

logger = logging.getLogger(__name__)

_SKIP_REASON = pm.part_b_skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
PILOTS_APPEAR_S = 120


class TestUnrealPerMachineTwoMachines(TwoMachineFlow):
    """Two machines, two card kinds: own pilots, pieces on both, routing by result."""

    def _machines(self):
        """``{worker id: (hostname, kind)}`` of this machine and the second one."""
        remote, remote_kind = self._remote()
        return {self.worker_data["id"]: (self.worker_data["hostname"], self._local_kind()),
                remote["id"]: (remote["hostname"], remote_kind)}

    def _two_pilots(self, before, machines, label):
        """One new pilot per machine, targeted at it, of its kind; waits for both: ``{worker id: pilot}``."""
        deadline = time.monotonic() + PILOTS_APPEAR_S  # a pilot can also come from the next heartbeat sweep (D16)
        pilots = self._new_pilots(before)
        while len(pilots) < 2 and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
            pilots = self._new_pilots(before)
        targets = {rec.pilot_target(self.db_path, pilot["id"]): pilot for pilot in pilots}
        assert len(pilots) == 2 and set(targets) == set(machines), (
            f"{label}: expected one pilot per machine {machines}, got "
            f"{[(p['id'], rec.pilot_target(self.db_path, p['id']), p['render_settings']) for p in pilots]}")
        done = {}
        for worker_id, pilot in targets.items():
            assert pilot["render_settings"].get("unreal.card_kind") == machines[worker_id][1], (label, pilot)
            done[worker_id] = self._wait_job(pilot["id"], PILOT_TIMEOUT)
            self._record_job(f"{label} pilot", done[worker_id], machine=machines[worker_id])
            assert done[worker_id]["status"] == "DONE", (label, done[worker_id]["status"],
                                                         done[worker_id].get("error_message"))
        return done

    def _rows(self, version_id, machines):
        version = self._version(version_id)
        return {worker_id: self.machine_row(version, host, kind) for worker_id, (host, kind) in machines.items()}

    def _check_piece_gates(self, pieces, machines, label):
        """Each piece ran on a machine of the farm, gated by that machine's own result of its kind."""
        by_host = {host: (worker_id, kind) for worker_id, (host, kind) in machines.items()}
        for job in pieces:
            facts = parse_summary(job["last_output"])
            self._record_job(f"{label} piece", job)
            assert job["status"] == "DONE", f"{job['name']}: {job['status']} {job.get('error_message')}"
            worker_id, kind = by_host[job["assigned_worker_hostname"]]
            records = [r for r in rec.key_records(self.db_path, job["id"])
                       if r["worker_id"] == worker_id and r["card_kind"] == kind]
            assert len(records) == 1, (job["name"], records)
            assert (facts.get("vram_card_kind"), facts.get("vram_measurement_id")) == (kind, str(records[0]["id"])), (
                job["name"], records[0], rec.summary_facts(facts))

    def test_e7_two_kinds_one_version(self):
        machines, before = self._machines(), self._pilot_ids()
        version = self._submit(pm.SMALL_SIZE, shots=list(cfg.SEQUENCE_SHOTS))
        pieces, _audio = self._version_jobs(version["id"])
        planned = [(job["start_frame"], job["end_frame"], job["unreal_lead_in_frames"]) for job in pieces]
        assert planned == cfg.SEQUENCE_PIECES, planned
        for job in pieces:
            self._pause(job["id"])  # released together once both machines have their result
        self._two_pilots(before, machines, "AC-E7")
        rows = self._rows(version["id"], machines)
        rec.record("AC-E7 rows", rows=rows)
        assert all(row["state"] == "fits" for row in rows.values()), rows
        drivers = {row["driver_version"] for row in rows.values()}
        peaks = {row["peak_mb"] for row in rows.values()}
        assert len({row["card_kind"] for row in rows.values()}) == 2, rows
        assert None not in drivers and len(drivers) == 2, f"AC-E7: drivers {drivers}"
        assert len(peaks) == 2, f"AC-E7: both machines measured {peaks}"
        for job in pieces:
            self._unpause(job["id"])
        version = self._wait_version(version["id"])
        pieces, _audio = self._version_jobs(version["id"])
        self._check_piece_gates(pieces, machines, "AC-E7")
        hosts = {job["assigned_worker_hostname"] for job in pieces}
        assert hosts == {host for host, _kind in machines.values()}, f"AC-E7: pieces ran only on {hosts}"
        assert version["status"] == "DONE", (version["status"], version.get("error_message"))
        outputs.check_frames(self, version, pieces, pm.SMALL_SIZE, "V7")
        self._assert_isolated()

    def test_e8_routing_by_result(self):
        machines, before, size = self._machines(), self._pilot_ids(), pm.over_size()
        logger.info("Largest card per machine (MB): %s",
                    {host: self._cards_total(host) for host, _kind in machines.values()})
        version = self._submit(size, frames=pm.OVER_FRAMES)
        pilots = self._two_pilots(before, machines, "AC-E8")
        verdicts = {worker_id: parse_summary(pilot["last_output"]).get("pilot_verdict")
                    for worker_id, pilot in pilots.items()}
        version = self._wait_version(version["id"])
        rows = self._rows(version["id"], machines)
        rec.record("AC-E8 rows", rows=rows, verdicts=verdicts, size=size, version_status=version["status"])
        fits = [worker_id for worker_id, row in rows.items() if row["state"] == "fits"]
        too_big = [worker_id for worker_id, row in rows.items() if row["state"] == "does_not_fit"]
        assert len(fits) == 1 and len(too_big) == 1, f"AC-E8: expected one fits and one does_not_fit row: {rows}"
        assert verdicts == {fits[0]: "fits", too_big[0]: "does_not_fit"}, verdicts
        assert version["status"] == "DONE", (version["status"], version.get("error_message"))
        pieces, _audio = self._version_jobs(version["id"])
        assert len(pieces) == pm.OVER_PIECES, f"AC-E8: {len(pieces)} pieces, expected {pm.OVER_PIECES}"
        self._check_piece_gates(pieces, machines, "AC-E8")
        host = machines[fits[0]][0]
        assert all(job["assigned_worker_hostname"] == host for job in pieces), (
            f"AC-E8: pieces ran on {[job['assigned_worker_hostname'] for job in pieces]}, not only on {host}")
        outputs.check_frames(self, version, pieces, size, "V8")
        self._assert_isolated()
