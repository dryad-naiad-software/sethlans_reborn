# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``worker_outcome`` and the worker steps of ``submit.apply_answers`` (FR-WZ-2, FR-WZ-8, OQ-7, FR-SEC-9).

The worker program is the conftest's ``fake_worker_cli``; the database test and free space are mocked.
"""
import json

import pytest

from sethlans_manager.setup_mode import submit, worker_cli, worker_outcome
from shared import data_folder
from tests.unit.manager.setup_mode._helpers import GOOD_PASSWORD, answers

KEY = "4F9XK2PBQ7M3N8RT"
WORKER = {"compute": "cpu", "ui_password": GOOD_PASSWORD}
ENROLL = {"url": "https://farm.local", "enrollment_key": KEY, "name": "Farm"}
KEPT = {"enrolled": True, "manager_id": "m-1", "manager_name": "Farm", "host": "fe80::1", "port": 8443}
APPLIED = {"enrolled": True, "manager_id": "m-1", "manager_name": "Farm", "cert_fingerprint": "AB:CD",
           "status": "pending"}
NOT_ENROLLED = {"enrolled": False, "kept_enrollment": False, "manager_url": None, "manager_id": None,
                "manager_name": None, "cert_fingerprint": None, "status": None}


def _refused(error, broken=False):
    return worker_cli.CliResult(worker_cli.APPLY, error=error, message=f"{error} text", broken=broken)


class TestFailure:

    @pytest.mark.parametrize("code, field", [
        ("invalid_key", "manager.enrollment_key"),
        ("unreachable", "manager.url"),
        ("certificate_mismatch", "manager.url"),
        ("manager_not_ready", "manager.url"),
        ("card_changed", "worker.cards"),
        ("card_held", "worker.cards"),
        ("config_unreadable", "non_field_errors"),
    ])
    def test_refusal_is_400_on_its_field(self, code, field):
        """FR-WZ-2 / OQ-7: a refusal the person can fix is a 400 on the field it points at."""
        assert worker_outcome.failure(_refused(code)) == (400, {
            "error": code, "message": f"{code} text", "fields": {field: [f"{code} text"]}})

    @pytest.mark.parametrize("code", [worker_cli.FAILED, worker_cli.TIMED_OUT, worker_cli.UNAVAILABLE,
                                      worker_cli.BAD_ANSWER])
    def test_broken_command_is_500(self, code):
        """FR-WZ-2: exit 2, a timeout, no program or an unreadable answer is a 500."""
        assert worker_outcome.failure(_refused(code, broken=True)) == (500, {"error": code, "message": f"{code} text"})


class TestFinish:

    def test_no_worker_set_up(self):
        """FR-WZ-8: the manager mode's finish answer has no worker part."""
        assert worker_outcome.finish(None, None, None) is None

    @pytest.mark.parametrize("result, url, kept, expected", [
        ({"enrolled": True, "manager_id": "m\x00-1", "manager_name": "N" * 70, "cert_fingerprint": "F" * 130,
          "status": "pending", "api_token": "tok"}, "https://farm.local:8080", None,
         {"enrolled": True, "kept_enrollment": False, "manager_url": "https://farm.local:8080", "manager_id": "m-1",
          "manager_name": "N" * 64, "cert_fingerprint": "F" * 128, "status": "pending"}),
        ({"enrolled": True, "manager_id": 5, "manager_name": None, "cert_fingerprint": [], "status": ""},
         "https://h:1", None, {**NOT_ENROLLED, "enrolled": True, "manager_url": "https://h:1"}),
        ({"enrolled": "yes", "manager_id": "m-1"}, None, None, NOT_ENROLLED),
        ({"enrolled": False}, None, {"manager_id": "m-1", "manager_name": "Farm", "host": "fe80::1", "port": 8443},
         {**NOT_ENROLLED, "kept_enrollment": True, "manager_url": "https://[fe80::1]:8443", "manager_id": "m-1",
          "manager_name": "Farm"}),
        ({"enrolled": False}, None, {"manager_id": None, "manager_name": None, "host": "farm", "port": None},
         {**NOT_ENROLLED, "kept_enrollment": True}),
    ], ids=["enrolled", "enrolled_bad_types", "not_enrolled", "kept", "kept_without_port"])
    def test_worker_part(self, result, url, kept, expected):
        """FR-WZ-8 / FR-SEC-9: enrolled now or kept, with id, name, fingerprint and status as capped text."""
        assert worker_outcome.finish(result, url, kept) == expected


