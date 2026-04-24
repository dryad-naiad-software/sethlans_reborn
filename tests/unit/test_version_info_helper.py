# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the Windows VERSIONINFO helper (issue #109).

``packaging/pyinstaller/version_info.make_version_info`` builds the
``VSVersionInfo`` structure PyInstaller stamps onto each Sethlans
Windows exe. These tests cover:

* TR-1 — the returned structure carries every expected branding
  StringStruct with the right value, and the ``FixedFileInfo`` filevers
  match the parsed VERSION tuple.
* TR-2 — malformed VERSION contents raises ``ValueError`` with a
  message naming "semver" / "VERSION".
* TR-3 — missing VERSION file raises ``SystemExit`` with a clear
  message at call time.
* Bonus — repeated calls with different component names return
  independent structures, each with the right ``OriginalFilename`` /
  ``InternalName``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# version_info lazy-imports PyInstaller.utils.win32.versioninfo at call
# time; without PyInstaller installed every test below errors on the
# make_version_info call. PyInstaller lives in requirements-build.txt
# (not in the test-env install set), so skip the whole module when the
# build tool is absent rather than adding a build dep to the test env.
pytest.importorskip("PyInstaller")

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "packaging" / "pyinstaller"

# Make ``packaging/pyinstaller/`` importable so the helper module can be
# loaded by its bare name (matches the path manipulation each spec file
# performs at build time).
if str(SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(SPEC_DIR))


@pytest.fixture()
def version_info_module():
    """Import ``version_info`` fresh so per-test patches take effect.

    ``_VERSION_FILE`` is bound at import time from
    ``Path(__file__).resolve().parent.parent.parent / 'VERSION'``.
    Tests patch the module-level constant to redirect file reads, so we
    reload the module to get a clean reference each run.
    """
    if "version_info" in sys.modules:
        del sys.modules["version_info"]
    module = importlib.import_module("version_info")
    yield module
    # Leave the module cached for other tests that may share it; the
    # next fixture invocation will reload anyway.


def _string_structs_by_name(version_info_result) -> dict:
    """Flatten StringFileInfo->StringTable->StringStruct into name->val."""
    out: dict = {}
    for kid in version_info_result.kids:
        # VSVersionInfo.kids is a mix of StringFileInfo and VarFileInfo;
        # only the former carries the branding StringTable.
        if not hasattr(kid, "kids"):
            continue
        for child in kid.kids:
            if not hasattr(child, "kids"):
                continue
            for struct in child.kids:
                name = getattr(struct, "name", None)
                val = getattr(struct, "val", None)
                if name is not None:
                    out[name] = val
    return out


class TestMakeVersionInfoFields:
    """TR-1: every branding field is present with the expected value."""

    def test_all_expected_string_fields(
        self, tmp_path: Path, monkeypatch, version_info_module,
    ) -> None:
        version_file = tmp_path / "VERSION"
        version_file.write_text("0.2.0\n", encoding="utf-8")
        monkeypatch.setattr(
            version_info_module, "_VERSION_FILE", version_file,
        )

        result = version_info_module.make_version_info(
            "run_launcher.exe", "run_launcher",
        )

        fields = _string_structs_by_name(result)
        assert fields["CompanyName"] == "Dryad and Naiad Software LLC"
        assert fields["FileDescription"] == "Sethlans"
        assert fields["FileVersion"] == "0.2.0"
        assert fields["InternalName"] == "run_launcher"
        assert fields["LegalCopyright"] == (
            "© 2025 Dryad and Naiad Software LLC"
        )
        assert fields["OriginalFilename"] == "run_launcher.exe"
        assert fields["ProductName"] == "Sethlans"
        assert fields["ProductVersion"] == "0.2.0"

    def test_fixed_file_info_tuple(
        self, tmp_path: Path, monkeypatch, version_info_module,
    ) -> None:
        version_file = tmp_path / "VERSION"
        version_file.write_text("0.2.0\n", encoding="utf-8")
        monkeypatch.setattr(
            version_info_module, "_VERSION_FILE", version_file,
        )

        result = version_info_module.make_version_info(
            "run_launcher.exe", "run_launcher",
        )

        # FixedFileInfo stores the tuple split across MS/LS dwords. Rebuild.
        ffi = result.ffi
        filevers = (
            ffi.fileVersionMS >> 16, ffi.fileVersionMS & 0xFFFF,
            ffi.fileVersionLS >> 16, ffi.fileVersionLS & 0xFFFF,
        )
        prodvers = (
            ffi.productVersionMS >> 16, ffi.productVersionMS & 0xFFFF,
            ffi.productVersionLS >> 16, ffi.productVersionLS & 0xFFFF,
        )
        assert filevers == (0, 2, 0, 0)
        assert prodvers == (0, 2, 0, 0)

    def test_translation_language_pair(
        self, tmp_path: Path, monkeypatch, version_info_module,
    ) -> None:
        # en-US (1033) + Unicode code page (1200). The VarFileInfo
        # Translation kid must match the StringTable lang code `040904B0`.
        version_file = tmp_path / "VERSION"
        version_file.write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(
            version_info_module, "_VERSION_FILE", version_file,
        )

        result = version_info_module.make_version_info(
            "run_launcher.exe", "run_launcher",
        )

        var_file_infos = [
            kid for kid in result.kids
            if kid.__class__.__name__ == "VarFileInfo"
        ]
        assert var_file_infos, "VarFileInfo should be present in kids"
        translation = var_file_infos[0].kids[0]
        assert translation.name == "Translation"
        assert translation.kids == [1033, 1200]


