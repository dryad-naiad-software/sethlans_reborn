# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Static checks on the smoke steps of ``build-installers.yml``.

Issue #236: on macOS, ``shared.frozen_paths.get_install_root()`` only
accepts the ``.app`` layouts from ``packaging/macos/build_dmg.sh``
(``Contents/MacOS/sethlans`` for the launcher,
``Contents/Resources/bin/<component>/run_<component>`` for the rest).
The macOS smoke tests ran the raw PyInstaller output in
``dist/<component>/``, so the frozen manager raised "unrecognized macOS
layout" and every macOS installer build failed. These tests pin the
smoke steps to the bundle installed from the DMG.

Fixes #236.

Setup-rebuild chunk 7a replaced the wizard smoke with
``tools/setup_smoke.py`` ("Smoke test — Setup mode", 3-minute step
timeout) on all three OSes and took the wizard bundle out of the build.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-installers.yml"

SMOKE_PREFIX = "Smoke test"
SETUP_SMOKE_STEP = "Smoke test — Setup mode"
INSTALL_STEP = "Install DMG for smoke tests"
BUILD_DMG_STEP = "Build DMG"
NOTARIZE_DMG_STEP = "Notarize DMG"
BUILD_JOBS = ("build-windows", "build-macos", "build-linux")

# A component executable or bundle run straight out of the flat
# PyInstaller output, e.g. ``dist/manager/run_manager`` or
# ``--bin-root dist``. ``--launcher-dir dist/launcher`` is no run: the
# setup smoke only measures that folder's size.
_FLAT_DIST_RE = re.compile(
    r"(?<!--launcher-dir )\bdist(?:/(?:launcher|manager|worker|tray_helper)\b|(?=[\s\"']|$))"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    if not WORKFLOW.is_file():
        # The Docker test image excludes .github/ (.dockerignore); the
        # host CI matrix, which has the checkout, runs these checks.
        pytest.skip(f"workflow not present at {WORKFLOW}")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def macos_steps(workflow) -> list[dict]:
    return workflow["jobs"]["build-macos"]["steps"]


def _names(steps: list[dict]) -> list[str]:
    return [step.get("name", "") for step in steps]


def _step(steps: list[dict], name: str) -> dict:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"job has no step named {name!r}")


def _smoke_steps(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s.get("name", "").startswith(SMOKE_PREFIX)]


class TestMacosSmokeLayout:
    """The macOS smoke steps must run from the DMG's ``.app`` layout."""

    def test_expected_smoke_steps_present(self, macos_steps):
        names = _names(_smoke_steps(macos_steps))
        assert names == [
            "Smoke test — Launcher --version",
            "Smoke test — Manager starts",
            SETUP_SMOKE_STEP,
        ]

    def test_no_smoke_step_runs_flat_dist_output(self, macos_steps):
        for step in _smoke_steps(macos_steps):
            match = _FLAT_DIST_RE.search(step["run"])
            assert match is None, (
                f"{step['name']!r} runs {match.group(0)!r}; on macOS the "
                "frozen components only start from the .app layout (#236)"
            )

    def test_smoke_steps_run_after_dmg_install(self, macos_steps):
        names = _names(macos_steps)
        build = names.index(BUILD_DMG_STEP)
        install = names.index(INSTALL_STEP)
        notarize = names.index(NOTARIZE_DMG_STEP)
        assert build < install < notarize
        for step in _smoke_steps(macos_steps):
            assert install < names.index(step["name"]) < notarize, (
                f"{step['name']!r} must run between {INSTALL_STEP!r} and "
                f"{NOTARIZE_DMG_STEP!r}"
            )

    def test_install_step_copies_app_out_of_the_dmg(self, macos_steps):
        run = _step(macos_steps, INSTALL_STEP)["run"]
        assert "dist/sethlans-${BUILD_VERSION}-macos-arm64.dmg" in run
        assert "hdiutil attach" in run
        assert re.search(r'ditto\s+"\$\{MOUNT_DIR\}/Sethlans\.app"', run)
        assert 'SMOKE_BIN="${SMOKE_APP}/Contents/Resources/bin"' in run
        assert 'echo "SMOKE_APP=' in run and 'echo "SMOKE_BIN=' in run

    def test_launcher_runs_from_app_macos_dir(self, macos_steps):
        run = _step(macos_steps, "Smoke test — Launcher --version")["run"]
        assert '"${SMOKE_APP}/Contents/MacOS/sethlans" --version' in run

    def test_manager_runs_from_app_resources_bin(self, macos_steps):
        run = _step(macos_steps, "Smoke test — Manager starts")["run"]
        assert '"${SMOKE_BIN}/manager/run_manager" &' in run

    def test_setup_smoke_runs_from_app_resources_bin(self, macos_steps):
        """#236: the setup smoke runs the components installed from the DMG."""
        run = _step(macos_steps, SETUP_SMOKE_STEP)["run"]
        assert 'tools/setup_smoke.py --bin-root "${SMOKE_BIN}"' in run


class TestSetupSmokeSteps:
    """Chunk 7a: the setup-mode smoke replaces the wizard smoke on every OS."""

    @pytest.mark.parametrize("job", BUILD_JOBS)
    def test_each_job_runs_the_setup_smoke_with_a_step_timeout(self, workflow, job):
        """Chunk 7a smoke steps: every OS runs setup_smoke.py, bounded by a 3-minute step timeout."""
        step = _step(workflow["jobs"][job]["steps"], SETUP_SMOKE_STEP)
        assert "python tools/setup_smoke.py --bin-root " in step["run"]
        assert step["timeout-minutes"] == 3

    def test_no_wizard_left_in_the_workflow(self, workflow):
        """Chunk 7a: wizard.spec and its requirements are gone, so a wizard build or smoke step fails CI."""
        text = WORKFLOW.read_text(encoding="utf-8").lower()
        assert "wizard" not in text, (
            "build-installers.yml still builds, installs or smokes the old wizard"
        )
