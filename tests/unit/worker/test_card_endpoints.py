# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke tests for the graphics card endpoints (FR-CW-2, FR-CW-4, FR-CW-5).

The probe is replaced by a fixed inventory and the saved ``compute`` section
lives in a dict (``config_compute._read_raw`` reads it, a fake
``config_store.set_many`` applies each write), so no real card is probed and
no real ``config.json`` is touched.
"""
import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_choice import KEY_XT_23
from tests.unit.worker._card_pages import fake_probes, fake_saved
from tests.unit.worker._card_pages import body as _body
from tests.unit.worker._card_pages import call as _call


@pytest.fixture
def saved(monkeypatch):
    return fake_saved(monkeypatch)


@pytest.fixture
def probes(monkeypatch):
    return fake_probes(monkeypatch)


class TestRuntime:

    @pytest.fixture(autouse=True)
    def _open(self, fresh_shutdown_event, bearer_header):
        self.auth = bearer_header

    def test_post_needs_password(self, saved, probes):
        assert _call('/api/control/cards', 'POST', _body())[0] == 401

    def test_deeply_nested_body_is_400_not_500(self, saved, probes):
        """About 1,000 nested arrays fit in the 4 KiB cap and make ``json.loads`` raise RecursionError."""
        raw = b'[' * 2000 + b']' * 2000
        assert len(raw) <= 4096
        status, answer = _call('/api/control/cards', 'POST', raw=raw, headers=self.auth)
        assert (status, answer['error']) == (400, "The request body must be a JSON object.")
        assert saved[1] == [] and probes == []

    def test_post_saves(self, saved, probes):
        status, answer = _call('/api/control/cards', 'POST', _body(), self.auth)
        assert status == 200 and answer['revision'] == 1
        assert answer['message'] == ("Saved. The new graphics card choice is in use once running GPU jobs on "
                                     "removed cards finish.")
        assert rebuild.pending() is True  # live rebuild (FR-CM-6, FR-CL-4)

    def test_env_override_is_409(self, saved, probes, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        status, answer = _call('/api/control/cards', 'POST', _body(), self.auth)
        assert (status, answer['error']) == (409, cards_view.ENV_OVERRIDE_TEXT)

    def test_stale_revision_is_409(self, saved, probes):
        status, answer = _call('/api/control/cards', 'POST', _body(revision=4), self.auth)
        assert (status, answer['error']) == (409, cards_view.STALE_TEXT)

    def test_held_card_is_400(self, saved, probes):
        saved[0]['compute']['alerts'] = [{'id': 'a' * 32, 'kind': 'card_lost', 'key': KEY_XT_23}]
        status, answer = _call('/api/control/cards', 'POST', _body(), self.auth)
        assert (status, answer['error']) == (400, 'Dismiss the alert for AMD Radeon RX 7900 XT first.')

    def test_get_is_cached_and_writes_nothing(self, saved, probes):
        for _ in range(10):
            status, answer = _call('/api/cards/')
            assert status == 200 and len(answer['cards']) == 2
        assert len(probes) == 1 and saved[1] == []
