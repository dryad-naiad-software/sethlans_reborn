# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The launcher loop (``launcher.machine_loop.run_loop``) run in this process
from source, with its real children: Caddy, ``run_manager --setup``,
``run_manager`` and ``run_worker``.

The loop runs on a thread with no tray: tray markers are written by the
test as this process (``tray_pid_override``), with the session secret
known here. Every port is a free one picked here and handed over the way
the product reads it (``[server] port``, ``SETHLANS_MANAGER_WAITRESS_*``,
``SETHLANS_MANAGER_CADDY_LOOPBACK_PORT``, ``SETHLANS_WORKER_*``), so a
Sethlans running on the machine is never in the way. Every folder is
under the test's ``tmp_path``; the broadcaster params are not published.

Nothing waits without a deadline: :meth:`LoopRun.wait_state` polls
``setup/launcher_state.json`` (the tray's view) and fails at once when
the loop thread has ended. :meth:`LoopRun.stop` asks for a quit, joins
the thread, then ends every child the loop started that is still alive,
by its PID and its descendants only.
"""
import datetime as _dt
import json
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Optional

from launcher import caddy_wiring, machine_loop, supervision
from launcher.loop_context import LoopContext
from tests.integration.manager.setup_mode.setup_process import end_tree, free_port, request

READY_S = 60.0
START_S = 240.0
STOP_S = 90.0
POLL_S = 0.2
SECRET = "integration-launcher-secret-" + "x" * 24


def unverified_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class LoopRun:
    """One loop with its folders, ports and environment (see the module doc)."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.root = tmp_path
        self.settings_dir = tmp_path / "settings"
        self.manager_data = tmp_path / "manager-data"
        self.public_port = free_port("0.0.0.0")
        self.worker_port = free_port("0.0.0.0")
        self.ctx: Optional[LoopContext] = None
        self.rc = None
        self.error = None
        self.procs = set()
        self._thread = None
        self._stopped = False
        self._environment(monkeypatch)
        self._seed_settings()

    def _environment(self, monkeypatch) -> None:
        for key in list(os.environ):
            if key.upper().startswith("SETHLANS_") or key.upper() == "DJANGO_SETTINGS_MODULE":
                monkeypatch.delenv(key, raising=False)
        home = self.root / "home"
        for name in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            (home / name).mkdir(parents=True, exist_ok=True)
            monkeypatch.setenv(name, str(home / name))
        ports = iter(sorted({free_port() for _ in range(8)} - {self.public_port, self.worker_port}))
        monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(self.settings_dir))
        monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(self.settings_dir / "sethlans.ini"))
        monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(self.manager_data))
        for name in ("SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
                     "SETHLANS_MANAGER_CADDY_LOOPBACK_PORT", "SETHLANS_WORKER_CADDY_LOOPBACK_PORT",
                     "SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT"):
            monkeypatch.setenv(name, str(next(ports)))
        monkeypatch.setenv("SETHLANS_WORKER_UI_PORT", str(self.worker_port))
        monkeypatch.setenv("SETHLANS_DISABLE_RELEASE_FETCH", "1")
        monkeypatch.setenv("SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS", "0")
        monkeypatch.setenv("SETHLANS_SECURITY_DEBUG", "False")
        monkeypatch.setenv("PYTHONUNBUFFERED", "1")

    def _seed_settings(self) -> None:
        """The earlier ``[server] port`` (so setup uses ours) and no FFmpeg download."""
        self.settings_dir.mkdir(parents=True)
        missing = self.root / "no-ffmpeg" / "ffmpeg"
        (self.settings_dir / "sethlans.ini").write_text(
            f"[server]\nport = {self.public_port}\n\n[ffmpeg]\npath = {missing}\n", encoding="utf-8")

    # --- running the loop ---------------------------------------------------------------

    def start(self) -> "LoopRun":
        self.ctx = LoopContext(settings_dir=self.settings_dir, secret=SECRET, tray_pid_override=os.getpid(),
                               params_dir=self.manager_data, no_browser=True)
        self._thread = threading.Thread(target=self._run, name="test-launcher-loop", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            self.rc = machine_loop.run_loop(self.ctx)
        except BaseException as exc:  # noqa: BLE001 - reported by wait_state and stop
            self.error = exc

    def _note_pids(self) -> None:
        for proc in (self.ctx.manager, self.ctx.worker, self.ctx.setup_proc):
            if proc is not None:
                self.procs.add(proc)
        caddy = supervision.get_caddy_supervisor()
        handle = getattr(caddy, "_process", None) if caddy is not None else None
        if handle is not None:
            self.procs.add(handle)

    def state(self) -> Optional[dict]:
        try:
            return json.loads((self.settings_dir / "setup" / "launcher_state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def wait_state(self, wanted: str, timeout: float = START_S, *, with_reason: bool = False) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._note_pids()
            if not self._thread.is_alive():
                raise AssertionError(f"the loop ended (rc={self.rc}, error={self.error!r}) "
                                     f"waiting for {wanted}; state {self.state()}\n{self.logs()}")
            current = self.state()
            if current and current["state"] == wanted and (current["reason"] or not with_reason):
                return current
            time.sleep(POLL_S)
        raise AssertionError(f"no {wanted} within {timeout} s; state {self.state()}\n{self.logs()}")

    def stop(self) -> int:
        """Quit as the tray does; return the loop's exit code once every child has ended.

        Idempotent: the fixture calls it again at teardown."""
        if self._stopped:
            return self.rc
        self._stopped = True
        if self._thread is not None and self._thread.is_alive():
            supervision.request_quit(supervision.QUIT_ALL)
            self._thread.join(timeout=STOP_S)
        alive = self._thread is not None and self._thread.is_alive()
        leaked = [proc for proc in self.procs if proc.poll() is None]
        for proc in leaked:
            end_tree(proc)
        supervision.shutdown_supervisors()
        supervision._reset_for_tests()
        caddy_wiring._reset_for_tests()
        assert not alive, "the loop did not stop after a quit"
        assert not leaked, f"the loop left children running: {[proc.pid for proc in leaked]}"
        if self.error is not None:
            raise self.error
        return self.rc

    # --- what the test reads and sends ----------------------------------------------------

    def token_bytes(self) -> bytes:
        return (self.settings_dir / "setup" / "setup_token").read_bytes()

    def cert(self) -> Path:
        return self.manager_data / "tls" / "cert.pem"

    def https(self, method: str, path: str, port: Optional[int] = None, body=None):
        context = ssl.create_default_context(cafile=str(self.cert()))
        return request(port or self.public_port, method, path, body, ssl_context=context)

    def https_unverified(self, method: str, path: str, port: int):
        """A request to a page with its own self-signed certificate (the worker's)."""
        return request(port, method, path, ssl_context=unverified_context())

    def request_reset(self) -> None:
        """The tray's Reset (``.reset_requested``), as the tray would write it."""
        payload = {"secret": SECRET, "pid": os.getpid(), "action": "reset", "target": "all",
                   "requested_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
        (self.settings_dir / ".reset_requested").write_text(json.dumps(payload), encoding="utf-8")

    def logs(self) -> str:
        parts = []
        for path in sorted((self.settings_dir / "logs").glob("*")) if (self.settings_dir / "logs").is_dir() else ():
            if path.is_file():
                parts.append(f"--- {path.name}\n{path.read_text(encoding='utf-8', errors='replace')[-4000:]}")
        return "\n".join(parts)