@pytest.fixture
def steps(fake_worker_cli, monkeypatch, fake_net, probe, free_space):
    """The order of the worker commands, the data folder and the settings write."""
    order = []
    fake_run, real_prepare, real_write = worker_cli.run, data_folder.prepare_data_root, submit.write_settings

    def run(command, payload, timeout, data_root=None):
        order.append((command, data_root))
        return fake_run(command, payload, timeout, data_root)

    def prepare(root):
        order.append("prepare")
        return real_prepare(root)

    def write(plan):
        order.append("write")
        return real_write(plan)

    monkeypatch.setattr(worker_cli, "run", run)
    monkeypatch.setattr(data_folder, "prepare_data_root", prepare)
    monkeypatch.setattr(submit, "write_settings", write)
    return order


class TestApplyAnswers:

    def test_order_validate_folder_apply_write(self, steps, ctx, tmp_path, fake_worker_cli, hasher):
        """FR-WZ-2 / OQ-7: validate, data folder, --setup-apply, then sethlans.ini last."""
        outcome = submit.apply_answers(answers(tmp_path, mode="manager_worker", worker=WORKER), ctx, "h")
        root = tmp_path / "sethlans-data"
        assert (outcome.status, outcome.saved) == (200, True)
        assert steps == [(worker_cli.CARDS, None), "prepare", (worker_cli.APPLY, root), "write"]
        assert fake_worker_cli.calls[-1][1] == {"compute": "cpu", "cards": [], "combine": False,
                                                "unreal_enabled": False, "ui_password": GOOD_PASSWORD}

    @pytest.mark.parametrize("result, status", [
        (_refused("invalid_key"), 400), (_refused(worker_cli.TIMED_OUT, broken=True), 500)])
    def test_failed_apply_writes_nothing(self, steps, ctx, tmp_path, ini_path, monkeypatch, caplog, result, status):
        """OQ-7 / FR-WK-2: a failed --setup-apply leaves sethlans.ini unwritten; no secret in the answer or log."""
        fake_run = worker_cli.run
        monkeypatch.setattr(worker_cli, "run", lambda command, *args, **kwargs: result if command == worker_cli.APPLY
                            else fake_run(command, *args, **kwargs))
        caplog.set_level("DEBUG")
        outcome = submit.apply_answers(answers(tmp_path, mode="worker", worker=WORKER, manager=ENROLL), ctx, "h")
        assert outcome == submit.Outcome(status, worker_outcome.failure(result)[1])
        assert "write" not in steps and not ini_path.exists()
        for secret in (GOOD_PASSWORD, KEY):
            assert secret not in json.dumps(outcome.body) and secret not in caplog.text

    def test_failed_setup_cards_stops_before_the_data_folder(self, steps, ctx, tmp_path, ini_path, monkeypatch):
        """FR-WZ-2 / OQ-7: a failed --setup-cards while checking is answered before anything is written."""
        failed = worker_cli.CliResult(worker_cli.CARDS, error=worker_cli.UNAVAILABLE, message="gone", broken=True)
        monkeypatch.setattr(worker_cli, "cards", lambda: failed)
        outcome = submit.apply_answers(answers(tmp_path, mode="worker", worker=WORKER, manager=ENROLL), ctx, "h")
        assert outcome == submit.Outcome(500, {"error": worker_cli.UNAVAILABLE, "message": "gone"})
        assert steps == [] and not ini_path.exists() and not (tmp_path / "sethlans-data").exists()

    @pytest.mark.parametrize("mode, manager, enrollment, applied, head, worker", [
        ("worker", ENROLL, None, APPLIED, {"restarting": False, "url": None, "port_changed": False},
         {**APPLIED, "kept_enrollment": False, "manager_url": "https://farm.local:8080"}),
        ("worker", {"keep_enrollment": True}, KEPT, {"enrolled": False},
         {"restarting": False, "url": None, "port_changed": False},
         {**NOT_ENROLLED, "kept_enrollment": True, "manager_url": "https://[fe80::1]:8443", "manager_id": "m-1",
          "manager_name": "Farm"}),
        ("manager_worker", {}, None, {"enrolled": False},
         {"restarting": True, "url": "https://h:18444/", "port_changed": True}, NOT_ENROLLED),
    ], ids=["worker_enrolled", "worker_kept", "manager_worker"])
    def test_finish_body(self, steps, ctx, tmp_path, fake_worker_cli, hasher, mode, manager, enrollment, applied,
                         head, worker):
        """FR-WZ-8 / FR-SEC-9: the finish answer of each worker mode; no password or key in it."""
        fake_worker_cli.answers[worker_cli.CARDS]["enrollment"] = enrollment
        fake_worker_cli.answers[worker_cli.APPLY] = applied
        outcome = submit.apply_answers(answers(tmp_path, mode=mode, worker=WORKER, manager=manager), ctx, "h")
        body = json.loads(outcome.body)
        assert {key: body[key] for key in head} == head
        assert body["worker"] == worker
        assert body["first_admin"] == ("boss" if mode == "manager_worker" else None)
        assert GOOD_PASSWORD not in outcome.body.decode() and KEY not in outcome.body.decode()
