# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A real ``run_manager --setup`` child process for integration tests.

The child gets a clean environment: every ``SETHLANS_*`` variable of the
test process is dropped, ``LOCALAPPDATA`` and ``SETHLANS_SETTINGS_DIR``
point under the test's ``tmp_path``, and the Waitress ports and the
setup port are free ports picked here. The setup token is written
owner-only at ``<settings>/setup/setup_token`` before the start.

Output goes to files (no pipe can fill up), and :meth:`SetupProcess.stop`
ends a running child and every process it started (the worker commands it
runs), so a failing test leaves nothing running (#343). On POSIX the child
leads its own process group for that. A child that already ended on its
own has waited for its worker commands itself; its group is not signalled
then, because once reaped its id may name someone else's group.
"""
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Optional

import psutil

from shared import owner_only_acl

REPO_ROOT = Path(__file__).resolve().parents[4]
RUN_MANAGER = REPO_ROOT / "manager" / "run_manager.py"
READY_TIMEOUT_S = 60.0
KILL_WAIT_S = 10.0
EXIT_SAVED = 10
ADMIN_PASSWORD = "Plain-Admin-Secret-7c4e1f9a"
SESSION_COOKIE = "sethlans_setup_session"


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def distinct_free_ports(count: int, host: str = "127.0.0.1"):
    ports = set()
    while len(ports) < count:
        ports.add(free_port(host))
    return sorted(ports)


def write_token(settings_dir: Path, token: str) -> Path:
    """Write a fresh setup token owner-only, as the launcher does (an earlier one is replaced)."""
    for folder in (settings_dir, settings_dir / "setup"):
        if not folder.exists():
            owner_only_acl.create_owner_only_dir(folder)
    path = settings_dir / "setup" / "setup_token"
    path.unlink(missing_ok=True)
    fd = owner_only_acl.create_owner_only_file(path)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    return path


def end_tree(proc: subprocess.Popen, wait: float = KILL_WAIT_S) -> None:
    """Kill a running ``proc`` and its descendants, waiting at most ``wait`` s for it (#343).

    Its descendants are listed before anything is killed, so a worker command
    it runs is not left orphaned. On POSIX ``proc`` leads its own process
    group (``process_group=0``); ``killpg`` is sent before ``proc.kill()``
    (which polls, and so may reap it), while the group is surely still its
    own, so members orphaned earlier end too. A ``proc`` that already ended
    is left alone (module doc).
    """
    if proc.poll() is not None:
        return
    _kill_descendants(proc.pid)
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:  # pragma: no cover - a stuck kill
        pass


def _kill_descendants(pid: int) -> None:
    try:
        descendants = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return
    for child in descendants:
        try:
            child.kill()
        except psutil.Error:
            pass


def child_env(tmp_path: Path, settings_dir: Path, extra: dict) -> dict:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("SETHLANS_") and key.upper() != "DJANGO_SETTINGS_MODULE"}
    local = tmp_path / "localappdata"
    local.mkdir(exist_ok=True)
    env.update({"LOCALAPPDATA": str(local), "SETHLANS_SETTINGS_DIR": str(settings_dir),
                "PYTHONUNBUFFERED": "1"})
    env.update(extra)
    return env


@dataclass
class Response:
    status: int
    headers: dict
    raw: bytes
    finished_at: float = 0.0

    def json(self):
        return json.loads(self.raw.decode("utf-8"))


def request(port: int, method: str, path: str, body=None, headers=None, *, host="127.0.0.1",
            ssl_context=None, timeout=60.0) -> Response:
    """One request on its own connection; the whole body is read."""
    if ssl_context is not None:
        conn = HTTPSConnection(host, port, timeout=timeout, context=ssl_context)
    else:
        conn = HTTPConnection(host, port, timeout=timeout)
    data = None
    sent = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        sent.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=data, headers=sent)
        resp = conn.getresponse()
        raw = resp.read()
        return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, raw, time.monotonic())
    finally:
        conn.close()


@dataclass
class SetupProcess:
    """One setup-mode child and where it listens."""

    tmp_path: Path
    proc: Optional[subprocess.Popen] = None
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    settings_dir: Path = None
    waitress_port: int = 0
    internal_port: int = 0
    setup_port: int = 0
    _outputs: tuple = ()

    @property
    def ini_path(self) -> Path:
        return self.settings_dir / "sethlans.ini"

    @property
    def log_path(self) -> Path:
        return self.settings_dir / "logs" / "setup-mode.log"

    def start(self, setup_port: Optional[int] = None, extra_env: Optional[dict] = None) -> "SetupProcess":
        self.settings_dir = self.tmp_path / "settings"
        write_token(self.settings_dir, self.token)
        taken = {setup_port} if setup_port else set()
        ports = [p for p in distinct_free_ports(3 + len(taken)) if p not in taken]
        self.waitress_port, self.internal_port = ports[0], ports[1]
        self.setup_port = setup_port or ports[2]
        env = child_env(self.tmp_path, self.settings_dir, {
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC": str(self.waitress_port),
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL": str(self.internal_port),
            "SETHLANS_SETUP_PORT": str(self.setup_port),
            **(extra_env or {}),
        })
        out = open(self.tmp_path / "setup.stdout.log", "wb")
        err = open(self.tmp_path / "setup.stderr.log", "wb")
        self._outputs = (out, err)
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"process_group": 0})
        self.proc = subprocess.Popen(  # nosec B603 - fixed argv
            [sys.executable, str(RUN_MANAGER), "--setup"], cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, **group)
        self._wait_ready()
        return self

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"setup mode exited {self.proc.returncode} before ready\n{self.output()}")
            try:
                resp = request(self.waitress_port, "GET", "/api/health/", timeout=2)
                if resp.status == 200 and resp.json().get("mode") == "setup":
                    return
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"setup mode did not answer on {self.waitress_port}\n{self.output()}")

    def output(self) -> str:
        for handle in self._outputs:
            handle.flush()
        parts = []
        for name in ("setup.stdout.log", "setup.stderr.log"):
            path = self.tmp_path / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def wait(self, timeout: float) -> int:
        return self.proc.wait(timeout=timeout)

    def stop(self) -> None:
        if self.proc is not None:
            end_tree(self.proc)
        for handle in self._outputs:
            handle.close()
        self._outputs = ()


class Client:
    """Talks to one setup process (directly or through Caddy) with a session."""

    def __init__(self, port: int, *, host="127.0.0.1", ssl_context=None):
        self.port = port
        self.host = host
        self.ssl_context = ssl_context
        self.session = None

    def call(self, method, path, body=None, headers=None, timeout=60.0) -> Response:
        sent = dict(headers or {})
        if self.session:
            sent.setdefault("Cookie", f"{SESSION_COOKIE}={self.session}")
            sent.setdefault("X-Setup-Session", self.session)
        return request(self.port, method, path, body, sent, host=self.host,
                       ssl_context=self.ssl_context, timeout=timeout)

    def auth(self, token: str, headers=None) -> Response:
        resp = request(self.port, "POST", "/api/setup/auth/", {"token": token}, headers, host=self.host,
                       ssl_context=self.ssl_context)
        if resp.status == 200:
            self.session = resp.json()["session_token"]
        return resp

    def submit(self, answers: dict) -> Response:
        return self.call("POST", "/api/setup/submit/", answers)


def fields_of(resp: Response) -> dict:
    """The field errors of a 400 ``{"error": "invalid", "fields": {...}}``."""
    assert resp.status == 400, resp.raw
    body = resp.json()
    assert body["error"] == "invalid", body
    return body["fields"]


def manager_answers(data_root: Path, port: int, database=None, admin=None) -> dict:
    """A valid ``manager`` submit: loopback bind, SQLite unless given."""
    return {
        "mode": "manager",
        "server": {"host": "127.0.0.1", "port": port},
        "data_folder": {"path": str(data_root)},
        "database": database or {"engine": "sqlite"},
        "admin": admin or {"username": "farmadmin", "email": "admin@example.com",
                           "password": ADMIN_PASSWORD},
    }
