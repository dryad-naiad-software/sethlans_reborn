# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Fixtures for the wizard hand-off E2E tests.

Provides:

* :func:`isolated_data_dir` — fresh tmpdir for each test.
* :func:`require_manager_port_free` / :func:`require_worker_port_free`
  — skip if port 8080 / 8081 is busy on the host. The mock runtime
  can't bind to a different port because FR-W14a hardcodes the probe
  URL by topology.
* :func:`launcher_driver` — context-manager factory that spawns the
  ``_driver.py`` subprocess in a given mode, yields a handle exposing
  the wizard URL + IPC secret + data dir, and cleans up on teardown.

Heavy-lifting helpers (subprocess spawn, readiness poll, termination)
live in ``_driver_process.py`` so this file stays under the 300-line
limit.
"""

from __future__ import annotations

import contextlib
import dataclasses
import secrets
import subprocess
from pathlib import Path
from typing import Iterator

import pytest

from . import _driver_process as _dp

# ---- FR-W14a hardcoded probe ports ----------------------------------------

MANAGER_PROBE_PORT = 8080
WORKER_PROBE_PORT = 8081


# ---- Driver handle dataclass ----------------------------------------------

@dataclasses.dataclass
class DriverHandle:
    """Handle to a running wizard E2E driver subprocess."""

    proc: subprocess.Popen
    data_dir: Path
    wizard_subdir: Path
    wizard_port: int | None
    wizard_base_url: str | None
    runtime_port: int
    setup_token: str
    ipc_secret: bytes


# ---- Fixtures --------------------------------------------------------------

@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    """Fresh per-test data directory under tmp."""
    data_dir = tmp_path / "sethlans_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def require_manager_port_free():
    """Skip the test if port 8080 (manager probe) is already in use."""
    if _dp.port_in_use(MANAGER_PROBE_PORT):
        pytest.skip(
            f"port {MANAGER_PROBE_PORT} already in use; cannot run "
            "wizard E2E test (FR-W14a probe URL is hardcoded)."
        )


@pytest.fixture
def require_worker_port_free():
    """Skip the test if port 8081 (worker probe) is already in use."""
    if _dp.port_in_use(WORKER_PROBE_PORT):
        pytest.skip(
            f"port {WORKER_PROBE_PORT} already in use; cannot run "
            "wizard E2E test (FR-W14a probe URL is hardcoded)."
        )


@pytest.fixture
def launcher_driver(isolated_data_dir: Path) -> Iterator:
    """Yield a context-manager factory that spawns the driver process.

    Usage::

        with launcher_driver(runtime_mode="health-ok") as handle:
            # handle.wizard_base_url, handle.proc, handle.data_dir, ...
            ...

    Cleanup happens on fixture teardown regardless of whether the
    context manager exited cleanly.
    """
    spawned: list[subprocess.Popen] = []

    @contextlib.contextmanager
    def _factory(
        runtime_mode: str = "health-ok",
        runtime_port: int = MANAGER_PROBE_PORT,
        idle_timeout: float | None = None,
        wait_for_wizard: bool = True,
    ) -> Iterator[DriverHandle]:
        # Pin the setup token + IPC secret so the test can authenticate
        # without racing the wizard's FR-W6 immediate-unlink and so the
        # test can validate the .wizard_done HMAC against a known key.
        setup_token = secrets.token_urlsafe(32)
        ipc_secret = secrets.token_bytes(32)
        # Pin the wizard's listening port via SETHLANS_WIZARD_PORT so
        # the .wizard_done marker's wizard_port field matches what we
        # observe (the wizard otherwise scans 8100-8104 and the marker
        # carries the first-attempted port from create_app's closure,
        # not the actually-bound port — wizard quirk, irrelevant here).
        wizard_port_env = _dp.find_free_port()
        proc = _dp.spawn_driver(
            isolated_data_dir,
            runtime_mode=runtime_mode,
            runtime_port=runtime_port,
            idle_timeout=idle_timeout,
            setup_token=setup_token,
            ipc_secret=ipc_secret,
            extra_env={"SETHLANS_WIZARD_PORT": str(wizard_port_env)},
        )
        spawned.append(proc)
        wizard_port: int | None = None
        wizard_base_url: str | None = None
        if wait_for_wizard:
            try:
                wizard_port, wizard_base_url = _dp.wait_for_wizard_ready(
                    isolated_data_dir, proc,
                )
            except Exception:
                _dp.terminate(proc)
                raise
        try:
            yield DriverHandle(
                proc=proc,
                data_dir=isolated_data_dir,
                wizard_subdir=isolated_data_dir / "wizard",
                wizard_port=wizard_port,
                wizard_base_url=wizard_base_url,
                runtime_port=runtime_port,
                setup_token=setup_token,
                ipc_secret=ipc_secret,
            )
        finally:
            _dp.terminate(proc, isolated_data_dir)

    try:
        yield _factory
    finally:
        for proc in spawned:
            _dp.terminate(proc, isolated_data_dir)
