# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Seeding and assertion helpers for the clean-script tests (issue #315)."""

from pathlib import Path

REFUSAL_PREFIX = "[ERROR] Refusing to remove the per-user data folder: "
NOTHING_REMOVED = "Nothing was removed."

# Repo artifacts the fake project starts with, all removed by a clean that runs.
REPO_ARTIFACTS = (
    "manager/db.sqlite3",
    "manager/sethlans.ini",
    "manager/media/assets/test/foo.blend",
    "manager/staticfiles",
    "manager/logs",
    "worker/sethlans_worker_agent/managed_tools",
    "worker/sethlans_worker_agent/managed_assets",
)

KEEP_HINT = {
    "ps1": r".\tools\sethlans.ps1 clean -All",
    "sh": "bash tools/sethlans.sh clean --all",
}


def output(result):
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def assert_exit(result, code):
    assert result.returncode == code, output(result)


def present_artifacts(project_root: Path):
    return [rel for rel in REPO_ARTIFACTS if (project_root / rel).exists()]


def assert_artifacts_removed(project_root: Path):
    assert present_artifacts(project_root) == [], "clean should remove the repo artifacts"


def assert_artifacts_untouched(project_root: Path):
    assert present_artifacts(project_root) == list(REPO_ARTIFACTS), (
        "a refused or rejected clean must not remove anything"
    )


def seed_identity_files(data_dir: Path):
    """Stand-ins for the real identity files ``clean --all`` would wipe."""
    (data_dir / "manager").mkdir(parents=True)
    (data_dir / "manager" / "secret_key").write_text("fake secret")
    (data_dir / "worker").mkdir()
    (data_dir / "worker" / "config.json").write_text('{"api_token": "fake"}')


def assert_identity_files_kept(data_dir: Path):
    assert (data_dir / "manager" / "secret_key").read_text() == "fake secret"
    assert (data_dir / "worker" / "config.json").read_text() == '{"api_token": "fake"}'


def seed_sibling_marker(data_dir: Path) -> Path:
    marker = data_dir.parent / "UnrelatedApp" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep me")
    return marker


def assert_refused(result, project_root: Path, reason: str):
    """Exit 1, the refusal message with ``reason``, and nothing removed."""
    assert_exit(result, 1)
    lines = [line.strip() for line in result.stdout.splitlines()]
    assert REFUSAL_PREFIX + reason in lines, output(result)
    refusal_at = lines.index(REFUSAL_PREFIX + reason)
    assert lines[refusal_at + 1] == NOTHING_REMOVED, output(result)
    assert "Sethlans Reborn -- Clean" not in result.stdout, "the refusal must come before the clean starts"
    assert_artifacts_untouched(project_root)


def assert_no_folder_message(result):
    assert "Shared data dir" not in result.stdout, output(result)
