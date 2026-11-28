# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the ad-hoc signing steps in the macOS DMG build script.

Covers GitHub issues #85 (re-sign after the Info.plist edit, verify
before ``hdiutil``) and #149 (inside-out signing, no ``--deep``). The
bundle-path and executable-rename tests live in ``test_dmg_installer.py``.
"""

import re

import pytest

from tests.unit._dmg_installer_helpers import DMG_SCRIPT, OUTER_RESIGN_PATTERN, read_required


@pytest.fixture(scope="module")
def dmg_contents() -> str:
    return read_required(DMG_SCRIPT, "DMG build script")


class TestDmgInstallerCodesign:
    """Static assertions guarding the #85 fix in build_dmg.sh.

    PyInstaller 6.19 ad-hoc signs the Sethlans.app bundle at build time.
    Overwriting Contents/Info.plist from the template invalidates and
    strips that signature, so Gatekeeper reports the app as "damaged".
    The script must re-sign the bundle after the plist edit and verify
    the signature before hdiutil wraps it into a DMG.

    Fixes #85.
    """

    # The outer-bundle re-sign pattern (no --deep) is OUTER_RESIGN_PATTERN
    # in _dmg_installer_helpers.

    def test_resigns_bundle_after_plist_edit(self, dmg_contents: str) -> None:
        # Match `codesign --force --sign -` targeting the staged .app.
        # `--deep` is intentionally absent — see OUTER_RESIGN_PATTERN.
        assert OUTER_RESIGN_PATTERN.search(dmg_contents), (
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
        resign_match = OUTER_RESIGN_PATTERN.search(dmg_contents)
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
        resign_match = OUTER_RESIGN_PATTERN.search(dmg_contents)
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
        outer_match = OUTER_RESIGN_PATTERN.search(dmg_contents)
        assert find_match is not None and outer_match is not None
        assert find_match.start() < outer_match.start(), (
            "Nested Mach-O signing must run BEFORE the outer-bundle "
            "re-sign (inside-out order, issue #149)."
        )

    def test_signs_each_component_executable(self, dmg_contents: str) -> None:
        # Issue #149 inside-out signing: each PyInstaller-frozen component
        # launcher (manager, worker, launcher) must be signed individually
        # before the outer bundle. The script uses a `for component in
        # manager worker launcher` loop; there is no wizard bundle since the
        # setup rebuild (the manager serves the setup page).
        loop_pattern = re.compile(
            r'for\s+component\s+in\s+manager\s+worker\s+launcher\s*;',
        )
        assert loop_pattern.search(dmg_contents), (
            "Expected a `for component in manager worker launcher` "
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
        outer_match = OUTER_RESIGN_PATTERN.search(dmg_contents)
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
