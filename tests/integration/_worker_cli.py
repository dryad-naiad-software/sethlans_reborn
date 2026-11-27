# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Run the real worker program's one-shot setup commands as a child process (setup-rebuild FR-WK-1).

``WorkerCli(tmp_path)`` gives the child its own scratch home (``LOCALAPPDATA``,
``APPDATA``, ``USERPROFILE``, ``HOME``, ``XDG_*``) and settings folder
(``SETHLANS_SETTINGS_DIR``), and no other ``SETHLANS_*`` variable of this
process (no ``SETHLANS_COMPUTE_*``, ``SETHLANS_UNREAL_ENABLED`` or worker data
folder override) and no proxy variable, so nothing it writes or reads is this
machine's. The worker's
``config.json`` and ``logs/worker.log`` are then under ``<settings>/worker``.

``run(command, request)`` sends ``request`` as JSON on stdin and returns
``(exit code, answer, stdout text, stderr text)``; ``answer`` is the one JSON
object stdout must hold (``None`` when stdout is not exactly one JSON line).

``start_agent(extra_env)`` starts the real agent itself (no setup command)
with the same scratch environment plus ``extra_env``; its stdout and stderr
go to ``agent_output`` (``agent_text()``). ``stop_agent(proc)`` ends it: on
Windows the kill also ends its Caddy (the supervisor's Job Object), on POSIX
SIGTERM runs the agent's own shutdown.

Module name starts with an underscore so pytest does not collect it.
"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

RUN_WORKER = Path(__file__).resolve().parents[2] / 'worker' / 'run_worker.py'
TIMEOUT = 60  # setup mode gives --setup-apply 30 s; this only stops a hung child failing the run forever
_USER_FOLDERS = ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME',
                 'XDG_CACHE_HOME')
_PROXY_VARIABLES = ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')  # the child talks to 127.0.0.1 directly


def _one_object(stdout: str):
    lines = stdout.splitlines()
    if len(lines) != 1:
        return None
    try:
        answer = json.loads(lines[0])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def free_ports(count: int):
    """``count`` distinct loopback ports that were free a moment ago."""
    ports = set()
    while len(ports) < count:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            ports.add(sock.getsockname()[1])
    return sorted(ports)


class WorkerCli:
    def __init__(self, tmp_path: Path):
        self.home = tmp_path / 'cli-home'
        self.settings = tmp_path / 'cli-settings'
        self.home.mkdir()
        self.worker_dir = self.settings / 'worker'
        self.config_path = self.worker_dir / 'config.json'
        self.log_path = self.worker_dir / 'logs' / 'worker.log'
        self.agent_output = tmp_path / 'agent-output.txt'

    def env(self) -> dict:
        env = {name: value for name, value in os.environ.items()
               if not name.upper().startswith('SETHLANS_') and name.lower() not in _PROXY_VARIABLES}
        for name in _USER_FOLDERS:
            env[name] = str(self.home)
        env['SETHLANS_SETTINGS_DIR'] = str(self.settings)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return env

    def run(self, command: str, request: dict):
        done = subprocess.run([sys.executable, str(RUN_WORKER), command], input=json.dumps(request).encode('utf-8'),
                              capture_output=True, env=self.env(), cwd=str(self.home), timeout=TIMEOUT)
        stdout = done.stdout.decode('utf-8', 'replace')
        return done.returncode, _one_object(stdout), stdout, done.stderr.decode('utf-8', 'replace')

    def log_text(self) -> str:
        return self.log_path.read_text(encoding='utf-8') if self.log_path.exists() else ''

    def start_agent(self, extra_env: dict) -> subprocess.Popen:
        env = dict(self.env(), PYTHONUNBUFFERED='1', **extra_env)
        with open(self.agent_output, 'wb') as out:  # the child keeps its own handle
            return subprocess.Popen([sys.executable, str(RUN_WORKER)], env=env, cwd=str(self.home),
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)

    @staticmethod
    def stop_agent(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:  # pragma: no cover - a stuck shutdown
                proc.kill()
                proc.wait(timeout=TIMEOUT)

    def agent_text(self) -> str:
        return self.agent_output.read_text(encoding='utf-8', errors='replace') if self.agent_output.exists() else ''
