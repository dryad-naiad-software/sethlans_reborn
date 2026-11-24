# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test for issue #301: the Docker entrypoint's setup marker.

The image has no setup wizard, so ``launcher.container_setup_marker.
setup_marker_problem`` writes the setup-complete marker into the
manager data folder (``SETHLANS_MANAGER_DATA_DIR``, ``/data`` in the
image) on start. Workers skip all work until the heartbeat reports
``manager_setup_complete: true``, so this file walks the real round
trip: same env-var data folder the container uses, a real worker token
through ``APIClient``, the heartbeat before and after the call, and the
loopback status endpoint's ``setup_mode`` after it.

Unlike ``test_heartbeat_setup_complete_roundtrip.py`` nothing is
patched: the heartbeat and the status view resolve the data folder
from the environment variable exactly as in the container.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from launcher.container_setup_marker import setup_marker_problem
from workers.services.data_dir import get_manager_data_dir
from workers.services.sentinel import SENTINEL_FILENAME
from workers.views import heartbeat as heartbeat_mod
from workers.views import status_public as status_public_mod

HEARTBEAT_URL = '/api/heartbeat/'
STATUS_URL = '/api/status/public/'
LOOPBACK_URLCONF = 'sethlans_manager.urls_loopback'


def _clear_status_count_cache() -> None:
    with status_public_mod._count_cache_lock:
        status_public_mod._count_cache.clear()


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Reset the heartbeat's sticky-True cache and the status count cache.

    A prior test that saw setup complete would otherwise make the first
    (False-expected) heartbeat report True: a silent false pass.
    """
    heartbeat_mod._reset_setup_complete_cache()
    _clear_status_count_cache()
    yield
    heartbeat_mod._reset_setup_complete_cache()
    _clear_status_count_cache()


def _heartbeat_setup_complete(client, hostname):
    resp = client.post(
        HEARTBEAT_URL, data={'hostname': hostname}, format='json',
    )
    assert resp.status_code == 200, resp.data
    assert 'manager_setup_complete' in resp.data
    return resp.data['manager_setup_complete']


@pytest.mark.django_db
class TestContainerSetupMarkerIntegration:
    """The entrypoint's marker flips the heartbeat flag workers gate on."""

    def test_marker_write_flips_heartbeat_and_status(
        self, worker_with_token, monkeypatch, tmp_path,
    ):
        worker, client = worker_with_token
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        monkeypatch.setenv('SETHLANS_MANAGER_DATA_DIR', str(data_dir))
        # The views must resolve the same folder the entrypoint writes to.
        assert get_manager_data_dir() == data_dir
        assert not (data_dir / SENTINEL_FILENAME).exists()

        # --- Before: no marker -> workers are told to idle ----------------
        assert _heartbeat_setup_complete(client, worker.hostname) is False

        # --- Entrypoint step: write the marker ----------------------------
        assert setup_marker_problem(data_dir) is None
        assert (data_dir / SENTINEL_FILENAME).is_file()

        # --- After: same worker, same client -> workers may take work -----
        assert _heartbeat_setup_complete(client, worker.hostname) is True

        # The loopback status endpoint (internal URLconf) agrees.
        with override_settings(ROOT_URLCONF=LOOPBACK_URLCONF):
            resp = Client().get(STATUS_URL)
        assert resp.status_code == 200
        assert resp.json()['setup_mode'] is False
