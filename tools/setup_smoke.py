# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build-time smoke of the frozen setup mode (setup-rebuild chunk 7a).

Runs the built ``run_manager`` and ``run_worker`` from ``--bin-root``
(``<bin-root>/<component>/run_<component>``) against a fresh scratch
folder; the real per-user Sethlans folders are never touched:

1. write an owner-only ``<settings>/setup/setup_token``;
2. start ``run_manager --setup``; ``GET /api/health/`` must answer
   ``mode: setup`` with a ``boot_id``;
3. ``GET /setup/`` must answer 200 with the HTML page;
4. authenticate, submit a ``manager``-mode setup on SQLite: 200, then
   exit 10 within 10 s. ``sethlans.ini`` must hold ``[setup] version=1``,
   ``mode=manager``, a ``completed_at`` and a ``pbkdf2_sha256$`` admin
   hash, and be owner-only; the plain password must be in no file;
5. ``run_worker --setup-cards`` with ``{}``: exit 0 and one JSON line
   in the documented shape (the number of cards is not checked);
6. ``run_manager --manage migrate --check`` must reach Django (#191),
   and the launcher bundle size is printed.

A 120 s watchdog bounds the whole run. On any failure the captured
logs are printed and every process the smoke started is ended.

Usage: python tools/setup_smoke.py --bin-root dist [--launcher-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from _setup_smoke_checks import (  # noqa: E402
    cards_problems, files_holding, health_problems, ini_problems,
    manager_answers, page_problems, smoke_parent,
)
from _smoke_common import (  # noqa: E402
    bundle_size_bytes, check_manager_manage_mode, dump_logs, err,
    http_call, http_get_json, install_wall_clock_watchdog, terminate,
)
from shared import owner_only_acl  # noqa: E402
from shared.manager_exit_codes import EXIT_SETUP_SAVED  # noqa: E402

WALL_CLOCK_SECONDS = 120
READY_SECONDS = 60
SAVED_EXIT_SECONDS = 10
CARDS_SECONDS = 60
POLL_SECONDS = 0.5
SESSION_COOKIE = "sethlans_setup_session"
SESSION_HEADER = "X-Setup-Session"


class SmokeFailure(Exception):
    """One smoke step failed; the message says which and why."""


def component_cmd(bin_root: pathlib.Path, component: str) -> list[str]:
    """The command that starts a built component: ``<bin-root>/<c>/run_<c>``."""
    suffix = ".exe" if sys.platform == "win32" else ""
    exe = bin_root / component / f"run_{component}{suffix}"
    if not exe.is_file():
        raise SmokeFailure(f"{component} executable not found at {exe}")
    return [str(exe)]


def free_ports(count: int) -> list[int]:
    """``count`` distinct free loopback ports."""
    ports: set[int] = set()
    while len(ports) < count:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            ports.add(sock.getsockname()[1])
    return sorted(ports)


def _fail_on(problems: list[str], step: str) -> None:
    if problems:
        raise SmokeFailure(f"{step}: " + "; ".join(problems))


class Smoke:
    """One run: its scratch folder, ports, environment and setup process."""

    def __init__(self, bin_root: pathlib.Path):
        self.bin_root = bin_root
        parent = smoke_parent(os.environ, sys.platform)
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="sethlans-setup-smoke-", dir=parent))
        self.settings = self.root / "settings"
        self.data_root = self.root / "data"
        self.logs = (self.root / "setup.stdout.log", self.root / "setup.stderr.log",
                     self.settings / "logs" / "setup-mode.log")
        self.public_port, self.internal_port, self.setup_port = free_ports(3)
        self.env = self._env()
        self.proc: subprocess.Popen | None = None
        self._handles: list = []

    def _env(self) -> dict:
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("SETHLANS_") and key.upper() != "DJANGO_SETTINGS_MODULE"}
        for name in ("localappdata", "home"):
            (self.root / name).mkdir()
        env.update({
            "SETHLANS_SETTINGS_DIR": str(self.settings),
            "LOCALAPPDATA": str(self.root / "localappdata"),
            "HOME": str(self.root / "home"),
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC": str(self.public_port),
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL": str(self.internal_port),
            "SETHLANS_SETUP_PORT": str(self.setup_port),
            "PYTHONUNBUFFERED": "1",
        })
        return env

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.public_port}"

    def write_token(self) -> str:
        """Step 1: a fresh owner-only setup token, as the launcher writes it."""
        token = secrets.token_urlsafe(32)
        for folder in (self.settings, self.settings / "setup"):
            owner_only_acl.create_owner_only_dir(folder)
        fd = owner_only_acl.create_owner_only_file(self.settings / "setup" / "setup_token")
        try:
            os.write(fd, token.encode("ascii"))
        finally:
            os.close(fd)
        return token

    def start_setup(self) -> None:
        """Step 2: start ``run_manager --setup`` and wait for its setup-mode health."""
        out, errs = (open(path, "wb") for path in self.logs[:2])
        self._handles = [out, errs]
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                 if sys.platform == "win32" else {"start_new_session": True})
        argv = component_cmd(self.bin_root, "manager") + ["--setup"]
        print(f"Starting {' '.join(argv)} (settings {self.settings})")
        self.proc = subprocess.Popen(argv, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=errs, **group)
        started = time.monotonic()
        problems = ["no answer yet"]
        while time.monotonic() - started < READY_SECONDS:
            if self.proc.poll() is not None:
                raise SmokeFailure(f"setup mode exited {self.proc.returncode} before it answered")
            problems = health_problems(*http_get_json(self.base_url + "/api/health/"))
            if not problems:
                print(f"Step 2 passed: health is setup mode after {time.monotonic() - started:.1f}s")
                return
            time.sleep(POLL_SECONDS)
        _fail_on(problems, f"setup mode not ready within {READY_SECONDS}s")

    def check_page(self) -> None:
        """Step 3: the setup page is served."""
        _fail_on(page_problems(*http_call(self.base_url + "/setup/")), "GET /setup/")
        print("Step 3 passed: GET /setup/ -> 200 html")

    def submit(self, token: str) -> None:
        """Step 4: auth, a manager-mode submit, exit 10, then the saved file."""
        status, _, raw = http_call(self.base_url + "/api/setup/auth/", "POST", {"token": token})
        if status != 200:
            raise SmokeFailure(f"auth answered {status}: {raw[:400]!r}")
        session = json.loads(raw.decode("utf-8"))["session_token"]
        headers = {"Cookie": f"{SESSION_COOKIE}={session}", SESSION_HEADER: session}
        password = "Smoke-" + secrets.token_urlsafe(24)
        answers = manager_answers(self.data_root, self.setup_port, password)
        status, _, raw = http_call(self.base_url + "/api/setup/submit/", "POST", answers, headers)
        if status != 200:
            raise SmokeFailure(f"submit answered {status}: {raw[:800]!r}")
        try:
            code = self.proc.wait(timeout=SAVED_EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            raise SmokeFailure(f"setup mode still running {SAVED_EXIT_SECONDS}s after the submit") from None
        if code != EXIT_SETUP_SAVED:
            raise SmokeFailure(f"setup mode exited {code}, expected {EXIT_SETUP_SAVED}")
        ini = self.settings / "sethlans.ini"
        try:
            text = ini.read_text(encoding="utf-8")
        except OSError as exc:
            raise SmokeFailure(f"cannot read {ini}: {exc}") from None
        _fail_on(ini_problems(text), "sethlans.ini")
        if not owner_only_acl.verify_owner_only(ini):
            raise SmokeFailure(f"{ini} is not owner-only")
        leaks = files_holding(self.root, password.encode("utf-8"))
        if leaks:
            raise SmokeFailure("the plain admin password is in " + ", ".join(map(str, leaks)))
        print(f"Step 4 passed: saved, exit {code}, sethlans.ini checked, no plain password on disk")

    def check_cards(self) -> None:
        """Step 5: ``run_worker --setup-cards`` answers one JSON line."""
        argv = component_cmd(self.bin_root, "worker") + ["--setup-cards"]
        try:
            result = subprocess.run(argv, input=b"{}", capture_output=True, env=self.env,
                                    timeout=CARDS_SECONDS)
        except subprocess.TimeoutExpired:
            raise SmokeFailure(f"--setup-cards did not finish within {CARDS_SECONDS}s") from None
        if result.returncode != 0:
            raise SmokeFailure(f"--setup-cards exited {result.returncode}: "
                               f"stdout={result.stdout[:400]!r} stderr={result.stderr[-800:]!r}")
        _fail_on(cards_problems(result.stdout), "--setup-cards")
        found = len(json.loads(result.stdout)["cards"])
        print(f"Step 5 passed: --setup-cards answered the documented shape ({found} card(s) listed)")

    def check_manage(self) -> None:
        """Step 6: #191, against the same scratch folder."""
        if not check_manager_manage_mode(component_cmd(self.bin_root, "manager"), self.env):
            raise SmokeFailure("run_manager --manage migrate --check (#191)")

    def dump(self) -> None:
        for handle in self._handles:
            handle.flush()
        dump_logs(*self.logs)

    def stop(self) -> None:
        """End the setup process tree, if still running."""
        if self.proc is not None:
            terminate(self.proc)

    def abort(self) -> None:
        """Watchdog: logs out, processes ended (the scratch folder stays)."""
        self.dump()
        self.stop()

    def close(self) -> None:
        self.stop()
        for handle in self._handles:
            handle.close()
        self._handles = []
        shutil.rmtree(self.root, ignore_errors=True)


def print_launcher_size(launcher_dir: pathlib.Path) -> None:
    if not launcher_dir.is_dir():
        raise SmokeFailure(f"launcher bundle not found at {launcher_dir}")
    size = bundle_size_bytes(launcher_dir)
    print(f"Launcher bundle size: {size} bytes ({size / 1024 / 1024:.1f} MiB) at {launcher_dir}")


def run(smoke: Smoke, launcher_dir: pathlib.Path) -> None:
    token = smoke.write_token()
    smoke.start_setup()
    smoke.check_page()
    smoke.submit(token)
    smoke.check_cards()
    smoke.check_manage()
    print_launcher_size(launcher_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bin-root", required=True,
                        help="folder holding manager/run_manager and worker/run_worker")
    parser.add_argument("--launcher-dir",
                        help="launcher bundle whose size is printed (default: <bin-root>/launcher)")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    bin_root = pathlib.Path(args.bin_root).resolve()
    launcher_dir = pathlib.Path(args.launcher_dir).resolve() if args.launcher_dir else bin_root / "launcher"
    smoke = Smoke(bin_root)
    install_wall_clock_watchdog(WALL_CLOCK_SECONDS, on_expire=smoke.abort)
    try:
        run(smoke, launcher_dir)
    except Exception as exc:  # noqa: BLE001 - every failure dumps the logs and exits 1
        if not isinstance(exc, SmokeFailure):
            traceback.print_exc()
        err(f"SETUP SMOKE FAILED: {exc}")
        smoke.dump()
        return 1
    finally:
        smoke.close()
    print("All setup smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
