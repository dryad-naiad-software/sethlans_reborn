# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A normal ``run_manager`` start whose ``[paths] data_dir`` is a system
folder stops at once with exit 13 and one line on stderr, before it
makes certificates, a database or the folder itself.
"""
import os
import subprocess
import sys
from pathlib import Path

from .setup_process import REPO_ROOT, RUN_MANAGER, child_env

EXIT_DATA_FOLDER = 13
PREFIX = "[ERROR] The data folder cannot be used: "
SUFFIX = ". Run Reset setup to choose another."


def _system_folder() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SethlansTestData"
    return Path("/usr/sethlans-test-data")


class TestDataFolderStartIntegration:
    def test_system_data_folder_stops_the_start_with_exit_13(self, tmp_path):
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        target = _system_folder()
        (settings_dir / "sethlans.ini").write_text(f"[paths]\ndata_dir = {target}\n", encoding="utf-8")
        env = child_env(tmp_path, settings_dir, {"SETHLANS_SECURITY_DEBUG": "False"})

        out = subprocess.run(  # nosec B603 - fixed argv
            [sys.executable, str(RUN_MANAGER)], cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=120)

        assert out.returncode == EXIT_DATA_FOLDER, (out.stdout, out.stderr)
        errors = [line for line in out.stderr.splitlines() if line.startswith("[ERROR]")]
        assert len(errors) == 1, out.stderr
        assert errors[0].startswith(PREFIX) and errors[0].endswith(SUFFIX), errors[0]
        assert "Traceback" not in out.stderr
        assert not target.exists()
        # Nothing was made next to the settings file either (no TLS folder, no database).
        assert sorted(p.name for p in settings_dir.iterdir()) == ["sethlans.ini"]
