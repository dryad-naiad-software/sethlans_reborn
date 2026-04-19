# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the macOS DMG build script.

Covers GitHub issue #84: ``packaging/macos/build_dmg.sh`` looked for
``Sethlans.app`` inside the PyInstaller COLLECT directory
(``dist/launcher/``), but PyInstaller 6.x writes the ``BUNDLE(...)``
output to the canonical top-level location ``dist/Sethlans.app``. The
same fragile assumption applied to ``SethlansHelper.app``. These static
assertions lock the script to the canonical top-level ``.app`` paths
while preserving the intentional COLLECT-dir copy into
``Contents/Resources/bin/``.

Fixes #84.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DMG_SCRIPT = REPO_ROOT / "packaging" / "macos" / "build_dmg.sh"


@pytest.fixture(scope="module")
def dmg_contents() -> str:
    assert DMG_SCRIPT.is_file(), f"Expected DMG build script at {DMG_SCRIPT}"
    return DMG_SCRIPT.read_text(encoding="utf-8")


class TestDmgInstallerRegression:
    """Static assertions guarding the #84 fix in build_dmg.sh."""

    def test_launcher_app_source_is_canonical_bundle_path(self, dmg_contents: str) -> None:
        # The canonical BUNDLE output lives at dist/Sethlans.app.
        assert '"${DIST_DIR}/${APP_NAME}"' in dmg_contents, (
            'Expected the script to reference "${DIST_DIR}/${APP_NAME}" '
            "(the canonical top-level PyInstaller BUNDLE location)."
        )
        # The orphan path under the COLLECT dir must not reappear.
        assert '"${DIST_DIR}/launcher/${APP_NAME}"' not in dmg_contents, (
            "Regression: build_dmg.sh references ${DIST_DIR}/launcher/${APP_NAME}, "
            "but PyInstaller writes Sethlans.app to ${DIST_DIR}/${APP_NAME} (issue #84)."
        )

    def test_tray_helper_app_source_is_canonical_bundle_path(self, dmg_contents: str) -> None:
        assert '"${DIST_DIR}/SethlansHelper.app"' in dmg_contents, (
            'Expected the tray helper copy to reference "${DIST_DIR}/SethlansHelper.app" '
            "(the canonical top-level PyInstaller BUNDLE location)."
        )
        assert '"${DIST_DIR}/tray_helper/SethlansHelper.app"' not in dmg_contents, (
            "Regression: build_dmg.sh references ${DIST_DIR}/tray_helper/SethlansHelper.app, "
            "but the canonical BUNDLE location is ${DIST_DIR}/SethlansHelper.app (issue #84)."
        )

    def test_collect_dirs_still_copied_into_resources(self, dmg_contents: str) -> None:
        # The `for component in manager worker tray_helper` loop copies the COLLECT dirs
        # (dist/manager, dist/worker, dist/tray_helper) into the .app's Resources/bin/.
        # This is intentional and must not be "aligned away" by a future refactor.
        assert "for component in manager worker tray_helper" in dmg_contents, (
            "Expected the loop iterating over COLLECT dirs "
            "`for component in manager worker tray_helper`."
        )
        assert '"${DIST_DIR}/${component}"' in dmg_contents, (
            'Expected the loop to reference "${DIST_DIR}/${component}" as the source.'
        )
        assert '"${RESOURCES}/bin/${component}"' in dmg_contents, (
            'Expected the loop to copy into "${RESOURCES}/bin/${component}".'
        )

        cp_pattern = re.compile(
            r'cp\s+-R\s+"\$\{DIST_DIR\}/\$\{component\}"\s+'
            r'"\$\{RESOURCES\}/bin/\$\{component\}"',
        )
        assert cp_pattern.search(dmg_contents), (
            "Expected the COLLECT-dir copy `cp -R \"${DIST_DIR}/${component}\" "
            "\"${RESOURCES}/bin/${component}\"` loop body to remain intact."
        )

    def test_error_message_mentions_canonical_path(self, dmg_contents: str) -> None:
        # The "[ERROR] ... not found at ..." message must name the canonical path
        # so a broken check and a misleading error don't drift apart.
        error_pattern = re.compile(
            r'\[ERROR\][^\n]*not found at\s+\$\{DIST_DIR\}/\$\{APP_NAME\}',
        )
        assert error_pattern.search(dmg_contents), (
            "Expected the not-found error message to reference "
            "${DIST_DIR}/${APP_NAME} (the canonical bundle location)."
        )
        assert '${DIST_DIR}/launcher/${APP_NAME}' not in dmg_contents, (
            "Regression: error message still references the orphan "
            "${DIST_DIR}/launcher/${APP_NAME} path."
        )
