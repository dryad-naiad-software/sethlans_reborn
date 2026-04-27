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
PLIST_TEMPLATE = REPO_ROOT / "packaging" / "macos" / "Info.plist.template"
LAUNCHER_SPEC = REPO_ROOT / "packaging" / "pyinstaller" / "launcher.spec"


@pytest.fixture(scope="module")
def dmg_contents() -> str:
    assert DMG_SCRIPT.is_file(), f"Expected DMG build script at {DMG_SCRIPT}"
    return DMG_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def plist_contents() -> str:
    assert PLIST_TEMPLATE.is_file(), f"Expected Info.plist template at {PLIST_TEMPLATE}"
    return PLIST_TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def launcher_spec_contents() -> str:
    assert LAUNCHER_SPEC.is_file(), f"Expected launcher.spec at {LAUNCHER_SPEC}"
    return LAUNCHER_SPEC.read_text(encoding="utf-8")


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

    # Pattern matching the OUTER-bundle re-sign WITHOUT --deep.
    # Apple deprecated `codesign --deep` for signing in macOS 11 and
    # notarytool will reject `--deep`-signed artifacts (issue #149). The
    # script now uses inside-out signing instead: nested Mach-O first,
    # then component executables, then the outer bundle last — each
    # without `--deep`.
    OUTER_RESIGN_PATTERN = re.compile(
        r'codesign\s+--force\s+--sign\s+-\s+'
        r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
    )

    def test_resigns_bundle_after_plist_edit(self, dmg_contents: str) -> None:
        # Match `codesign --force --sign -` targeting the staged .app.
        # `--deep` is intentionally absent — see OUTER_RESIGN_PATTERN.
        assert self.OUTER_RESIGN_PATTERN.search(dmg_contents), (
            "Expected the script to re-sign the staged bundle with "
            '`codesign --force --sign - "${STAGING_DIR}/${APP_NAME}"` '
            "(no --deep) after the Info.plist template overwrite (issue #85)."
        )

    def test_resign_happens_after_plist_template_write(self, dmg_contents: str) -> None:
        # The re-sign must come AFTER the plist edit — signing before the edit
        # is pointless because the edit would strip the signature again.
        plist_marker = 'PLIST_TEMPLATE="${SCRIPT_DIR}/Info.plist.template"'

        assert plist_marker in dmg_contents, (
            "Expected the Info.plist template block to be present "
            "(sanity check — the fix depends on its location)."
        )
        resign_match = self.OUTER_RESIGN_PATTERN.search(dmg_contents)
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
        # ad-hoc signature cleanly. Inside-out signing means the helper
        # bundle is signed without `--deep` (issue #149); its nested
        # Mach-O binaries and main executable are signed individually
        # before the helper bundle itself is signed.
        helper_bundle_pattern = re.compile(
            r'codesign\s+--force\s+--sign\s+-\s+\\?\s*'
            r'"\$\{HELPER_BUNDLE\}"',
        )
        assert helper_bundle_pattern.search(dmg_contents), (
            "Expected the script to re-sign the nested helper bundle "
            '"${HELPER_BUNDLE}" with `codesign --force --sign -` (no --deep) '
            "after signing its nested binaries (issue #85, #149)."
        )

        # Helper executable must be signed individually (inside-out) so
        # the helper bundle's signature seals a valid inner signature
        # rather than relying on deprecated --deep traversal.
        helper_exe_pattern = re.compile(
            r'codesign\s+--force\s+--sign\s+-\s+'
            r'"\$\{HELPER_EXE\}"',
        )
        assert helper_exe_pattern.search(dmg_contents), (
            "Expected the script to sign the helper main executable "
            '"${HELPER_EXE}" individually (inside-out signing) before '
            "signing the helper bundle (issue #149)."
        )

        # Helper's nested Mach-O (.dylib/.so) must be signed individually
        # via a `find ... -exec codesign` line targeting ${HELPER_BUNDLE}.
        # The find command spans two lines via `\` continuation.
        helper_find_pattern = re.compile(
            r'find\s+"\$\{HELPER_BUNDLE\}"\s+-type\s+f\s+\\\(\s*'
            r'-name\s+"\*\.dylib"\s+-o\s+-name\s+"\*\.so"\s*\\\)\s*\\?\s*'
            r'-exec\s+codesign\s+--force\s+--sign\s+-\s+\{\}\s+\+',
            re.DOTALL,
        )
        assert helper_find_pattern.search(dmg_contents), (
            "Expected a `find ${HELPER_BUNDLE} ... -exec codesign --force "
            "--sign -` line that signs nested .dylib/.so binaries inside "
            "the helper before the helper bundle itself (issue #149)."
        )

    def test_resign_happens_after_all_contents_mutations(self, dmg_contents: str) -> None:
        # The outer-bundle re-sign must come AFTER every write into the
        # bundle's Contents/ tree — plist template, sethlans.icns copy,
        # version.json heredoc. A re-sign placed before any of those
        # writes gets immediately invalidated by the subsequent write,
        # and codesign --verify (issue #85) would then fail with set -e.
        resign_match = self.OUTER_RESIGN_PATTERN.search(dmg_contents)
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

    def test_signs_nested_macho_binaries_first(self, dmg_contents: str) -> None:
        # Issue #149: the inside-out signing pattern requires every nested
        # Mach-O binary (.dylib, .so) to be signed BEFORE the outer
        # bundle. The script does this with a single `find ... -exec
        # codesign` over the staged .app. Tolerates the `\` line
        # continuation between the predicate and the -exec clause.
        find_pattern = re.compile(
            r'find\s+"\$\{STAGING_DIR\}/\$\{APP_NAME\}"\s+-type\s+f\s+\\\(\s*'
            r'-name\s+"\*\.dylib"\s+-o\s+-name\s+"\*\.so"\s*\\\)\s*\\?\s*'
            r'-exec\s+codesign\s+--force\s+--sign\s+-\s+\{\}\s+\+',
            re.DOTALL,
        )
        assert find_pattern.search(dmg_contents), (
            "Expected a `find ${STAGING_DIR}/${APP_NAME} -type f "
            '\\( -name "*.dylib" -o -name "*.so" \\) -exec codesign '
            "--force --sign - {} +` line to sign every nested Mach-O "
            "binary before the outer bundle (issue #149)."
        )

        # The nested-binary signing must happen BEFORE the outer-bundle
        # re-sign — inside-out order.
        find_match = find_pattern.search(dmg_contents)
        outer_match = self.OUTER_RESIGN_PATTERN.search(dmg_contents)
        assert find_match is not None and outer_match is not None
        assert find_match.start() < outer_match.start(), (
            "Nested Mach-O signing must run BEFORE the outer-bundle "
            "re-sign (inside-out order, issue #149)."
        )

    def test_signs_each_component_executable(self, dmg_contents: str) -> None:
        # Issue #149 inside-out signing: each PyInstaller-frozen component
        # launcher (wizard, manager, worker, launcher) must be signed
        # individually before the outer bundle. The script uses a `for
        # component in wizard manager worker launcher` loop.
        loop_pattern = re.compile(
            r'for\s+component\s+in\s+wizard\s+manager\s+worker\s+launcher',
        )
        assert loop_pattern.search(dmg_contents), (
            "Expected a `for component in wizard manager worker launcher` "
            "loop that signs each component executable individually "
            "(issue #149 inside-out signing)."
        )

        # The loop body must invoke `codesign --force --sign -` on
        # ${RESOURCES}/bin/${component}/run_${component}.
        component_sign_pattern = re.compile(
            r'codesign\s+--force\s+--sign\s+-\s+"\$bin"',
        )
        assert component_sign_pattern.search(dmg_contents), (
            "Expected `codesign --force --sign - \"$bin\"` inside the "
            "component-signing loop (issue #149)."
        )

    def test_outer_bundle_signed_last(self, dmg_contents: str) -> None:
        # Issue #149: the outer bundle re-sign MUST be the last
        # `codesign --sign` line in the script (excluding `codesign
        # --verify`). Signing the outer bundle re-seals references to
        # every nested signature, so any signing call placed after it
        # would invalidate the outer seal.
        outer_match = self.OUTER_RESIGN_PATTERN.search(dmg_contents)
        assert outer_match is not None, (
            "Expected the outer-bundle re-sign line to be present."
        )

        # Find every `codesign --force --sign -` occurrence; the outer
        # bundle one must be the last.
        sign_calls = list(re.finditer(
            r'codesign\s+--force\s+--sign\s+-',
            dmg_contents,
        ))
        assert sign_calls, "Expected at least one `codesign --force --sign -` call."
        last_sign = sign_calls[-1]
        assert last_sign.start() == outer_match.start(), (
            "The outer-bundle `codesign --force --sign - "
            '"${STAGING_DIR}/${APP_NAME}"` must be the LAST signing '
            "call in the script. Inside-out signing requires the parent "
            "bundle to be sealed after every nested signature is in "
            "place (issue #149)."
        )

    def test_no_deep_flag_on_any_signing_call(self, dmg_contents: str) -> None:
        # Issue #149 drift guard: Apple deprecated `codesign --deep` for
        # signing in macOS 11; notarytool rejects `--deep`-signed
        # artifacts. The build_dmg.sh script must never pass `--deep`
        # to a signing call. `--deep` is only acceptable on `codesign
        # --verify` (Apple still documents it for verification).
        for raw_line in dmg_contents.splitlines():
            stripped = raw_line.lstrip()
            # Ignore shell comment lines — they explain the rule, they
            # don't invoke codesign.
            if stripped.startswith('#'):
                continue
            if 'codesign' not in raw_line:
                continue
            if '--verify' in raw_line:
                # `--deep` is allowed on verify only (Apple still
                # documents it for verification).
                continue
            assert '--deep' not in raw_line, (
                f"Regression: `codesign --deep` used on a non-verify call:\n"
                f"  {raw_line.strip()}\n"
                "Apple deprecated `--deep` for signing; notarytool will "
                "reject it. Use inside-out signing instead (issue #149)."
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
        pattern = re.compile(
            r'mv\s+\\?\s*'
            r'"\$\{MACOS_DIR\}/run_launcher"\s+\\?\s*'
            r'"\$\{MACOS_DIR\}/sethlans"',
        )
        assert pattern.search(dmg_contents), (
            "Expected build_dmg.sh to rename the PyInstaller binary with "
            '`mv "${MACOS_DIR}/run_launcher" "${MACOS_DIR}/sethlans"` so '
            "CFBundleExecutable resolves (issue #86)."
        )

    def test_rename_happens_before_outer_resign(self, dmg_contents: str) -> None:
        # The rename must come BEFORE the outer ad-hoc re-sign. codesign
        # hashes file names into the seal, so renaming after signing
        # would invalidate the signature and codesign --verify would
        # abort the build.
        rename_pattern = re.compile(
            r'mv\s+\\?\s*'
            r'"\$\{MACOS_DIR\}/run_launcher"\s+\\?\s*'
            r'"\$\{MACOS_DIR\}/sethlans"',
        )
        # Inside-out signing (issue #149) — the outer re-sign no longer
        # passes `--deep`. Match the current pattern.
        resign_pattern = re.compile(
            r'codesign\s+--force\s+--sign\s+-\s+'
            r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        rename_match = rename_pattern.search(dmg_contents)
        resign_match = resign_pattern.search(dmg_contents)
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
        rename_pattern = re.compile(
            r'mv\s+\\?\s*'
            r'"\$\{MACOS_DIR\}/run_launcher"\s+\\?\s*'
            r'"\$\{MACOS_DIR\}/sethlans"',
        )
        assert cp_marker in dmg_contents, (
            f"Expected marker {cp_marker!r} to be present as a sanity check."
        )
        rename_match = rename_pattern.search(dmg_contents)
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
        # Inside-out signing (issue #149) — the outer re-sign no longer
        # passes `--deep`. Match the current pattern.
        resign_pattern = re.compile(
            r'codesign\s+--force\s+--sign\s+-\s+'
            r'"\$\{STAGING_DIR\}/\$\{APP_NAME\}"',
        )
        xattr_match = xattr_pattern.search(dmg_contents)
        resign_match = resign_pattern.search(dmg_contents)
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
