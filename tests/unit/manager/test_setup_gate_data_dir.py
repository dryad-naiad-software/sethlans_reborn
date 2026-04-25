# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``_get_data_dir()`` in
``manager/sethlans_manager/middleware/setup_gate.py`` (issue #137).

The function chooses the manager data directory using the following
precedence:

1. Frozen build -> OS-appropriate per-user data dir
   (``shared.frozen_paths.get_data_dir("manager")``).
2. Otherwise, ``$SETHLANS_MANAGER_DATA_DIR`` if set.
3. Otherwise, ``settings.BASE_DIR``.

The env-var override exists so the E2E harness can anchor the manager
subprocess's sentinel reads at the same per-test tmp tree the harness
writes to (see ``tests/e2e/env_config.py:build_manager_env``).  Without
it, the manager would read ``BASE_DIR`` and report
``manager_setup_complete: False`` even though the harness wrote a fresh
sentinel into its own tmp dir.

The final test in this file is load-bearing for the #137 fix: it drives
a real ``/api/heartbeat/`` request after pointing the env var at a tmp
sentinel and asserts the response reports ``manager_setup_complete:
True``.  Reverting the env-var branch in production must make this test
fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from sethlans_manager.middleware import setup_gate
from sethlans_manager.middleware.setup_gate import _get_data_dir
from workers.models import Worker
from workers.services.sentinel import create_sentinel
from workers.views import heartbeat as heartbeat_mod

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'

ENV_VAR = 'SETHLANS_MANAGER_DATA_DIR'


# ---------------------------------------------------------------------------
# Pure unit tests for the precedence logic in ``_get_data_dir``
# ---------------------------------------------------------------------------


class TestGetDataDirEnvVar:
    """Precedence tests for ``_get_data_dir()`` (no DB, no HTTP)."""

    def test_returns_settings_base_dir_when_env_var_unset(
        self, monkeypatch, mocker,
    ):
        """With ``SETHLANS_MANAGER_DATA_DIR`` unset and not frozen, the
        function falls through to ``settings.BASE_DIR``.

        This is the long-standing default and must remain the behavior
        for dev runs that do not opt into the override.
        """
        monkeypatch.delenv(ENV_VAR, raising=False)
        mocker.patch.object(setup_gate, 'is_frozen', return_value=False)

        assert _get_data_dir() == settings.BASE_DIR

    def test_returns_env_var_path_when_set(
        self, monkeypatch, mocker, tmp_path,
    ):
        """When the env var is set, the function returns it as a Path.

        This is the override path used by the E2E harness so the manager
        subprocess and the test harness agree on where the sentinel
        lives.
        """
        monkeypatch.setenv(ENV_VAR, str(tmp_path))
        mocker.patch.object(setup_gate, 'is_frozen', return_value=False)

        result = _get_data_dir()

        assert isinstance(result, Path)
        assert result == tmp_path

    def test_returns_env_var_path_even_if_dir_does_not_exist(
        self, monkeypatch, mocker, tmp_path,
    ):
        """The function does not validate the override path.

        Documenting the contract: ``_get_data_dir`` is a pure path
        resolver -- existence/permission checks are left to callers
        (``read_sentinel`` already tolerates a missing directory by
        returning ``None``).  A future refactor that adds an
        ``exists()`` check here would silently break the E2E harness's
        cleanup-then-create ordering, so lock the no-validation
        contract in.
        """
        nonexistent = tmp_path / 'does' / 'not' / 'exist'
        assert not nonexistent.exists()
        monkeypatch.setenv(ENV_VAR, str(nonexistent))
        mocker.patch.object(setup_gate, 'is_frozen', return_value=False)

        result = _get_data_dir()

        assert result == nonexistent

    def test_frozen_mode_ignores_env_var(
        self, monkeypatch, mocker, tmp_path,
    ):
        """In a frozen build, the env var MUST NOT be consulted.

        Frozen installs use the OS-appropriate per-user data dir; an
        accidental ``SETHLANS_MANAGER_DATA_DIR`` in the user's
        environment must not redirect a packaged manager away from its
        canonical sentinel location.
        """
        frozen_path = tmp_path / 'frozen-data'
        monkeypatch.setenv(ENV_VAR, str(tmp_path / 'env-override'))
        mocker.patch.object(setup_gate, 'is_frozen', return_value=True)
        get_data_dir_mock = mocker.patch.object(
            setup_gate, 'get_data_dir', return_value=frozen_path,
        )

        result = _get_data_dir()

        assert result == frozen_path
        get_data_dir_mock.assert_called_once_with('manager')


# ---------------------------------------------------------------------------
# Integration-flavor unit test: heartbeat reads the override
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_heartbeat_setup_cache():
    """Reset the sticky-True setup-complete cache around every test.

    The heartbeat view caches a True observation of
    ``is_setup_complete`` at module scope (issue #130).  Without this
    reset, a previous test in the run that observed True would force
    the heartbeat call below to also see True regardless of the
    sentinel state we set up here -- masking a regression.  Reset
    before AND after to guarantee no leakage in either direction.
    """
    heartbeat_mod._reset_setup_complete_cache()
    yield
    heartbeat_mod._reset_setup_complete_cache()


@pytest.mark.django_db
class TestHeartbeatUsesOverriddenDir:
    """The env-var override is the load-bearing fix for issue #137.

    The E2E harness writes the sentinel into a per-test tmp dir and
    sets ``SETHLANS_MANAGER_DATA_DIR`` so the manager subprocess reads
    from that same dir.  This test reproduces that contract end-to-end
    inside the Django test client: set the env var, write the sentinel
    via ``create_sentinel``, hit ``/api/heartbeat/``, and assert the
    response reports ``manager_setup_complete: True``.

    If the production fix in ``_get_data_dir`` is reverted (the env-var
    branch deleted) this test MUST fail -- the heartbeat would read
    ``settings.BASE_DIR`` instead, find no sentinel there, and report
    False.
    """

    def test_heartbeat_uses_overridden_dir(
        self, monkeypatch, tmp_path,
    ):
        # Arrange: point the override at our tmp dir and write a
        # finalized sentinel there.
        monkeypatch.setenv(ENV_VAR, str(tmp_path))
        create_sentinel(tmp_path, 'manager', ['verified'])

        # Build a token-authed worker (heartbeat requires it).
        user = User.objects.create_user(username='worker_137')
        user.set_unusable_password()
        user.save()
        Worker.objects.create(
            hostname='unit137',
            user=user,
            is_active=True,
            available_tools={'blender': ['4.2.19']},
        )
        token = Token.objects.create(user=user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        # Act
        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'unit137'},
            format='json',
        )

        # Assert: the heartbeat saw the sentinel at the overridden
        # location and reports completion.
        assert resp.status_code == 200
        assert resp.data.get('manager_setup_complete') is True
