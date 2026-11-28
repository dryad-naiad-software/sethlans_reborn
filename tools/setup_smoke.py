# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build-time smoke of the frozen setup mode (setup-rebuild chunk 7a).

Runs the built ``run_manager`` and ``run_worker`` from ``--bin-root``
(``<bin-root>/<component>/run_<component>``) against a fresh scratch
folder; every per-user and temp folder of the children points into it,
so the real per-user Sethlans folders are never touched:

1. write an owner-only ``<settings>/setup/setup_token``;
2. start ``run_manager --setup``; ``GET /api/health/`` must answer
   ``mode: setup`` with a ``boot_id``;
3. ``GET /setup/`` must answer 200 with the HTML page;
4. authenticate, submit a ``manager``-mode setup on SQLite: 200, then
   exit 10 within 10 s. ``sethlans.ini`` must hold ``[setup] version=1``,
   ``mode=manager``, a ``completed_at`` and a ``pbkdf2_sha256$`` admin
   hash, and be owner-only; the admin password and the session token
   must be in no file, the setup token only in its own file;
5. ``run_worker --setup-cards`` with ``{}``: exit 0 and one JSON line
   in the documented shape (the number of cards is not checked);
6. ``run_manager --manage migrate --check`` must reach Django (#191),
   and the launcher bundle size is printed. The leak scan runs again.

A 120 s watchdog bounds the whole run. On any failure every process the
smoke started is ended and the captured logs are printed, secrets
redacted.

Usage: python tools/setup_smoke.py --bin-root dist [--launcher-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from _setup_smoke_checks import (  # noqa: E402
    cards_problems, health_problems, ini_problems, leak_problems,
    manager_answers, page_problems, smoke_parent,
)
from _setup_smoke_proc import (  # noqa: E402
    ChildTracker, SmokeFailure, child_env, free_ports, scratch_folders, write_token,
)
from _smoke_common import (  # noqa: E402
    bundle_size_bytes, dump_logs, err, http_call, http_get_json,
    install_wall_clock_watchdog, manage_mode_problems, redact_text,
)
from shared import owner_only_acl  # noqa: E402
from shared.manager_exit_codes import EXIT_SETUP_SAVED  # noqa: E402

WALL_CLOCK_SECONDS = 120
READY_SECONDS = 60
SAVED_EXIT_SECONDS = 10
CARDS_SECONDS = 60
MANAGE_SECONDS = 30
POLL_SECONDS = 0.5
SESSION_COOKIE = "sethlans_setup_session"
SESSION_HEADER = "X-Setup-Session"


def component_cmd(bin_root: pathlib.Path, component: str) -> list[str]:
    """The command that starts a built component: ``<bin-root>/<c>/run_<c>``."""
    suffix = ".exe" if sys.platform == "win32" else ""
    exe = bin_root / component / f"run_{component}{suffix}"
    if not exe.is_file():
        raise SmokeFailure(f"{component} executable not found at {exe}")
    return [str(exe)]


def _fail_on(problems: list[str], step: str) -> None:
    if problems:
        raise SmokeFailure(f"{step}: " + "; ".join(problems))


class Smoke:
    """One run: its scratch folder, its environment and the processes it started."""

    def __init__(self, bin_root: pathlib.Path):
        self.bin_root = bin_root
        parent = smoke_parent(os.environ, sys.platform)
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="sethlans-setup-smoke-", dir=parent))
        self.settings = self.root / "settings"
        self.data_root = self.root / "data"
        self.logs = (self.root / "setup.stdout.log", self.root / "setup.stderr.log",
                     self.settings / "logs" / "setup-mode.log")
        for folder in scratch_folders(self.root).values():
            folder.mkdir()
        public, internal, self.setup_port = free_ports(3)
        self.base_url = f"http://127.0.0.1:{public}"
        env = child_env(os.environ, self.root, self.settings,
                        {"public": public, "internal": internal, "setup": self.setup_port})
        self.children = ChildTracker(env)
        self.token = secrets.token_urlsafe(32)
        self.token_file: pathlib.Path | None = None
        self.session = ""
        self.password = "Smoke-" + secrets.token_urlsafe(24)
        self._handles: list = []
        self._dump_lock = threading.Lock()
        self._dumped = False

    @property
    def secrets(self) -> list[str]:
        return [self.token, self.session, self.password]

    def start_setup(self) -> None:
        """Steps 1-2: the token, then ``run_manager --setup`` until its setup-mode health."""
        self.token_file = write_token(self.settings, self.token)
        out, errs = (open(path, "wb") for path in self.logs[:2])
        self._handles = [out, errs]
        argv = component_cmd(self.bin_root, "manager") + ["--setup"]
        print(f"Starting {' '.join(argv)} (settings {self.settings})")
        proc = self.children.start(argv, out, errs)
        started = time.monotonic()
        problems = ["no answer yet"]
        while time.monotonic() - started < READY_SECONDS:
            if proc.poll() is not None:
                raise SmokeFailure(f"setup mode exited {proc.returncode} before it answered")
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

    def check_leaks(self) -> None:
        _fail_on(leak_problems(self.root, self.token_file, password=self.password,
                               session=self.session, token=self.token), "leak scan")

    def submit(self) -> None:
        """Step 4: auth, a manager-mode submit, exit 10, then the saved file and the leak scan."""
        status, _, raw = http_call(self.base_url + "/api/setup/auth/", "POST", {"token": self.token})
        if status != 200:
            raise SmokeFailure(f"auth answered {status}: {raw[:400]!r}")
        self.session = json.loads(raw.decode("utf-8"))["session_token"]
        headers = {"Cookie": f"{SESSION_COOKIE}={self.session}", SESSION_HEADER: self.session}
        answers = manager_answers(self.data_root, self.setup_port, self.password)
        status, _, raw = http_call(self.base_url + "/api/setup/submit/", "POST", answers, headers)
        if status != 200:
            raise SmokeFailure(f"submit answered {status}: {raw[:800]!r}")
        try:
            code = self.children.proc.wait(timeout=SAVED_EXIT_SECONDS)
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
        self.check_leaks()
        print(f"Step 4 passed: saved, exit {code}, sethlans.ini checked, no secret on disk")

    def check_cards(self) -> None:
        """Step 5: ``run_worker --setup-cards`` answers one JSON line."""
        argv = component_cmd(self.bin_root, "worker") + ["--setup-cards"]
        code, out, errs = self.children.run(argv, b"{}", CARDS_SECONDS)
        if code != 0:
            raise SmokeFailure(f"--setup-cards exited {code}: stdout={out[:400]!r} stderr={errs[-800:]!r}")
        _fail_on(cards_problems(out), "--setup-cards")
        found = len(json.loads(out)["cards"])
        print(f"Step 5 passed: --setup-cards answered the documented shape ({found} card(s) listed)")

    def check_manage(self) -> None:
        """Step 6: #191, against the same scratch folder; then the leak scan once more."""
        argv = component_cmd(self.bin_root, "manager") + ["--manage", "migrate", "--check"]
        print(f"#191 invoking {' '.join(argv)}")
        code, _, errs = self.children.run(argv, b"", MANAGE_SECONDS)
        problems = manage_mode_problems(code, errs)
        if problems:
            raise SmokeFailure(f"--manage migrate --check (#191): {'; '.join(problems)}; stderr={errs[-800:]!r}")
        print(f"#191 passed: --manage reached Django (rc={code})")
        self.check_leaks()

    def dump(self) -> None:
        """Print the logs once, secrets redacted (the watchdog and a failing step may both ask)."""
        with self._dump_lock:
            if self._dumped:
                return
            self._dumped = True
            for handle in self._handles:
                handle.flush()
            dump_logs(*self.logs, redact=self.secrets)

    def abort(self) -> None:
        """Watchdog: every process ended first, then the logs (the scratch folder stays)."""
        for step in (self.children.stop, self.dump):
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - the watchdog exits anyway
                err(f"watchdog clean-up step failed: {exc!r}")

    def close(self) -> None:
        self.children.stop()
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
    smoke.start_setup()
    smoke.check_page()
    smoke.submit()
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
        smoke.children.stop()
        if not isinstance(exc, SmokeFailure):
            traceback.print_exc()
        err(f"SETUP SMOKE FAILED: {redact_text(str(exc), smoke.secrets)}")
        smoke.dump()
        return 1
    finally:
        smoke.close()
    print("All setup smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
