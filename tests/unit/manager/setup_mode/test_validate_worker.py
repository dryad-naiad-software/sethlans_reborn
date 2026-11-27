# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The validation table: the Worker, Manager and Enrollment key sections (FR-WZ-3, FR-WZ-4, FR-WZ-5, FR-WZ-6, FR-RS-3).

``--setup-cards`` is answered by the conftest's ``fake_worker_cli``; ``fake_net`` names this machine ``setuphost``.
"""
import pytest

from sethlans_manager.setup_mode import fields, validate_worker, worker_cli
from sethlans_manager.setup_mode.validate_worker import EDGE_SPACE, NO_CARD, NOT_ENROLLED, NOT_SENDABLE, URL_SHAPE
from tests.unit.manager.setup_mode._helpers import GOOD_PASSWORD, answers

KEY = "4F9XK2PBQ7M3N8RT"
KEPT = {"manager_id": "m-1", "manager_name": "Farm", "host": "farm.local", "port": 8080}
ENROLLMENT = {"enrolled": True, **KEPT}
FOUND = [{"address": "A", "brand": "NVIDIA"}, {"address": "B", "brand": "NVIDIA"},
         {"address": "C", "brand": "AMD"}, {"address": "D", "brand": None}]
BAD_MANAGER_SECTIONS = {"server": {"host": "10.255.255.1", "port": 80}, "admin": {"username": "bad name!"}}


def _cards_answer(env_override=False, password_set=True, enrollment=None):
    return {"cards": FOUND, "saved": {"env_override": env_override}, "ui_password_set": password_set,
            "enrollment": enrollment}


def _worker(worker, cards_answer=None):
    errors = fields.Errors()
    payload = validate_worker.worker_section({"worker": worker}, cards_answer or _cards_answer(), errors)
    return errors.by_field, payload


def _manager(manager, enrollment=None):
    errors = fields.Errors()
    result = validate_worker.manager_section({"manager": manager}, _cards_answer(enrollment=enrollment), errors)
    return errors.by_field, result


class TestSectionsByMode:

    @pytest.mark.parametrize("mode, error_keys, commands", [
        ("manager", {"server.host", "server.port", "admin.username", "admin.password"}, []),
        ("manager_worker", {"server.host", "server.port", "admin.username", "admin.password", "worker.compute"},
         [worker_cli.CARDS]),
        ("worker", {"worker.compute", "manager.url", "manager.enrollment_key"}, [worker_cli.CARDS]),
    ])
    def test_each_mode_checks_its_sections(self, check, tmp_path, fake_worker_cli, mode, error_keys, commands):
        """FR-WZ-3: Server/Admin for the manager modes, Worker for the worker modes, Manager for worker only."""
        errors, plan = check(answers(tmp_path, mode=mode, worker={"compute": "fast", "ui_password": GOOD_PASSWORD},
                                     manager={"url": ""}, **BAD_MANAGER_SECTIONS))
        assert plan is None and set(errors) == error_keys
        assert [command for command, _payload, _timeout in fake_worker_cli.calls] == commands

    @pytest.mark.parametrize("mode, manager, enrollment, extra, kept", [
        ("manager_worker", {"url": "https://ignored"}, None, {}, None),
        ("worker", {"url": "https://farm.local", "enrollment_key": "4f9x-k2pb-q7m3-n8rt", "name": "Farm"}, None,
         {"manager_url": "https://farm.local:8080", "enrollment_key": KEY, "manager_name": "Farm"}, None),
        ("worker", {"keep_enrollment": True}, ENROLLMENT, {}, KEPT),
    ], ids=["manager_worker", "worker_enrolls", "worker_keeps"])
    def test_worker_plan(self, check, tmp_path, fake_worker_cli, probe, hasher, mode, manager, enrollment, extra,
                         kept):
        """FR-WZ-3 / FR-RS-3: the plan carries the --setup-apply payload; a kept enrollment adds no key."""
        fake_worker_cli.answers[worker_cli.CARDS]["enrollment"] = enrollment
        sections = BAD_MANAGER_SECTIONS if mode == "worker" else {}
        errors, plan = check(answers(tmp_path, mode=mode, manager=manager,
                                     worker={"compute": "cpu", "ui_password": GOOD_PASSWORD}, **sections))
        assert errors == {}
        assert plan.worker_apply == {"compute": "cpu", "cards": [], "combine": False, "unreal_enabled": False,
                                     "ui_password": GOOD_PASSWORD, **extra}
        assert plan.enrollment == kept
        assert GOOD_PASSWORD not in repr(plan) and KEY not in repr(plan)
        if mode == "worker":
            assert (plan.server_host, plan.server_port, plan.database, plan.first_admin) == (None, None, None, None)
            probe.assert_not_called()
            hasher.assert_not_called()

    def test_failed_setup_cards_raises(self, check, tmp_path, monkeypatch):
        """FR-WZ-2 / OQ-7: a failed --setup-cards stops the check with the worker's result."""
        failed = worker_cli.CliResult(worker_cli.CARDS, error="worker_timeout", message="late", broken=True)
        monkeypatch.setattr(worker_cli, "cards", lambda: failed)
        with pytest.raises(validate_worker.WorkerCliFailure) as raised:
            check(answers(tmp_path, mode="manager_worker"))
        assert raised.value.result is failed


