# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for ``tools/dev_launcher.py`` env propagation (issue #181).

The dev-launcher harness sets ``SETHLANS_DATA_DIR`` so the launcher
subprocess resolves to ``temp/dev-data/launcher/`` instead of the
user's real ``%LOCALAPPDATA%\\Sethlans``. These tests pin the env-var
contract — if the harness drifts (renames the var, drops it, builds it
relative), the launcher's data dir contaminates real install state.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_dev_launcher():
    """Import (or re-import) ``tools.dev_launcher`` for inspection."""
    return importlib.import_module("tools.dev_launcher")


class TestDevLauncherEnv:
    """Issue #181 contract: ``_build_env`` sets the canonical env vars.

    The launcher's :func:`launcher.paths.get_data_dir` honors
    ``SETHLANS_DATA_DIR`` — the harness must set the same key. Other
    keys (per-component, IPC secret) are pinned to catch silent
    renames.
    """

    def test_build_env_sets_shared_data_dir(self, tmp_path):
        dev_launcher = _import_dev_launcher()
        env = dev_launcher._build_env(
            tmp_path, ipc_secret="abc", log_level=None,
        )
        assert env["SETHLANS_DATA_DIR"] == str(tmp_path.resolve())

    def test_build_env_sets_per_component_data_dirs(self, tmp_path):
        dev_launcher = _import_dev_launcher()
        env = dev_launcher._build_env(
            tmp_path, ipc_secret="abc", log_level=None,
        )
        assert (
            env["SETHLANS_MANAGER_DATA_DIR"]
            == str((tmp_path / "manager").resolve())
        )
        assert (
            env["SETHLANS_WORKER_DATA_DIR"]
            == str((tmp_path / "worker").resolve())
        )

    def test_build_env_propagates_ipc_secret(self, tmp_path):
        dev_launcher = _import_dev_launcher()
        env = dev_launcher._build_env(
            tmp_path, ipc_secret="the-secret", log_level=None,
        )
        assert env["SETHLANS_TRAY_IPC_SECRET"] == "the-secret"

    def test_build_env_log_level_optional(self, tmp_path):
        dev_launcher = _import_dev_launcher()
        env = dev_launcher._build_env(
            tmp_path, ipc_secret="x", log_level=None,
        )
        assert "SETHLANS_LOG_LEVEL" not in env
        env2 = dev_launcher._build_env(
            tmp_path, ipc_secret="x", log_level="DEBUG",
        )
        assert env2["SETHLANS_LOG_LEVEL"] == "DEBUG"


class TestDevLauncherLauncherIntegration:
    """End-to-end: dev_launcher's env vars feed launcher.paths.get_data_dir.

    Bridges the harness contract to the production resolver — if either
    side drifts, this test catches it without spawning a subprocess.
    """

    def test_launcher_resolves_to_dev_data_root(self, tmp_path, monkeypatch):
        dev_launcher = _import_dev_launcher()
        # Pretend the harness selected ``tmp_path`` as the dev root.
        env = dev_launcher._build_env(
            tmp_path, ipc_secret="x", log_level=None,
        )
        # Apply the harness's env vars to this process; production
        # would apply them to the launcher subprocess via
        # ``stream_subprocess(..., env=env)``.
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        # Re-import launcher.paths so any cached lookup picks up env;
        # ``get_data_dir`` reads ``os.environ`` live so this is a
        # belt-and-braces import.
        from launcher.paths import get_data_dir
        resolved = get_data_dir()
        assert resolved == tmp_path.resolve()
