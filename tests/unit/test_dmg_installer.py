# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the macOS DMG build script: bundle paths and layout.

Covers GitHub issue #84: ``packaging/macos/build_dmg.sh`` looked for
``Sethlans.app`` inside the PyInstaller COLLECT directory
(``dist/launcher/``), but PyInstaller 6.x writes the ``BUNDLE(...)``
output to the canonical top-level location ``dist/Sethlans.app``. The
same fragile assumption applied to ``SethlansHelper.app``. These static
assertions lock the script to the canonical top-level ``.app`` paths
while preserving the intentional COLLECT-dir copy into
``Contents/Resources/bin/``.

Also covers #86 (the ``run_launcher`` -> ``sethlans`` rename and the
quarantine strip). The signing tests live in
``test_dmg_installer_codesign.py``.

Fixes #84.
"""

import re

import pytest

from tests.unit._dmg_installer_helpers import (
    DMG_SCRIPT,
    LAUNCHER_SPEC,
    OUTER_RESIGN_PATTERN,
    PLIST_TEMPLATE,
    RENAME_PATTERN,
    read_required,
)


@pytest.fixture(scope="module")
def dmg_contents() -> str:
    return read_required(DMG_SCRIPT, "DMG build script")


@pytest.fixture(scope="module")
def plist_contents() -> str:
    return read_required(PLIST_TEMPLATE, "Info.plist template")


@pytest.fixture(scope="module")
def launcher_spec_contents() -> str:
    return read_required(LAUNCHER_SPEC, "launcher.spec")


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


class TestDmgInstallerBundleExecutable:
    """Static assertions guarding the #86 fix in build_dmg.sh.

    Info.plist.template declares ``CFBundleExecutable=sethlans`` but
    ``launcher.spec`` names the PyInstaller EXE ``run_launcher`` (shared
    with the Windows build). After PyInstaller writes the bundle, the
    binary at ``Contents/MacOS/run_launcher`` does not match the plist
    key — macOS Launch Services reports the app as "damaged or
    incomplete". ``build_dmg.sh`` must rename the binary to ``sethlans``
    inside the staged bundle, BEFORE the ad-hoc re-sign (codesign hashes
    file names, so renaming post-sign would break the seal).

    Also guards the companion ``xattr -cr`` hygiene pass that strips
    inherited ``com.apple.quarantine`` attributes.

    Fixes #86.
    """

    def test_info_plist_declares_sethlans_as_executable(self, plist_contents: str) -> None:
        # The user-visible binary name is `sethlans`. If this ever
        # changes, the rename in build_dmg.sh must be updated to match.
        pattern = re.compile(
            r'<key>CFBundleExecutable</key>\s*<string>sethlans</string>',
        )
        assert pattern.search(plist_contents), (
            "Expected Info.plist.template to declare "
            "<key>CFBundleExecutable</key><string>sethlans</string>. "
            "Drift here would desync the #86 fix in build_dmg.sh."
        )

    def test_launcher_spec_exe_name_is_run_launcher(self, launcher_spec_contents: str) -> None:
        # launcher.spec is shared with Windows — the EXE name is
        # `run_launcher`. The macOS DMG rename depends on this.
        pattern = re.compile(r"name\s*=\s*['\"]run_launcher['\"]")
        assert pattern.search(launcher_spec_contents), (
            "Expected launcher.spec to contain `name='run_launcher'` in "
            "its EXE(...) call. The macOS DMG rename in build_dmg.sh "
            "assumes this name (issue #86)."
        )

    def test_dmg_script_renames_run_launcher_to_sethlans(self, dmg_contents: str) -> None:
        # Regex-match the mv of ${MACOS_DIR}/run_launcher to
        # ${MACOS_DIR}/sethlans. Tolerate whitespace + line continuations.
        assert RENAME_PATTERN.search(dmg_contents), (
            "Expected build_dmg.sh to rename the PyInstaller binary with "
            '`mv "${MACOS_DIR}/run_launcher" "${MACOS_DIR}/sethlans"` so '
            "CFBundleExecutable resolves (issue #86)."
        )

    def test_rename_happens_before_outer_resign(self, dmg_contents: str) -> None:
        # The rename must come BEFORE the outer ad-hoc re-sign. codesign
        # hashes file names into the seal, so renaming after signing
        # would invalidate the signature and codesign --verify would
        # abort the build. The outer re-sign passes no `--deep` (#149).
        rename_match = RENAME_PATTERN.search(dmg_contents)
        resign_match = OUTER_RESIGN_PATTERN.search(dmg_contents)
        assert rename_match is not None, (
            "Expected the run_launcher -> sethlans rename to be present."
        )
        assert resign_match is not None, (
            "Expected the outer-bundle re-sign line to be present."
        )
        assert rename_match.start() < resign_match.start(), (
            "The run_launcher -> sethlans rename MUST happen before the "
            "outer `codesign ... ${STAGING_DIR}/${APP_NAME}` re-sign; "
            "codesign hashes file names, so a post-sign rename breaks "
            "the seal and the verify gate would fail (issue #86)."
        )

    def test_rename_happens_after_bundle_copy_into_staging(self, dmg_contents: str) -> None:
        # Can't rename a file before it exists — the mv must come AFTER
        # the `cp -R "${DIST_DIR}/${APP_NAME}" "${STAGING_DIR}/${APP_NAME}"`
        # that populates the staged bundle.
        cp_marker = 'cp -R "${DIST_DIR}/${APP_NAME}" "${STAGING_DIR}/${APP_NAME}"'
        assert cp_marker in dmg_contents, (
            f"Expected marker {cp_marker!r} to be present as a sanity check."
        )
        rename_match = RENAME_PATTERN.search(dmg_contents)
        assert rename_match is not None, (
            "Expected the run_launcher -> sethlans rename to be present."
        )
        cp_index = dmg_contents.index(cp_marker)
        assert rename_match.start() > cp_index, (
            "The run_launcher -> sethlans rename must appear AFTER the "
            "`cp -R ${DIST_DIR}/${APP_NAME} ${STAGING_DIR}/${APP_NAME}` "
            "line; the binary doesn't exist in staging before the copy "
            "(issue #86)."
        )

    def test_dmg_script_strips_quarantine_xattrs(self, dmg_contents: str) -> None:
        # Regex-match `xattr -cr "${STAGING_DIR}/${APP_NAME}"`. The
        # trailing `|| true` is not required for the assertion to hold
        # but is part of the defensive pattern.
        pattern = re.compile(
            r'xattr\s+-cr\s+"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        assert pattern.search(dmg_contents), (
            "Expected build_dmg.sh to strip inherited quarantine xattrs "
            'with `xattr -cr "${STAGING_DIR}/${APP_NAME}"` as pre-sign '
            "hygiene (issue #86)."
        )

    def test_xattr_strip_happens_before_outer_resign(self, dmg_contents: str) -> None:
        # The xattr strip must run BEFORE the outer re-sign so the
        # pre-sign state is the clean bundle state. Running it after
        # signing is logically confused (xattrs don't affect the
        # signature itself, but we want the hygiene pass co-located
        # with the rest of the pre-sign bundle setup).
        xattr_pattern = re.compile(
            r'xattr\s+-cr\s+"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        # The outer re-sign passes no `--deep` (issue #149).
        xattr_match = xattr_pattern.search(dmg_contents)
        resign_match = OUTER_RESIGN_PATTERN.search(dmg_contents)
        assert xattr_match is not None, (
            "Expected the `xattr -cr` hygiene line to be present."
        )
        assert resign_match is not None, (
            "Expected the outer-bundle re-sign line to be present."
        )
        assert xattr_match.start() < resign_match.start(), (
            "`xattr -cr` must run BEFORE the outer-bundle re-sign so "
            "pre-sign bundle hygiene is co-located (issue #86)."
        )
