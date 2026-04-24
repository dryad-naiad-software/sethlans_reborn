# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Import-safety tests for ``sethlans_worker_agent.agent``.

Regression coverage for issue #119: a module-level ``parser.parse_args()``
call caused any importer (pytest, tooling, other modules) to fail with
"unrecognized arguments: ..." and ``SystemExit(2)`` because argparse saw
the importer's own ``sys.argv``. These tests assert that importing the
agent module is side-effect free — no argparse, no logging configuration.

The tests deliberately avoid mutating ``sys.modules`` (e.g. popping the
cached agent module and re-importing). Other worker unit tests keep a
module-level reference to the same agent module and call
``mocker.patch.object(agent_module, '_shutdown_event', ...)`` on it. If
we swapped the cached copy out, code under test that does a fresh
``from sethlans_worker_agent import agent`` would see the new copy while
the patch was applied to the old copy, producing spurious failures.

Instead we verify import safety by running a subprocess that imports the
agent module with a hostile ``sys.argv`` and inspecting its exit code
and stderr. That is the true production scenario (a separate interpreter
importing the module) and cannot contaminate in-process state.
"""

import logging
import subprocess
import sys

import pytest


IMPORT_PROBE_SCRIPT = (
    "import sys, logging\n"
    "before_level = logging.getLogger().level\n"
    "before_handler_count = len(logging.getLogger().handlers)\n"
    "import sethlans_worker_agent.agent as agent_module\n"
    "assert hasattr(agent_module, 'main'), 'agent.main missing'\n"
    "assert callable(agent_module.main), 'agent.main not callable'\n"
    "after_level = logging.getLogger().level\n"
    "after_handler_count = len(logging.getLogger().handlers)\n"
    "assert after_level == before_level, (\n"
    "    f'root level changed {before_level} -> {after_level}'\n"
    ")\n"
    "assert after_handler_count == before_handler_count, (\n"
    "    f'root handlers changed {before_handler_count} -> '\n"
    "    f'{after_handler_count}'\n"
    ")\n"
    "print('IMPORT_SAFE')\n"
)


def _run_probe(extra_argv):
    """Run the probe script in a subprocess with the given extra argv.

    Returns the CompletedProcess. Passes the repo's worker/ and project
    root on PYTHONPATH so the child interpreter can import the package
    the same way the test runner does.
    """
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    worker_dir = repo_root / "worker"
    env_path = str(worker_dir) + ";" + str(repo_root)
    import os
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = env_path + (";" + existing if existing else "")
    return subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE_SCRIPT, *extra_argv],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_import_does_not_parse_sys_argv():
    """Importing ``sethlans_worker_agent.agent`` with hostile argv is clean.

    Simulates pytest's own ``sys.argv`` (with flags argparse would reject)
    in a fresh subprocess. If module-level argparse returns, the probe
    script exits with 2 and stderr contains "unrecognized arguments".
    """
    result = _run_probe(["tests/integration", "--some-unknown-flag"])

    assert result.returncode == 0, (
        f"Import probe exited {result.returncode}. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "unrecognized arguments" not in result.stderr, (
        f"argparse error leaked from import: {result.stderr!r}"
    )
    assert "usage:" not in result.stderr, (
        f"argparse usage banner leaked from import: {result.stderr!r}"
    )
    assert "IMPORT_SAFE" in result.stdout, (
        f"Probe did not reach success line: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


def test_agent_module_exposes_callable_main():
    """The already-imported agent module must expose ``main``.

    Cheap in-process check: other tests in this file cover the harder
    case (hostile argv) via subprocess. This one simply guarantees the
    public entry point wasn't renamed or removed.
    """
    from sethlans_worker_agent import agent as agent_module
    assert hasattr(agent_module, "main")
    assert callable(agent_module.main)


def test_main_calls_configure_logging_with_parsed_level(monkeypatch):
    """main(['--loglevel', 'DEBUG']) must wire logging before side effects.

    Stubs out every side-effectful downstream call (signal handlers,
    session monitor, setup phase, graceful shutdown) so the test verifies
    just the argument -> logging bridge without starting the agent loop.
    """
    from sethlans_worker_agent import agent as agent_module

    configure_calls = []
    monkeypatch.setattr(
        agent_module,
        "configure_logging",
        lambda level: configure_calls.append(level),
    )
    monkeypatch.setattr(agent_module.signal, "signal", lambda *a, **k: None)

    # Prevent main() from doing any real work past the logging bridge.
    # _run_setup_phase returning False causes main() to call
    # _graceful_shutdown and return immediately.
    monkeypatch.setattr(agent_module, "_run_setup_phase", lambda: False)
    monkeypatch.setattr(agent_module, "_graceful_shutdown", lambda: None)

    # The session monitor import happens inside main(); stub it out via
    # sys.modules so the real Win32 code never runs.
    import types
    stub = types.ModuleType(
        "sethlans_worker_agent.idle_detection.session_win32"
    )
    stub.start_session_monitor = lambda: None
    monkeypatch.setitem(
        sys.modules,
        "sethlans_worker_agent.idle_detection.session_win32",
        stub,
    )

    agent_module.main(["--loglevel", "DEBUG"])

    assert configure_calls == ["DEBUG"], (
        f"Expected configure_logging to be called once with 'DEBUG', "
        f"got {configure_calls!r}"
    )


def test_root_logger_untouched_by_fresh_import():
    """A fresh subprocess import does not configure the root logger.

    Uses a subprocess so the test does not need to swap ``sys.modules``
    on the in-process agent. Root level and handler count must match
    before and after the import.
    """
    # Sanity check the in-process root logger still looks sensible — this
    # test only cares about the subprocess assertion done in the probe,
    # but leaving this line here makes the intent clear when reading.
    assert isinstance(logging.getLogger().level, int)

    result = _run_probe([])
    if result.returncode != 0:
        pytest.fail(
            f"Probe failed: stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
    assert "IMPORT_SAFE" in result.stdout
