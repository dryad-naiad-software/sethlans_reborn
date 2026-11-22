# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Requests and waits shared by the per-machine memory test E2E (chunk 9).

``MachineTestFlow`` is ``UnrealChecks`` (the manager + Unreal worker harness
and the shared Unreal checks) plus: one shots job per class (memoized, so
each test method runs alone too); sequence versions by shots or by frame
range at any size, with sound, videos and draft as asked; a version's pieces
and audio jobs; the pilots created since a point; job pause and unpause
(``POST /api/jobs/{id}/pause/``: a paused QUEUED job is skipped by the poll,
which is how the tests release jobs held for a pilot in a fixed order); a version's
machine rows (``machines``, FR-MT-U-1); a worker's ticked card kinds from
its heartbeat ``card_status`` (D1, ``shared.unreal.card_kind``, never from
a card's name); and the test database for what no API shows
(``unreal_pm_records``).
"""

import logging
import time

from tests.e2e import unreal_e2e_config as cfg
from tests.e2e import unreal_pm_records as rec
from tests.e2e.helpers import POLL_INTERVAL
from tests.e2e.log_capture import peek_log_files
from tests.e2e.unreal_e2e_checks import UnrealChecks
from tests.e2e.unreal_workflow_harness import parse_summary, poll_sampling

logger = logging.getLogger(__name__)

VERSIONS = "/api/sequence-submissions"
VERSION_FINAL = {"DONE", "PARTLY_DONE", "ERROR", "CANCELED"}
# The first Unreal job also copies and extracts the 3.7 GB archive.
DISCOVERY_TIMEOUT = 3600
# A cold pilot took about 23 minutes (prep, a 13-14 minute warm-up, its frames).
PILOT_TIMEOUT = 7200
VERSION_TIMEOUT = 5400
OLD_MINIMUM_MARKS = ("12478", "12,478", "12 478")
NO_FIT = "This render does not fit on any graphics card that is on."  # FR-MT-N-2


def rows_of(data):
    return data["results"] if isinstance(data, dict) else data


class MachineTestFlow(UnrealChecks):
    """The harness and checks, plus the requests the per-machine memory test E2E makes."""

    _discovery_done = False
    piece_seconds = cfg.PIECE_SECONDS

    @property
    def db_path(self):
        return self.temp_dir / "test_e2e_db.sqlite3"

    def _post(self, path, body=None, expected=(201,)):
        resp = self.session.post(f"{self.base_url}{path}", json=body, timeout=60)
        assert resp.status_code in expected, f"POST {path}: {resp.status_code} {resp.text}"
        return resp.json()

    def _target(self):
        return {"asset": self.asset_id, "map": cfg.RENDER_SETTINGS["unreal.map"],
                "sequence": cfg.RENDER_SETTINGS["unreal.sequence"], "preset": cfg.RENDER_SETTINGS["unreal.preset"]}

    def _sample(self):
        """Called on every poll round; a test class may sample more (network watch)."""

    def _discovered(self):
        """The project settings and one DONE shots job for the class (memoized)."""
        cls = type(self)
        if cls._discovery_done:
            return
        body = {"unreal_piece_seconds": self.piece_seconds, "unreal_lead_in_ms": cfg.LEAD_IN_MS}
        resp = self.session.patch(f"{self.base_url}/api/projects/{self.project_id}/", json=body, timeout=30)
        assert resp.status_code == 200, f"Project settings: {resp.status_code} {resp.text}"
        record = self._post("/api/sequence-discoveries/", self._target(), expected=(200, 201))
        found = poll_sampling(self.session, f"{self.base_url}/api/sequence-discoveries/{record['id']}/",
                              DISCOVERY_TIMEOUT, self._sample)
        assert found["status"] == "DONE", f"Discovery failed: {found.get('error_message')}"
        bounds = {shot["name"]: (shot["start"], shot["end"]) for shot in found["shots"]}
        for name, expected in cfg.SEQUENCE_SHOTS.items():
            assert bounds.get(name) == expected, f"Shot {name}: {bounds.get(name)}, expected {expected}"
        cls._discovery_done = True

    def _submit(self, size, shots=None, frames=None, sound=False, video=None, merge=False, draft=None):
        """A version of ``shots`` (names) or ``frames`` ((first, last) inclusive) at ``size``."""
        self._discovered()
        body = dict(self._target(), resolution_x=size[0], resolution_y=size[1], frame_format="PNG",
                    video_settings=video, sound=sound, merge_sound=merge)
        if shots is not None:
            body["shots"] = list(shots)
        else:
            body["frame_start"], body["frame_end"] = frames
        if draft is not None:
            body["draft"] = dict(draft)
        version = self._post(f"{VERSIONS}/", body)
        assert version["status"] == "QUEUED", version
        return version

    def _version(self, version_id):
        return self._get(f"{VERSIONS}/{version_id}/")

    def _version_jobs(self, version_id):
        """``(pieces, audio jobs)`` of the version, in edit order."""
        rows = rows_of(self._get(f"/api/jobs/?asset__project={self.project_id}"))
        mine = sorted((job for job in rows if job["sequence_submission"] == version_id),
                      key=lambda job: (job["start_frame"], job["id"]))
        return ([job for job in mine if job["unreal_kind"] == "render"],
                [job for job in mine if job["unreal_kind"] == "audio"])

    def _pilot_ids(self):
        return {job["id"] for job in self._pilots()}

    def _new_pilots(self, before):
        return sorted((job for job in self._pilots() if job["id"] not in before), key=lambda job: job["id"])

    def _job(self, job_id):
        return self._get(f"/api/jobs/{job_id}/")

    def _pause(self, job_id):
        self._post(f"/api/jobs/{job_id}/pause/", expected=(200,))

    def _unpause(self, job_id):
        self._post(f"/api/jobs/{job_id}/unpause/", expected=(200,))

    def _wait_job(self, job_id, timeout, held_url=None):
        return poll_sampling(self.session, f"{self.base_url}/api/jobs/{job_id}/", timeout, self._sample,
                             held_url=held_url)

    def _wait_started(self, job_ids, timeout):
        """Every job of ``job_ids`` left QUEUED (claimed); returns them as read last."""
        deadline = time.monotonic() + timeout
        jobs = []
        while time.monotonic() < deadline:
            self._sample()
            jobs = [self._job(job_id) for job_id in job_ids]
            if all(job["status"] != "QUEUED" for job in jobs):
                return jobs
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"Jobs not claimed within {timeout}s: {[(j['id'], j['status']) for j in jobs]}")

    def _wait_version(self, version_id, timeout=VERSION_TIMEOUT):
        return poll_sampling(self.session, f"{self.base_url}{VERSIONS}/{version_id}/", timeout, self._sample,
                             terminal=VERSION_FINAL)

    def _workers(self):
        return self._get("/api/heartbeat/")

    def _worker(self, hostname):
        found = [worker for worker in self._workers() if worker.get("hostname") == hostname]
        assert len(found) == 1, f"No single worker {hostname}"
        return found[0]

    @staticmethod
    def ticked_kinds(worker):
        """The D1 kinds of a worker's ticked ``ok`` cards (its heartbeat ``card_status``)."""
        from shared.unreal.card_kind import card_kind
        cards = (worker.get("card_status") or {}).get("cards") or []
        kinds = {card_kind(card.get("vendor_id"), card.get("device_id"), card.get("revision_id"))
                 for card in cards if card.get("ticked") is True and card.get("state") == "ok"}
        return kinds - {None}

    @staticmethod
    def machine_row(version, hostname, kind=None):
        rows = [row for row in version.get("machines") or []
                if row["hostname"] == hostname and (kind is None or row["card_kind"] == kind)]
        assert len(rows) == 1, f"No single machine row of {hostname} ({kind}): {version.get('machines')}"
        return rows[0]

    def _wait_row_state(self, version_id, hostname, states, timeout=600):
        """The version's row of ``hostname`` once its state is in ``states`` (memory reports land async)."""
        deadline, row = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            row = self.machine_row(self._version(version_id), hostname)
            if row["state"] in states:
                return row
            time.sleep(POLL_INTERVAL)
        raise AssertionError(f"Row of {hostname} stayed {row} (wanted {states})")

    def _worker_log(self):
        stdout, stderr = peek_log_files(self.worker_proc)
        return stdout + "\n" + stderr

    def _record_job(self, name, job, **extra):
        """One result line for a job: its status, worker and summary facts."""
        rec.record(name, job=job["id"], job_name=job["name"], status=job["status"],
                   worker=job.get("assigned_worker_hostname"), error=job.get("error_message"),
                   summary=rec.summary_facts(parse_summary(job.get("last_output"))), **extra)

    def _assert_no_old_minimum(self):
        """U-2 / AC-E1: no job summary of the project mentions the removed 12,478 MB pilot minimum."""
        rows = rows_of(self._get(f"/api/jobs/?asset__project={self.project_id}"))
        hits = [(job["id"], mark) for job in rows for mark in OLD_MINIMUM_MARKS
                if mark in (job.get("last_output") or "") or mark in (job.get("error_message") or "")]
        assert not hits, f"Job summaries still mention the 12,478 MB minimum: {hits}"

    def _stopped_version(self, version_id, label):
        """AC-E4: the version stopped by fits-nowhere, its message naming the card and the lower bound."""
        version = self._wait_version(version_id, 1800)
        row = self.machine_row(version, self.worker_data["hostname"])
        rec.record(f"{label} stopped version", status=version["status"], error=version.get("error_message"), row=row)
        assert version["status"] == "ERROR" and row["state"] == "does_not_fit", (version["status"], row)
        lower = row["lower_bound_mb"] if row["lower_bound_mb"] is not None else row["peak_mb"]
        message = version.get("error_message") or ""
        for part in (NO_FIT, f"{row['card_name']} ({row['card_total_mb']} MB) on {row['hostname']}",
                     f"at least {lower} MB"):
            assert part in message, f"{label}: {part!r} not in the version's message {message!r}"
        pieces, _audio = self._version_jobs(version_id)
        assert all(job["status"] == "ERROR" and NO_FIT in (job["error_message"] or "") for job in pieces), pieces
        return row
