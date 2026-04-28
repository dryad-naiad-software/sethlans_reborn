# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/phase.py`` (spec FR-1, FR-2, AC-PhaseDetect).

Truth table for ``detect_phase(data_dir, manager_data_dir)``:

* ``<data_dir>/wizard/.setup_token`` exists  -> ``"wizard"``
* No wizard token AND a setup-complete sentinel exists -> ``"runtime"``
* No wizard token AND no sentinel -> ``"wizard"`` (covers fresh install)
* Token-file ``exists()`` raises ``OSError`` -> falls through to sentinel
  branch (treated as missing).
* Sentinel-file ``exists()`` raises ``OSError`` -> falls through to
  ``"wizard"`` (fail-closed: assume setup not complete).

The function MUST NOT raise.
"""

from __future__ import annotations

from shared.tray.phase import detect_phase


# ------------------------------------------------------------------ #
# Happy paths (truth table)
# ------------------------------------------------------------------ #

class TestDetectPhaseTruthTable:

    def test_runtime_when_only_sentinel_present(self, tmp_path):
        """``.setup_complete`` (current) sentinel only -> runtime."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        (tmp_path / ".setup_complete").write_text("", encoding="utf-8")
        assert detect_phase(tmp_path, manager_dir) == "runtime"

    def test_runtime_when_legacy_sentinel_only(self, tmp_path):
        """Legacy ``<manager_dir>/setup_complete.json`` -> runtime."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        (manager_dir / "setup_complete.json").write_text(
            "{}", encoding="utf-8",
        )
        assert detect_phase(tmp_path, manager_dir) == "runtime"

    def test_wizard_when_token_file_present(self, tmp_path):
        """Wizard's ``.setup_token`` short-circuits regardless of sentinel."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        wizard_dir = tmp_path / "wizard"
        wizard_dir.mkdir()
        (wizard_dir / ".setup_token").write_text(
            "tok", encoding="utf-8",
        )
        # Even with sentinel present, token file forces wizard phase.
        (tmp_path / ".setup_complete").write_text("", encoding="utf-8")
        assert detect_phase(tmp_path, manager_dir) == "wizard"

    def test_wizard_when_token_present_no_sentinel(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        wizard_dir = tmp_path / "wizard"
        wizard_dir.mkdir()
        (wizard_dir / ".setup_token").write_text(
            "tok", encoding="utf-8",
        )
        assert detect_phase(tmp_path, manager_dir) == "wizard"

    def test_wizard_when_neither_present(self, tmp_path):
        """Fresh install: no sentinel, no wizard token -> wizard."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        assert detect_phase(tmp_path, manager_dir) == "wizard"


# ------------------------------------------------------------------ #
# OSError tolerance — must NEVER raise
# ------------------------------------------------------------------ #

class TestDetectPhaseOSErrorSafe:

    def test_token_exists_oserror_falls_through_to_wizard(
        self, tmp_path, mocker,
    ):
        """If token-file ``exists()`` raises, fall through; no sentinel
        either -> wizard."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        # First call (token check) raises; subsequent calls (sentinel
        # branch via ``sentinel_exists``) return False.
        call_count = {"n": 0}

        original_exists = type(tmp_path).exists

        def _exists(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("denied")
            return original_exists(self)

        mocker.patch("pathlib.Path.exists", _exists)
        assert detect_phase(tmp_path, manager_dir) == "wizard"

    def test_sentinel_exists_oserror_falls_through_to_wizard(
        self, tmp_path, mocker,
    ):
        """If sentinel ``exists()`` raises after token-missing, the
        function MUST swallow and return ``"wizard"`` (fail-closed)."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()

        # All ``Path.exists`` calls raise OSError.
        mocker.patch("pathlib.Path.exists", side_effect=OSError("denied"))
        assert detect_phase(tmp_path, manager_dir) == "wizard"

    def test_never_raises_with_pathological_inputs(self, tmp_path, mocker):
        """Coverage: even if ``sentinel_exists`` itself blows up, the
        outer ``try/except OSError`` catches it."""
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        from shared.tray import phase as phase_mod
        mocker.patch.object(
            phase_mod, "sentinel_exists",
            side_effect=OSError("rare race"),
        )
        # Must not raise; falls through to wizard.
        assert detect_phase(tmp_path, manager_dir) == "wizard"


# ------------------------------------------------------------------ #
# Return type — Literal contract
# ------------------------------------------------------------------ #

class TestDetectPhaseReturnType:

    def test_returns_string_literal(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        result = detect_phase(tmp_path, manager_dir)
        assert isinstance(result, str)
        assert result in ("wizard", "runtime")