class TestWorkerSection:

    @pytest.mark.parametrize("worker, field, message", [
        ({}, "worker.compute", "The compute method is required."),
        ({"compute": 3}, "worker.compute", "The compute method must be text."),
        ({"compute": "fast"}, "worker.compute", "Choose cpu, gpu or cpu_gpu."),
        ({"compute": "cpu", "cards": "A"}, "worker.cards",
         "Send the ticked cards as a list of at most 16 card addresses."),
        ({"compute": "cpu", "cards": ["A"] * 17}, "worker.cards",
         "Send the ticked cards as a list of at most 16 card addresses."),
        ({"compute": "cpu", "cards": [1]}, "worker.cards", "Send the ticked cards as a list of at most 16 card addresses."),
        ({"compute": "gpu", "cards": ["A", "A"]}, "worker.cards", "A graphics card is ticked more than once."),
        ({"compute": "cpu", "combine": "yes"}, "worker.combine", "Must be true or false."),
    ])
    def test_shape_errors(self, fake_net, worker, field, message):
        """FR-WZ-4 / FR-WZ-5: method and card list shapes."""
        assert _worker(worker) == ({field: [message]}, None)

    @pytest.mark.parametrize("compute, cards, combine, errors", [
        ("cpu", [], False, {}),
        ("gpu", [], False, {"worker.cards": [NO_CARD]}),
        ("cpu_gpu", None, False, {"worker.cards": [NO_CARD]}),
        ("gpu", ["X"], False, {"worker.cards": ["Not a graphics card of this machine: X."]}),
        ("gpu", ["A", "C"], False,
         {"worker.cards": ["Cards of more than one brand cannot be ticked together (AMD, NVIDIA)."]}),
        ("gpu", ["A", "D"], False, {}),
        ("gpu", ["A"], True, {"worker.combine": ["Combine GPUs needs two or more ticked graphics cards."]}),
        ("cpu_gpu", ["A", "B"], True, {}),
    ])
    def test_card_rules(self, fake_net, compute, cards, combine, errors):
        """FR-WZ-4 (worker-card-selection O1): known cards, one brand, GPU needs a card, Combine needs two."""
        found, payload = _worker({"compute": compute, "cards": cards, "combine": combine})
        assert found == errors
        if not errors:
            assert payload == {"compute": compute, "cards": cards or [], "combine": combine, "unreal_enabled": False,
                               "ui_password": None}

    @pytest.mark.parametrize("cards, errors", [
        (["X"], {}),
        (["X", "X"], {"worker.cards": ["A graphics card is ticked more than once."]}),
    ])
    def test_env_override_checks_only_the_shape(self, fake_net, cards, errors):
        """FR-WZ-4: while SETHLANS_COMPUTE_* set the cards, only the list's shape is checked."""
        found, _payload = _worker({"compute": "gpu", "cards": cards}, _cards_answer(env_override=True))
        assert found == errors

    @pytest.mark.parametrize("stored, password, errors, saved", [
        (True, "", {}, None),
        (True, None, {}, None),
        (False, "", {"worker.ui_password": ["A password is required."]}, None),
        (True, " " + GOOD_PASSWORD, {"worker.ui_password": [EDGE_SPACE]}, None),
        (False, GOOD_PASSWORD + " ", {"worker.ui_password": [EDGE_SPACE]}, None),
        (False, GOOD_PASSWORD + "€", {"worker.ui_password": [NOT_SENDABLE]}, None),
        (False, GOOD_PASSWORD + "\x7f", {"worker.ui_password": [NOT_SENDABLE]}, None),
        (False, GOOD_PASSWORD + "\xe9\xff", {}, GOOD_PASSWORD + "\xe9\xff"),
        (True, GOOD_PASSWORD, {}, GOOD_PASSWORD),
    ], ids=["keep", "keep_null", "none_stored", "lead_space", "trail_space", "not_latin1", "del_char", "latin1",
            "replace"])
    def test_ui_password(self, fake_net, stored, password, errors, saved):
        """FR-WZ-6 / FR-WK-3: an empty password keeps a stored one; a new one must be sendable by a browser."""
        found, payload = _worker({"compute": "cpu", "ui_password": password}, _cards_answer(password_set=stored))
        assert found == errors
        assert (payload or {}).get("ui_password") == saved

    @pytest.mark.parametrize("password, message", [
        ("short", "This password is too short. It must contain at least 8 characters."),
        ("setuphost", "The password is too similar to the username."),
    ])
    def test_ui_password_uses_django_validators_with_this_computer_as_user(self, fake_net, password, message):
        """FR-WZ-6: Django's configured validators, with an unsaved User named after this computer."""
        found, _payload = _worker({"compute": "cpu", "ui_password": password}, _cards_answer(password_set=False))
        assert message in found["worker.ui_password"]