class TestMakeVersionInfoErrors:
    """TR-2 / TR-3: malformed and missing VERSION surface at call time."""

    @pytest.mark.parametrize(
        "bad_content",
        ["not-a-version", "0.2", "0.2.0-rc1", "1.2.3.4", "", "v0.2.0"],
    )
    def test_non_semver_raises_value_error(
        self, tmp_path: Path, monkeypatch, version_info_module,
        bad_content: str,
    ) -> None:
        version_file = tmp_path / "VERSION"
        version_file.write_text(bad_content, encoding="utf-8")
        monkeypatch.setattr(
            version_info_module, "_VERSION_FILE", version_file,
        )

        with pytest.raises(ValueError) as excinfo:
            version_info_module.make_version_info(
                "run_launcher.exe", "run_launcher",
            )
        message = str(excinfo.value)
        assert "semver" in message.lower() or "VERSION" in message, (
            f"ValueError message should reference semver or VERSION; "
            f"got: {message!r}"
        )

    def test_missing_version_file_raises_system_exit(
        self, tmp_path: Path, monkeypatch, version_info_module,
    ) -> None:
        missing_path = tmp_path / "definitely_absent" / "VERSION"
        monkeypatch.setattr(
            version_info_module, "_VERSION_FILE", missing_path,
        )

        with pytest.raises(SystemExit) as excinfo:
            version_info_module.make_version_info(
                "run_launcher.exe", "run_launcher",
            )
        message = str(excinfo.value)
        assert "VERSION" in message, (
            f"SystemExit message should reference VERSION; "
            f"got: {message!r}"
        )


class TestMakeVersionInfoRepeatCalls:
    """Bonus: each component gets a fresh structure with its own labels."""

    def test_repeat_calls_carry_distinct_filenames(
        self, tmp_path: Path, monkeypatch, version_info_module,
    ) -> None:
        version_file = tmp_path / "VERSION"
        version_file.write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(
            version_info_module, "_VERSION_FILE", version_file,
        )

        cases = [
            ("run_launcher.exe", "run_launcher"),
            ("run_manager.exe", "run_manager"),
            ("run_worker.exe", "run_worker"),
            ("run_tray_helper.exe", "run_tray_helper"),
        ]
        results = [
            version_info_module.make_version_info(orig, internal)
            for orig, internal in cases
        ]

        # Independent objects (no accidental shared mutable state).
        for i, result_a in enumerate(results):
            for result_b in results[i + 1:]:
                assert result_a is not result_b

        # Each result carries its own OriginalFilename / InternalName.
        for (orig, internal), result in zip(cases, results):
            fields = _string_structs_by_name(result)
            assert fields["OriginalFilename"] == orig
            assert fields["InternalName"] == internal
