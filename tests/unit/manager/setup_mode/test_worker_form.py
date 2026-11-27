# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``worker_form``: the worker parts of the setup form (FR-WZ-1, FR-WZ-4, FR-RS-3, FR-SEC-9).

The worker program's answers come from the conftest's ``fake_worker_cli``.
"""
import json
import threading

import pytest

from sethlans_manager.setup_mode import worker_cli, worker_form

CARD = {"address": "0000:01:00.0", "key": "pci:10de:2684:a1", "pci": "0000:01:00.0", "name": "RTX\x07 4090",
        "brand": "NVIDIA", "vendor_id": "10de", "device_id": 0x2684, "revision_id": True,
        "screen": "yes", "primary_screen": True, "driver_secret": "x"}


def _failed(command, broken=True):
    return worker_cli.CliResult(command, error="worker_timeout" if broken else "config_unreadable",
                                message="it broke", broken=broken)


class TestCards:

    def test_card_is_copied_field_by_field(self):
        """FR-WZ-4: each card is copied with type checks, never passed through."""
        assert worker_form.card_list({"cards": [CARD]}) == [{
            "address": "0000:01:00.0", "key": "pci:10de:2684:a1", "pci": "0000:01:00.0", "name": "RTX 4090",
            "brand": "NVIDIA", "vendor_id": "10de", "device_id": 0x2684, "revision_id": None,
            "screen": False, "primary_screen": True}]

    @pytest.mark.parametrize("raw", [None, "card", {"address": ""}, {"address": 5}, {"name": "no address"}])
    def test_entries_without_an_address_are_dropped(self, raw):
        """FR-WZ-4: only entries with a card address become ticks."""
        assert worker_form.card_list({"cards": [raw]}) == []

    def test_unknown_brand_and_list_cap(self):
        """FR-WZ-4: brand is one of the four known ones; at most 16 cards are read."""
        cards = [dict(CARD, address=f"0000:{n:02x}:00.0", brand="Matrox") for n in range(17)]
        found = worker_form.card_list({"cards": cards})
        assert len(found) == worker_form.MAX_CARDS and {card["brand"] for card in found} == {None}


class TestSavedWorker:

    @pytest.mark.parametrize("answer, expected", [
        ({}, {"compute": "cpu", "cards": [], "combine": False, "unreal_enabled": False, "ui_password": "",
              "ui_password_set": False, "compute_env_override": False, "unreal_enabled_env": False}),
        ({"saved": {"compute": "gpu", "cards": ["a", 3, "b\x00"], "combine": True, "env_override": True,
                    "ui_password_hash": "pbkdf2$x"},
          "unreal_enabled": True, "unreal_enabled_env": True, "ui_password_set": True, "ui_password": "pw"},
         {"compute": "gpu", "cards": ["a", "b"], "combine": True, "unreal_enabled": True, "ui_password": "",
          "ui_password_set": True, "compute_env_override": True, "unreal_enabled_env": True}),
        ({"saved": {"compute": "quantum", "cards": "a", "combine": "true"}},
         {"compute": "cpu", "cards": [], "combine": False, "unreal_enabled": False, "ui_password": "",
          "ui_password_set": False, "compute_env_override": False, "unreal_enabled_env": False}),
    ], ids=["defaults", "earlier_choice", "bad_types"])
    def test_earlier_values_without_secrets(self, answer, expected):
        """FR-RS-3 / FR-WZ-1: the earlier choice is pre-filled; the password field is always empty."""
        assert worker_form.saved_worker(answer) == expected


class TestEnrollment:

    @pytest.mark.parametrize("raw", [None, {}, {"enrolled": False, "manager_id": "m"}, {"enrolled": "true"}])
    def test_not_enrolled(self, raw):
        """FR-RS-3: only an explicit ``enrolled: true`` counts as an enrollment."""
        assert worker_form.enrollment_of({"enrollment": raw}) is None

    def test_enrolled_values_are_copied_and_capped(self):
        """FR-RS-3 / FR-SEC-9: id, name, host, port only; the token and fingerprint never come through."""
        raw = {"enrolled": True, "manager_id": "m-1", "manager_name": "N" * 70 + "\x1b", "host": "farm.local",
               "port": 8080, "api_token": "tok", "cert_fingerprint": "ab:cd", "token": "t"}
        assert worker_form.enrollment_of({"enrollment": raw}) == {
            "manager_id": "m-1", "manager_name": "N" * 64, "host": "farm.local", "port": 8080}

    @pytest.mark.parametrize("port, expected", [
        (1, 1), (65535, 65535), (0, None), (65536, None), ("8080", None), (True, None), (None, None)])
    def test_port_range(self, port, expected):
        """FR-RS-3: an enrollment port outside 1-65535 shows as null."""
        raw = {"enrolled": True, "manager_id": "", "host": "", "port": port}
        assert worker_form.enrollment_of({"enrollment": raw}) == {
            "manager_id": None, "manager_name": None, "host": None, "port": expected}


class TestManagers:

    def _manager(self, **over):
        entry = {"name": "Farm", "host": "farm.local", "ip": "192.168.1.9", "port": 8080, "manager_id": "m-1",
                 "version": "1.0"}
        entry.update(over)
        return entry

    def test_announcement_is_copied_with_a_url(self):
        """FR-WZ-1 / FR-SEC-9: name, host, ip, port, manager id and version, with a ready https URL."""
        raw = self._manager(name="Fa‮rm\n", extra="x")
        assert worker_form.manager_list({"managers": [raw]}) == [{
            "name": "Farm", "host": "farm.local", "ip": "192.168.1.9", "port": 8080, "manager_id": "m-1",
            "version": "1.0", "url": "https://192.168.1.9:8080"}]

    @pytest.mark.parametrize("over, url", [
        ({"ip": "not-an-ip"}, "https://farm.local:8080"),
        ({"ip": "", "host": "bad host"}, None),
    ])
    def test_url_falls_back_to_the_host(self, over, url):
        """FR-WZ-1: the URL uses the IPv4 address, else a host without spaces, else none."""
        assert worker_form.manager_list({"managers": [self._manager(**over)]})[0]["url"] == url

    @pytest.mark.parametrize("over", [{"manager_id": ""}, {"manager_id": None}, {"port": 0}, {"port": 65536},
                                      {"port": "8080"}])
    def test_entries_without_id_or_valid_port_are_dropped(self, over):
        """FR-WZ-1: an announcement without a manager id or a valid port is not offered."""
        assert worker_form.manager_list({"managers": [self._manager(**over), "junk"]}) == []

    def test_at_most_32_entries(self):
        """FR-WZ-1 [SR nit 11]: at most 32 managers."""
        raw = [self._manager(manager_id=f"m-{n}") for n in range(33)]
        assert len(worker_form.manager_list({"managers": raw})) == 32


class TestFormParts:

    @pytest.mark.parametrize("cards_ok, discover_ok, probe_failed, errors", [
        (True, True, False, []),
        (True, True, True, [{"part": "worker",
                             "message": "The operating system's list of graphics cards could not be read."}]),
        (False, True, False, [{"part": "worker", "message": "Could not list this machine's graphics cards: it broke"}]),
        (True, False, False, [{"part": "worker", "message": "Could not look for managers on the network: it broke"}]),
    ], ids=["ok", "os_probe_failed", "cards_failed", "discover_failed"])
    def test_failures_fill_errors_and_the_form_is_still_returned(self, fake_worker_cli, monkeypatch, cards_ok,
                                                                 discover_ok, probe_failed, errors):
        """FR-WZ-1: a CLI failure adds a line to ``errors``; its parts keep their defaults."""
        answers = fake_worker_cli.answers
        answers[worker_cli.CARDS].update(cards=[CARD], os_probe_failed=probe_failed,
                                         enrollment={"enrolled": True, "manager_id": "m", "host": "h", "port": 1})
        answers[worker_cli.DISCOVER]["managers"] = [{"manager_id": "m-1", "port": 8080, "host": "h"}]
        if not cards_ok:
            monkeypatch.setattr(worker_cli, "cards", lambda: _failed(worker_cli.CARDS, broken=False))
        if not discover_ok:
            monkeypatch.setattr(worker_cli, "discover", lambda: _failed(worker_cli.DISCOVER))
        found = []
        parts = worker_form.form_parts(found)
        assert found == errors
        assert (len(parts["cards"]), parts["enrollment"] is not None) == ((1, True) if cards_ok else (0, False))
        assert parts["cards_os_probe_failed"] is (cards_ok and probe_failed)
        assert parts["worker"]["compute"] == "cpu"
        assert len(parts["managers"]) == (1 if discover_ok else 0)

    def test_no_secret_reaches_the_form(self, fake_worker_cli):
        """FR-WZ-1 / FR-SEC-9: a secret in the worker's answer is never copied into the form."""
        answers = fake_worker_cli.answers
        answers[worker_cli.CARDS].update(
            api_token="tok-SECRET", ui_password="pw-SECRET", cards=[dict(CARD, token="card-SECRET")],
            saved={"compute": "cpu", "ui_password_hash": "hash-SECRET"},
            enrollment={"enrolled": True, "manager_id": "m", "api_token": "tok-SECRET",
                        "cert_fingerprint": "fp-SECRET"})
        answers[worker_cli.DISCOVER]["managers"] = [{"manager_id": "m", "port": 1, "key": "key-SECRET"}]
        assert "SECRET" not in json.dumps(worker_form.form_parts([]))

    def test_a_call_that_raises_is_a_broken_result(self, monkeypatch):
        """FR-WZ-1 (review: django nit 2): an exception in the manager while running a command fills
        ``errors``; the form is still returned."""
        def boom():
            raise RuntimeError("no pipes")

        monkeypatch.setattr(worker_cli, "cards", boom)
        found = []
        parts = worker_form.form_parts(found)
        assert found == [{"part": "worker", "message": "Could not list this machine's graphics cards: "
                                                       "The worker program could not be run; see the setup log."}]
        assert (parts["cards"], parts["enrollment"]) == ([], None)

    def test_a_call_past_its_deadline_is_not_waited_for(self, monkeypatch):
        """FR-WZ-1 (review: django nit 2): a command still running after its wait is a timeout; the
        form does not wait for its thread."""
        release = threading.Event()

        def stuck():
            release.wait()
            return worker_cli.CliResult(worker_cli.DISCOVER, answer={"managers": []})

        monkeypatch.setattr(worker_cli, "discover", stuck)
        monkeypatch.setattr(worker_form, "DISCOVER_WAIT", 0.0)
        found = []
        try:
            parts = worker_form.form_parts(found)   # returns while ``stuck`` is still blocked
        finally:
            release.set()
            for thread in threading.enumerate():
                if thread.name == f"setup-worker-cli{worker_cli.DISCOVER}":
                    thread.join()
        assert found == [{"part": "worker", "message": "Could not look for managers on the network: "
                                                       "The worker program did not answer in time; see the worker log."}]
        assert parts["managers"] == []
