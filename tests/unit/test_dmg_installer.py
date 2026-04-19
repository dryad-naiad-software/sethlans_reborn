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


class TestDmgInstallerCodesign:
    """Static assertions guarding the #85 fix in build_dmg.sh.

    PyInstaller 6.19 ad-hoc signs the Sethlans.app bundle at build time.
    Overwriting Contents/Info.plist from the template invalidates and
    strips that signature, so Gatekeeper reports the app as "damaged".
    The script must re-sign the bundle after the plist edit and verify
    the signature before hdiutil wraps it into a DMG.

    Fixes #85.
    """

    def test_resigns_bundle_after_plist_edit(self, dmg_contents: str) -> None:
        # Match `codesign --force --deep --sign -` targeting the staged .app.
        # Tolerates arbitrary whitespace between flags and across line
        # continuations.
        pattern = re.compile(
            r'codesign\s+--force\s+--deep\s+--sign\s+-\s+'
            r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        assert pattern.search(dmg_contents), (
            "Expected the script to re-sign the staged bundle with "
            '`codesign --force --deep --sign - "${STAGING_DIR}/${APP_NAME}"` '
            "after the Info.plist template overwrite (issue #85)."
        )

    def test_resign_happens_after_plist_template_write(self, dmg_contents: str) -> None:
        # The re-sign must come AFTER the plist edit — signing before the edit
        # is pointless because the edit would strip the signature again.
        plist_marker = 'PLIST_TEMPLATE="${SCRIPT_DIR}/Info.plist.template"'
        resign_marker_pattern = re.compile(
            r'codesign\s+--force\s+--deep\s+--sign\s+-\s+'
            r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )

        assert plist_marker in dmg_contents, (
            "Expected the Info.plist template block to be present "
            "(sanity check — the fix depends on its location)."
        )
        resign_match = resign_marker_pattern.search(dmg_contents)
        assert resign_match is not None, (
            "Expected the outer-bundle re-sign line to be present."
        )

        plist_index = dmg_contents.index(plist_marker)
        assert resign_match.start() > plist_index, (
            "The `codesign ... ${STAGING_DIR}/${APP_NAME}` re-sign must "
            "appear AFTER the Info.plist template write; signing before "
            "the plist edit leaves the bundle unsigned (issue #85)."
        )

    def test_resigns_nested_helper_bundle(self, dmg_contents: str) -> None:
        # The nested SethlansHelper.app inside Resources/bin/tray_helper/ must
        # also be re-signed defensively — cp -R doesn't always preserve the
        # ad-hoc signature cleanly, and the outer --deep re-sign needs a valid
        # seal on every nested bundle.
        pattern = re.compile(
            r'codesign\s+--force\s+--deep\s+--sign\s+-\s+\\?\s*'
            r'"\$\{RESOURCES\}/bin/tray_helper/SethlansHelper\.app"',
        )
        assert pattern.search(dmg_contents), (
            "Expected the script to re-sign the nested helper at "
            '"${RESOURCES}/bin/tray_helper/SethlansHelper.app" with '
            "`codesign --force --deep --sign -` (issue #85)."
        )

    def test_resign_happens_after_all_contents_mutations(self, dmg_contents: str) -> None:
        # The outer-bundle re-sign must come AFTER every write into the
        # bundle's Contents/ tree — plist template, sethlans.icns copy,
        # version.json heredoc. A re-sign placed before any of those
        # writes gets immediately invalidated by the subsequent write,
        # and codesign --verify (issue #85) would then fail with set -e.
        resign_pattern = re.compile(
            r'codesign\s+--force\s+--deep\s+--sign\s+-\s+'
            r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        resign_match = resign_pattern.search(dmg_contents)
        assert resign_match is not None, (
            "Expected the outer-bundle re-sign line to be present."
        )

        icon_marker = 'cp "${SCRIPT_DIR}/sethlans.icns" "${RESOURCES}/sethlans.icns"'
        version_marker = 'cat > "${RESOURCES}/version.json"'
        for marker in (icon_marker, version_marker):
            assert marker in dmg_contents, (
                f"Expected marker {marker!r} to be present as a sanity check."
            )
            idx = dmg_contents.index(marker)
            assert resign_match.start() > idx, (
                f"The outer-bundle re-sign must appear AFTER {marker!r}. "
                "Post-sign writes into Contents/ invalidate the signature "
                "(issue #85)."
            )

    def test_verifies_bundle_signature_before_hdiutil(self, dmg_contents: str) -> None:
        # A `codesign --verify --deep --strict` must run, and it must run
        # BEFORE `hdiutil create` so a broken signature aborts the build
        # instead of shipping a damaged DMG.
        verify_pattern = re.compile(
            r'codesign\s+--verify\s+--deep\s+--strict\s+'
            r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        verify_match = verify_pattern.search(dmg_contents)
        assert verify_match is not None, (
            "Expected `codesign --verify --deep --strict "
            '"${STAGING_DIR}/${APP_NAME}"` to guard the DMG build (issue #85).'
        )

        hdiutil_index = dmg_contents.index("hdiutil create")
        assert verify_match.start() < hdiutil_index, (
            "The `codesign --verify` check must run BEFORE `hdiutil create`; "
            "otherwise a broken signature would still be wrapped into the DMG "
            "(issue #85)."
        )
