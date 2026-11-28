# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""General helpers shared by the build-time smoke tools in ``tools/``.

Used by ``setup_smoke.py`` and the install-smoke tools
(``install_smoke.py``, ``_install_smoke_driver.py``,
``_install_smoke_liveness.py``). Stdlib only at import time; ``psutil``
is imported where a process tree is ended.

Every HTTP helper goes through one opener with no proxy (the smokes
only talk to loopback) and no redirects (an answer is the one from the
URL asked), as ``launcher/health_probe.py`` does.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Iterable, Optional

HTTP_TIMEOUT_SECONDS = 2
REDACTED = "<redacted>"
# Argparse-failure fingerprints: exit 2 from argparse was the #191 symptom.
MANAGE_ARGPARSE_FINGERPRINTS = (b"unrecognized arguments", b"error: argument")


def err(msg: str) -> None:
    """stderr print shortcut."""
    print(msg, file=sys.stderr)


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """``text`` with every non-empty secret replaced by ``<redacted>``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def bundle_size_bytes(bundle: pathlib.Path) -> int:
    """Sum of all regular-file sizes under ``bundle`` (recursive)."""
    total = 0
    for path in bundle.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def wait_for_port_file(
    port_file: pathlib.Path,
    proc: subprocess.Popen,
    timeout_seconds: float,
) -> int | None:
    """Poll ``port_file`` until it holds a port number; return it.

    Returns ``None`` if ``proc`` exits before the file is written or the
    timeout elapses.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if port_file.is_file():
            try:
                return int(port_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass
        if proc.poll() is not None:
            return None
        time.sleep(0.25)
    return None


def _insecure_context() -> ssl.SSLContext:
    """TLS context for the self-signed certificate (no verification)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: the 3xx comes back as an ``HTTPError``."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=_insecure_context()),
    _NoRedirect(),
)


def http_get_ok(url: str) -> bool:
    """GET url with TLS verification disabled (self-signed cert)."""
    try:
        with _OPENER.open(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def http_get_json(url: str) -> tuple[int, dict | None]:
    """GET url with self-signed-cert tolerance; return (status, parsed).

    Used by the ``/api/health/`` smoke check (issue #160). Returns
    ``(0, None)`` on transport failure so callers can distinguish
    "server not yet up" from "server up but bad envelope".
    """
    try:
        with _OPENER.open(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return resp.status, None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return 0, None


def http_call(
    url: str,
    method: str = "GET",
    body: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 30.0,
) -> tuple[int, dict, bytes]:
    """One request; return ``(status, lower-cased headers, body bytes)``.

    An HTTP error status (a refused redirect too) is returned like any
    other, with its body; a transport failure gives ``(0, {}, b"")``.
    ``body`` is sent as JSON.
    """
    sent = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        sent.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=sent, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
    except (urllib.error.URLError, OSError):
        return 0, {}, b""


def check_health_endpoint(
    base_url: str,
    log_out: pathlib.Path,
    log_err: pathlib.Path,
) -> bool:
    """Issue #160: assert ``GET /api/health/`` returns 200 + envelope.

    The launcher's cold-boot probe needs ``{boot_id, version}`` keys.
    """
    url = base_url.rstrip("/") + "/api/health/"
    status, payload = http_get_json(url)
    if status != 200:
        err(f"HEALTH FAILED: GET {url} returned status {status}")
        dump_logs(log_out, log_err)
        return False
    if not isinstance(payload, dict):
        err(f"HEALTH FAILED: GET {url} did not return a JSON object")
        dump_logs(log_out, log_err)
        return False
    missing = [k for k in ("boot_id", "version") if not payload.get(k)]
    if missing:
        err(
            f"HEALTH FAILED: GET {url} envelope missing keys: "
            f"{missing} (got {sorted(payload)})"
        )
        dump_logs(log_out, log_err)
        return False
    print(f"HEALTH passed: GET {url} -> 200 with {{boot_id, version}}")
    return True


def dump_logs(*paths: pathlib.Path, redact: Iterable[str] = ()) -> None:
    """Print captured log files after a failure, each under its file name.

    Every secret in ``redact`` prints as ``<redacted>``.
    """
    secrets = [secret for secret in redact if secret]
    for path in paths:
        err(f"--- {path.name} ---")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            err(redact_text(content, secrets) if content else "(empty)")
        except OSError:
            err("(log file missing)")


def terminate(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """End ``proc`` and every process it started (terminate, then kill).

    The descendants are listed before anything is signalled, so a child
    the parent started is not left behind when the parent goes first.
    """
    if proc.poll() is not None:
        return
    import psutil

    errors = (psutil.NoSuchProcess, psutil.AccessDenied, OSError)
    try:
        parent = psutil.Process(proc.pid)
        targets = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for target in targets:
        try:
            target.terminate()
        except errors:
            pass
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for target in alive:
        try:
            target.kill()
        except errors:
            pass
    psutil.wait_procs(alive, timeout=timeout)


def install_wall_clock_watchdog(
    seconds: int, on_expire: Optional[Callable[[], None]] = None,
) -> None:
    """Hard wall-clock budget for a smoke tool.

    When it runs out, ``on_expire`` (if given) runs first, best effort
    (end the processes the tool started, dump logs), then the process
    exits 1. POSIX uses SIGALRM; Windows lacks it, so a daemon thread
    fires ``os._exit`` to force the process down even mid-syscall.
    """
    def _bail() -> None:
        err(
            f"WALL-CLOCK BUDGET EXCEEDED: smoke step ran longer than "
            f"{seconds}s; aborting."
        )
        if on_expire is not None:
            try:
                on_expire()
            except Exception as exc:  # noqa: BLE001 - exiting anyway
                err(f"watchdog clean-up failed: {exc!r}")
        sys.stderr.flush()
        sys.stdout.flush()
        os._exit(1)

    if sys.platform != "win32":
        def _handler(signum, frame):  # noqa: ARG001
            _bail()
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(seconds)
    else:
        timer = threading.Timer(seconds, _bail)
        timer.daemon = True
        timer.start()


def manage_mode_problems(returncode: int, stderr: bytes) -> list[str]:
    """Issue #191: ``run_manager --manage migrate --check`` must reach Django.

    Django's ``migrate --check`` exits 0 or 1; argparse's refusal (exit 2,
    ``unrecognized arguments`` / ``error: argument``) was the #191 symptom.
    """
    problems = []
    if returncode not in (0, 1):
        problems.append(f"exited {returncode}, expected 0 or 1")
    hits = [fp.decode() for fp in MANAGE_ARGPARSE_FINGERPRINTS if fp in stderr]
    if hits:
        problems.append(f"argparse refused the arguments ({', '.join(hits)})")
    return problems
