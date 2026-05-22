# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Helpers for ``tools/wizard_smoke.py``.

Split out so the wizard smoke entry-point stays under the 300-line
project limit (CLAUDE.md). Pure-function helpers, no globals beyond
the constants imported at use-site.
"""

from __future__ import annotations

import hashlib
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


def err(msg: str) -> None:
    """stderr print shortcut."""
    print(msg, file=sys.stderr)


def check_common_passwords_resource(
    bundle: pathlib.Path,
    filename: str,
    expected_sha256: str,
) -> bool:
    """Issue #190: assert common-passwords.txt is bundled with the right hash.

    PyInstaller's static walker copies ``.py`` files inside packages
    but not arbitrary data resources, so a stray spec edit can silently
    drop ``wizard/sethlans_wizard/data/common-passwords.txt`` from the
    bundle. At runtime that surfaces as
    ``common_passwords_resource_invalid`` on the admin-user step and
    blocks first-run setup. This check fails the build before shipping.

    Uses ``pathlib.rglob`` (mirroring the AC-B2 style) so the harness
    is agnostic to where PyInstaller places the file under the bundle
    (one-dir layouts put data files under ``_internal/``; the exact
    path is an implementation detail of PyInstaller). The SHA-256 is
    supplied by the caller from
    ``wizard.sethlans_wizard.password_validators`` so the smoke can
    never drift from the runtime integrity check.
    """
    hits = [p for p in bundle.rglob(filename) if p.is_file()]
    if not hits:
        err(f"--- {filename} missing from bundle ---")
        err(
            f"Issue #190 FAILED: {filename} not found anywhere under "
            f"{bundle}. The admin-user step will fail with "
            "common_passwords_resource_invalid on first run. "
            "Check wizard.spec declares the resource in datas=."
        )
        return False
    # If somehow multiple copies got bundled, every one of them must
    # match. Surface the count for diagnosis.
    if len(hits) > 1:
        err(
            f"Issue #190 WARN: {len(hits)} copies of {filename} found "
            f"under {bundle}; verifying every copy"
        )
    for hit in hits:
        data = hit.read_bytes()
        if not data:
            err(
                f"Issue #190 FAILED: {hit} is empty (expected the "
                "bundled common-passwords list)"
            )
            return False
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            err(
                f"Issue #190 FAILED: {hit} SHA-256 mismatch "
                f"(expected {expected_sha256}, got {actual})"
            )
            return False
    print(
        f"Issue #190 passed: {filename} bundled at {hits[0]} with "
        "matching SHA-256"
    )
    return True


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
    """Poll <data_dir>/wizard/port until written; return port int.

    Used by AC-B4 to read the port the wizard actually chose rather
    than assuming the SETHLANS_WIZARD_PORT pin. Exercises the real
    ``bootstrap.write_port_file()`` code path so a regression there
    surfaces immediately. Returns ``None`` if the wizard exits before
    writing the file or the timeout elapses.
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


def http_get_ok(url: str) -> bool:
    """GET url with TLS verification disabled (self-signed cert)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=2, context=ctx) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def http_get_json(url: str) -> tuple[int, dict | None]:
    """GET url with self-signed-cert tolerance; return (status, parsed).

    Used by the ``/api/health/`` smoke check (issue #160). Returns
    ``(0, None)`` on transport failure so callers can distinguish
    "server not yet up" from "server up but bad envelope".
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=2, context=ctx) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return resp.status, None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return 0, None


def check_health_endpoint(
    base_url: str,
    log_out: pathlib.Path,
    log_err: pathlib.Path,
) -> bool:
    """Issue #160: assert ``GET /api/health/`` returns 200 + envelope.

    The launcher's cold-boot probe needs ``{boot_id, version}`` keys; a
    regression where the wizard stops serving this route (or returns
    the wrong envelope) would break first-run on every fresh install.
    Catching it here keeps the failure at build time, not user time.
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


def dump_logs(out_path: pathlib.Path, err_path: pathlib.Path) -> None:
    """Print the wizard's captured stdout/stderr after a failure."""
    for label, path in (("stdout", out_path), ("stderr", err_path)):
        err(f"--- wizard {label} ---")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            err(content if content else "(empty)")
        except OSError:
            err("(log file missing)")


def terminate(proc: subprocess.Popen) -> None:
    """Best-effort SIGTERM->SIGKILL escalation."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def install_wall_clock_watchdog(seconds: int) -> None:
    """Hard wall-clock budget (AC-B4 / DEVOPS-v22-MED-2).

    POSIX uses SIGALRM; Windows lacks it, so a daemon thread fires
    ``os._exit`` to force the process down even mid-syscall.
    """
    def _bail() -> None:
        err(
            f"WALL-CLOCK BUDGET EXCEEDED: smoke step ran longer than "
            f"{seconds}s; aborting."
        )
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


def check_manager_manage_mode(bundle: pathlib.Path) -> bool:
    """Issue #191: ``run_manager --manage migrate --check`` must reach Django.

    Asserts ``returncode in (0, 1)`` (Django's migrate --check exit
    codes) AND neither ``unrecognized arguments`` nor ``error:
    argument`` (argparse-failure fingerprints — exit 2 from argparse
    was the exact #191 symptom). Skips if no manager bundle present so
    wizard-only dev iterations still pass.
    """
    manager_root = bundle.parent / "manager"
    if not manager_root.is_dir():
        print(f"#191 SKIPPED: no manager bundle at {manager_root}")
        return True
    exe_name = "run_manager.exe" if sys.platform == "win32" else "run_manager"
    candidates = list(manager_root.rglob(exe_name))
    if not candidates:
        err(f"#191 FAILED: {exe_name} not found under {manager_root}")
        return False
    exe = candidates[0]
    print(f"#191 invoking {exe} --manage migrate --check")
    try:
        result = subprocess.run(
            [str(exe), "--manage", "migrate", "--check"],
            capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        err("#191 FAILED: --manage migrate --check timed out")
        return False
    fingerprints = (b"unrecognized arguments", b"error: argument")
    bad_stderr = any(fp in result.stderr for fp in fingerprints)
    if result.returncode not in (0, 1) or bad_stderr:
        err(f"#191 FAILED: rc={result.returncode}, stderr={result.stderr!r}")
        return False
    print(f"#191 passed: --manage reached Django (rc={result.returncode})")
    return True
