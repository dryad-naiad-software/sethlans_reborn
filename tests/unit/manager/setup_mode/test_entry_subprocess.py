# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_manager.py --setup`` / ``--hash-password`` in a child process.

The child starts with the run-time settings module already in
``DJANGO_SETTINGS_MODULE`` (as a frozen bundle's start hook leaves it) and
must still load ``settings_setup``, never the ``workers`` app or the
run-time start modules. The server itself is replaced in the child, so no
port is opened.
"""
import json
import subprocess
import textwrap

import pytest
from django.conf import settings as runtime_settings

from tests.unit.manager.setup_mode._helpers import GOOD_PASSWORD, PYTHON, RUN_MANAGER, child_env

TIMEOUT = 120

# Runs ``run_manager.py`` as ``__main__`` with ``serve_setup`` replaced by a
# reporter, so the dispatch, the settings switch and the guard are real.
_SETUP_CHILD = textwrap.dedent("""
    import json, runpy, sys
    from sethlans_manager.setup_mode import entry

    def report():
        from django.conf import settings
        print("REPORT " + json.dumps({
            "settings_module": settings.SETTINGS_MODULE,
            "installed_apps": list(settings.INSTALLED_APPS),
            "workers_modules": sorted(m for m in sys.modules if m == "workers" or m.startswith("workers.")),
            "runtime_modules": sorted(m for m in sys.modules if m in (
                "sethlans_manager.settings", "sethlans_manager.migration_runner",
                "sethlans_manager.runtime_init", "sethlans_manager.waitress_launcher")),
            "hashers": list(settings.PASSWORD_HASHERS),
            "validators": settings.AUTH_PASSWORD_VALIDATORS,
            "databases": settings.DATABASES,
            "debug": settings.DEBUG,
            "root_urlconf": settings.ROOT_URLCONF,
            "upload_max": settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
        }))
        return 0

    entry.serve_setup = report
    sys.argv = [sys.argv[1], "--setup"]
    runpy.run_path(sys.argv[0], run_name="__main__")
""")


def _run(args, tmp_path, stdin=b"", **env):
    return subprocess.run(args, input=stdin, capture_output=True, timeout=TIMEOUT,
                          env=child_env(tmp_path, **env), cwd=str(tmp_path))


@pytest.fixture(scope="module")
def setup_report(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("setup-child")
    result = _run([PYTHON, "-c", _SETUP_CHILD, str(RUN_MANAGER)], tmp_path,
                  DJANGO_SETTINGS_MODULE="sethlans_manager.settings", SETHLANS_SECURITY_DEBUG="True")
    lines = [line for line in result.stdout.decode("utf-8").splitlines() if line.startswith("REPORT ")]
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert len(lines) == 1, result.stdout
    return json.loads(lines[0][len("REPORT "):])


class TestSetupLoadsSetupSettings:

    def test_settings_setup_replaces_the_runtime_module_in_the_environment(self, setup_report):
        assert setup_report["settings_module"] == "sethlans_manager.settings_setup"

    def test_workers_app_is_neither_installed_nor_imported(self, setup_report):
        assert "workers" not in setup_report["installed_apps"]
        assert setup_report["workers_modules"] == []

    def test_no_runtime_settings_or_start_module_is_imported(self, setup_report):
        assert setup_report["runtime_modules"] == []

    def test_setup_mode_has_no_database_and_no_debug_pages(self, setup_report):
        # ``DATABASES = {}``: Django fills in only its dummy backend.
        assert list(setup_report["databases"]) == ["default"]
        assert setup_report["databases"]["default"]["ENGINE"] == "django.db.backends.dummy"
        assert setup_report["debug"] is False

    def test_setup_urlconf_and_body_limit_are_active(self, setup_report):
        assert setup_report["root_urlconf"] == "sethlans_manager.setup_mode.urls"
        assert setup_report["upload_max"] == 65536


class TestValidatorsAndHashersMatchRuntime:

    def test_password_validators_are_equal(self, setup_report):
        assert setup_report["validators"] == runtime_settings.AUTH_PASSWORD_VALIDATORS
        assert len(setup_report["validators"]) == 4

    def test_password_hashers_are_equal_and_pbkdf2_first(self, setup_report):
        assert setup_report["hashers"] == list(runtime_settings.PASSWORD_HASHERS)
        assert setup_report["hashers"][0] == "django.contrib.auth.hashers.PBKDF2PasswordHasher"


class TestRunManagerCommandLine:

    def test_hash_password_prints_one_pbkdf2_line(self, tmp_path):
        result = _run([PYTHON, str(RUN_MANAGER), "--hash-password"], tmp_path,
                      stdin=(GOOD_PASSWORD + "\n").encode("utf-8"),
                      DJANGO_SETTINGS_MODULE="sethlans_manager.settings")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.decode("utf-8").splitlines()
        assert len(lines) == 1
        assert lines[0].startswith("pbkdf2_sha256$1000000$")
        assert GOOD_PASSWORD.encode() not in result.stdout + result.stderr

    def test_hash_password_refuses_a_weak_password_with_exit_1(self, tmp_path):
        result = _run([PYTHON, str(RUN_MANAGER), "--hash-password"], tmp_path, stdin=b"password\n")
        assert result.returncode == 1
        assert result.stdout == b""
        assert b"too common" in result.stderr

    @pytest.mark.parametrize("args", [
        ["--setup", "--dev"],
        ["--setup", "--manage", "check"],
        ["--hash-password", "--dev"],
        ["--setup", "--hash-password"],
        ["--setup", "--answers", "a.ini"],
        ["--setup", "--bogus"],
    ])
    def test_bad_arguments_exit_2(self, tmp_path, args):
        result = _run([PYTHON, str(RUN_MANAGER), *args], tmp_path)
        assert result.returncode == 2
        assert b"[ERROR]" in result.stderr

    def test_setup_without_a_token_file_exits_2(self, tmp_path):
        result = _run([PYTHON, str(RUN_MANAGER), "--setup"], tmp_path)
        assert result.returncode == 2
        assert b"No setup token" in result.stderr