class TestManagerUrl:

    @pytest.mark.parametrize("text, url", [
        ("https://farm.local", "https://farm.local:8080"),
        ("https://Farm.Local:9443/", "https://farm.local:9443"),
        ("https://10.0.0.5:65535/api/", "https://10.0.0.5:65535"),
        ("https://[fe80::1]:8443/api", "https://[fe80::1]:8443"),
        ("https://h:1", "https://h:1"),
    ])
    def test_accepted(self, text, url):
        """FR-WZ-5: https://host[:port], port 8080 when none is given."""
        assert validate_worker.manager_url(text) == url

    @pytest.mark.parametrize("text", [
        "http://farm.local", "ftp://farm.local", "https://", "https://h:0", "https://h:65536", "https://h:port",
        "https://user:pw@h", "https://h/setup", "https://h?x=1", "https://h#top",
    ])
    def test_refused(self, text):
        """FR-WZ-5: anything else is refused with one message."""
        with pytest.raises(ValueError, match=URL_SHAPE):
            validate_worker.manager_url(text)


class TestManagerSection:

    @pytest.mark.parametrize("manager, enrollment, errors, result", [
        ({"keep_enrollment": True}, ENROLLMENT, {}, {}),
        ({"keep_enrollment": True}, None, {"manager.keep_enrollment": [NOT_ENROLLED]}, None),
        ({}, None, {"manager.url": ["The manager URL is required."],
                    "manager.enrollment_key": ["The enrollment key is required."]}, None),
        ({"url": "http://h", "enrollment_key": KEY}, None, {"manager.url": [URL_SHAPE]}, None),
        ({"url": "https://h", "enrollment_key": "IIII-OOOO-LLLL-UUUU"}, None, {"manager.enrollment_key": [
            "Enter the enrollment key shown on the manager (16 letters and digits)."]}, None),
        ({"url": "https://h", "enrollment_key": KEY * 5}, None,
         {"manager.enrollment_key": ["The enrollment key is longer than 64 characters."]}, None),
        ({"url": "https://h", "enrollment_key": f" {KEY.lower()} ", "name": "\x1bFarm" + "x" * 70}, None, {},
         {"manager_url": "https://h:8080", "enrollment_key": KEY, "manager_name": "Farm" + "x" * 60}),
        ({"url": "https://h", "enrollment_key": KEY, "name": 5, "keep_enrollment": False}, ENROLLMENT, {},
         {"manager_url": "https://h:8080", "enrollment_key": KEY, "manager_name": None}),
    ], ids=["keep", "keep_not_enrolled", "missing", "bad_url", "bad_key", "long_key", "normalised", "re_enroll"])
    def test_manager_and_key(self, manager, enrollment, errors, result):
        """FR-WZ-5 / FR-RS-3: URL and key, or keep_enrollment only when the worker is enrolled."""
        assert _manager(manager, enrollment) == (errors, result)
