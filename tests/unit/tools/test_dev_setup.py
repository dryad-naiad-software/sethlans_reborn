# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``tools/dev_setup.py``.

``dev_setup.main`` is a thin shim over ``caddy_fetch.cli.main`` — it
builds an argv list, delegates, and returns the child's exit code.
These tests pin the argv composition so future flag additions do not
silently regress.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def dev_setup():
    # Imported lazily — the conftest.py inserts tools/ onto sys.path
    # before any tests run, so this just picks up the patched path.
    return importlib.import_module("dev_setup")


class TestDevSetupArgv:

    def test_default_includes_target_dir(self, dev_setup, mocker):
        fetch = mocker.patch(
            "dev_setup.fetch_caddy_main", return_value=0,
        )
        rc = dev_setup.main([])
        assert rc == 0
        argv = fetch.call_args.args[0]
        assert "--target-dir" in argv
        idx = argv.index("--target-dir")
        # Next element should point to .venv-build/caddy under the repo.
        target = argv[idx + 1]
        assert ".venv-build" in target
        assert target.endswith("caddy")

    def test_platform_override_forwarded(self, dev_setup, mocker):
        fetch = mocker.patch(
            "dev_setup.fetch_caddy_main", return_value=0,
        )
        dev_setup.main(["--platform", "linux-amd64"])
        argv = fetch.call_args.args[0]
        assert "--platform" in argv
        assert argv[argv.index("--platform") + 1] == "linux-amd64"

    def test_verify_gpg_forwarded(self, dev_setup, mocker):
        fetch = mocker.patch(
            "dev_setup.fetch_caddy_main", return_value=0,
        )
        dev_setup.main(["--verify-gpg"])
        argv = fetch.call_args.args[0]
        assert "--verify-gpg" in argv

    def test_verbose_forwarded(self, dev_setup, mocker):
        fetch = mocker.patch(
            "dev_setup.fetch_caddy_main", return_value=0,
        )
        dev_setup.main(["--verbose"])
        argv = fetch.call_args.args[0]
        assert "--verbose" in argv

    def test_no_flags_produces_minimal_argv(self, dev_setup, mocker):
        fetch = mocker.patch(
            "dev_setup.fetch_caddy_main", return_value=0,
        )
        dev_setup.main([])
        argv = fetch.call_args.args[0]
        # Exactly --target-dir + its value and nothing else.
        assert argv[0] == "--target-dir"
        assert len(argv) == 2


class TestDevSetupReturnCode:

    def test_returns_child_rc_on_success(self, dev_setup, mocker):
        mocker.patch("dev_setup.fetch_caddy_main", return_value=0)
        assert dev_setup.main([]) == 0

    def test_propagates_nonzero_rc(self, dev_setup, mocker):
        mocker.patch("dev_setup.fetch_caddy_main", return_value=2)
        assert dev_setup.main([]) == 2

    def test_propagates_gpg_failure_rc(self, dev_setup, mocker):
        mocker.patch("dev_setup.fetch_caddy_main", return_value=3)
        assert dev_setup.main(["--verify-gpg"]) == 3


class TestDevSetupIdempotence:

    def test_repeat_invocation_no_ops(self, dev_setup, mocker):
        """Idempotence is delegated to caddy_fetch — we just prove that
        two back-to-back dev_setup calls re-invoke fetch_caddy_main
        twice, and that each returns 0 when the caddy_fetch layer
        short-circuits (0 from the mocked fetch stands in for the
        real no-op path)."""
        fetch = mocker.patch("dev_setup.fetch_caddy_main", return_value=0)
        assert dev_setup.main([]) == 0
        assert dev_setup.main([]) == 0
        assert fetch.call_count == 2
