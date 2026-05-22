# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Drive-through harness for ``tools/install_smoke.py`` (issue #197).
Authenticates wizard, walks steps, polls sentinel, probes manager
/api/health/. Pure stdlib. Endpoint shapes mirror live handlers in
``wizard/sethlans_wizard/handlers/*.py``.
"""

from __future__ import annotations

import json
import pathlib
import ssl
import subprocess
import time
import urllib.error
import urllib.request

from _wizard_smoke_helpers import (
    check_health_endpoint, err, http_get_ok, wait_for_port_file,
)

PORT_FILE_POLL_TIMEOUT_SECONDS = 30
SENTINEL_POLL_TIMEOUT_SECONDS = 90
HEALTH_POLL_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 1.0
SENTINEL_MAX_AGE_SECONDS = 60
# Non-dotted persistent copy (#172) — the wizard subprocess consumes-
# and-unlinks ``.setup_token`` at startup (FR-W6 / SEC-MED-11) which
# would race the smoke. The non-dotted sibling survives until handoff.
SETUP_TOKEN_FILENAME = "setup_token"
SENTINEL_FILENAME = ".setup_complete"

# Long admin password to survive Django's MinimumLengthValidator
# (>= 9 chars) AND the bundled CommonPasswordValidator (#190 + #196 —
# the bugs this smoke exists to catch). ``manager_worker`` topology
# exercises the full apply pipeline (manager migrate +
# apply_pending_setup + worker enrollment).
ADMIN_USERNAME = "smoke"
ADMIN_EMAIL = "smoke@sethlans.test"
ADMIN_PASSWORD = "Smoke!Install2026XYZ"
WORKER_PASSWORD = "Smoke!Worker2026"


def _read_setup_token(data_dir: pathlib.Path) -> str | None:
    """Return the persistent setup token written by the launcher."""
    token_path = data_dir / "wizard" / SETUP_TOKEN_FILENAME
    deadline = time.monotonic() + PORT_FILE_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if token_path.is_file():
            try:
                token = token_path.read_text(encoding="utf-8").strip()
                if token:
                    return token
            except OSError:
                pass
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _ssl_context() -> ssl.SSLContext:
    """Self-signed-cert-tolerant context (matches wizard_smoke helpers)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _wizard_request(
    base_url: str,
    path: str,
    body: dict,
    session_token: str | None = None,
) -> tuple[int, bytes]:
    """POST JSON to *base_url + path*; return ``(status, body_bytes)``.

    Returns ``(0, b"<error>")`` on transport-level failure.
    """
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if session_token:
        headers["X-Wizard-Session"] = session_token
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST",
    )
    ctx = _ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read() or b""
        except OSError:
            return exc.code, b""
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"transport error: {exc}".encode("utf-8")


def _auth_wizard(base_url: str, setup_token: str) -> str | None:
    """POST /api/wizard/auth/ and return the session token, or None."""
    status, body = _wizard_request(
        base_url, "/api/wizard/auth/", {"token": setup_token},
    )
    if status != 200:
        err(f"auth FAILED: status={status} body={body[:400]!r}")
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        err(f"auth FAILED: response not JSON: {body[:400]!r}")
        return None
    token = payload.get("session_token")
    if not isinstance(token, str) or not token:
        err(f"auth FAILED: no session_token in response: {payload}")
        return None
    return token


def _post_step(
    base_url: str, session_token: str, path: str, body: dict,
) -> bool:
    """POST a single wizard step; surface non-2xx with body."""
    status, raw = _wizard_request(base_url, path, body, session_token)
    if status < 200 or status >= 300:
        err(f"wizard step FAILED: POST {path} -> {status} body={raw[:400]!r}")
        return False
    print(f"wizard step OK: POST {path} -> {status}")
    return True


def _walk_wizard(
    base_url: str, session_token: str, manager_port: int,
    data_dir: pathlib.Path | None = None,
) -> bool:
    """Drive every wizard step. First non-2xx aborts. /api/wizard/done/
    is fire-and-forget (launcher tears down Caddy + wizard immediately
    on marker; HTTP response usually never arrives; sentinel is the
    contract)."""
    admin_body = {
        "username": ADMIN_USERNAME, "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "password_confirm": ADMIN_PASSWORD,
    }
    # #199: pin SQLite path inside data_dir so manager.ini bakes a
    # tempdir path, not %LOCALAPPDATA%\Sethlans\manager\.
    db_name = (
        str(data_dir / "manager" / "sethlans.db") if data_dir else ""
    )
    steps: list[tuple[str, dict]] = [
        ("/api/wizard/topology/", {"topology": "manager_worker"}),
        ("/api/wizard/network/",
         {"bind_host": "127.0.0.1", "bind_port": manager_port}),
        ("/api/wizard/database/", {"engine": "sqlite", "name": db_name}),
        ("/api/wizard/admin-user/", admin_body),
        ("/api/wizard/worker-password/",
         {"use_admin_password": False, "password": WORKER_PASSWORD}),
        ("/api/wizard/verify/", {}),
        ("/api/wizard/pending-setup/", {}),
    ]
    for path, body in steps:
        if not _post_step(base_url, session_token, path, body):
            return False
    status, raw = _wizard_request(
        base_url, "/api/wizard/done/", {}, session_token,
    )
    if status == 200:
        print("wizard step OK: POST /api/wizard/done/ -> 200")
    else:
        print(
            f"wizard step done (soft): -> {status} body={raw[:120]!r} "
            f"(launcher tear-down expected; sentinel is the contract)"
        )
    return True


def _check_sentinel_fresh(target: pathlib.Path) -> bool:
    """Verify ``target`` is non-empty and mtime is within freshness window."""
    try:
        stat = target.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        err(f"sentinel FAILED: {target} is empty (size=0)")
        return False
    age = time.time() - stat.st_mtime
    if age >= SENTINEL_MAX_AGE_SECONDS:
        err(f"sentinel FAILED: {target} mtime stale ({age:.1f}s old)")
        return False
    print(f"sentinel OK: {target} (size={stat.st_size}B, age={age:.1f}s)")
    return True


def _poll_sentinel(
    data_dir: pathlib.Path, launcher_proc: subprocess.Popen,
) -> bool:
    """Poll ``<data_dir>/.setup_complete`` for the apply pipeline."""
    target = data_dir / SENTINEL_FILENAME
    deadline = time.monotonic() + SENTINEL_POLL_TIMEOUT_SECONDS
    started = time.monotonic()
    while time.monotonic() < deadline:
        if target.is_file() and _check_sentinel_fresh(target):
            print(f"sentinel ready in {time.monotonic() - started:.1f}s")
            return True
        if launcher_proc.poll() is not None:
            elapsed = time.monotonic() - started
            err(
                f"sentinel FAILED: launcher exited "
                f"(code {launcher_proc.poll()}) before "
                f".setup_complete written (elapsed {elapsed:.1f}s)"
            )
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
    elapsed = time.monotonic() - started
    err(
        f"sentinel FAILED: {target} did not appear within "
        f"{SENTINEL_POLL_TIMEOUT_SECONDS}s (elapsed {elapsed:.1f}s)"
    )
    return False


def _poll_manager_health(
    manager_port: int,
    log_out: pathlib.Path,
    log_err: pathlib.Path,
) -> bool:
    """Poll ``GET https://127.0.0.1:<manager_port>/api/health/`` for 200."""
    base_url = f"https://127.0.0.1:{manager_port}"
    url = base_url + "/api/health/"
    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT_SECONDS
    started = time.monotonic()
    while time.monotonic() < deadline:
        if http_get_ok(url):
            print(f"health probe OK after {time.monotonic() - started:.1f}s")
            return check_health_endpoint(base_url, log_out, log_err)
        time.sleep(POLL_INTERVAL_SECONDS)
    err(
        f"health FAILED: GET {url} did not return 200 within "
        f"{HEALTH_POLL_TIMEOUT_SECONDS}s"
    )
    return False


def _wait_for_wizard(
    data_dir: pathlib.Path, launcher_proc: subprocess.Popen,
) -> int | None:
    """Wait for the wizard's loopback_port file. Return port or None."""
    port_file = data_dir / "wizard" / "loopback_port"
    chosen = wait_for_port_file(
        port_file, launcher_proc, PORT_FILE_POLL_TIMEOUT_SECONDS,
    )
    if chosen is None:
        err(
            "wizard FAILED: no loopback_port within "
            f"{PORT_FILE_POLL_TIMEOUT_SECONDS}s (launcher exit code: "
            f"{launcher_proc.poll()})"
        )
        return None
    print(f"wizard loopback port: {chosen}")
    return chosen


def drive_install(
    launcher_proc: subprocess.Popen,
    data_dir: pathlib.Path,
    manager_port: int,
    log_out: pathlib.Path,
    log_err: pathlib.Path,
) -> bool:
    """Orchestrate the full smoke (FR-SMOKE4-9). Return True on pass."""
    chosen_port = _wait_for_wizard(data_dir, launcher_proc)
    if chosen_port is None:
        return False

    setup_token = _read_setup_token(data_dir)
    if setup_token is None:
        err(
            "wizard FAILED: setup_token not written within "
            f"{PORT_FILE_POLL_TIMEOUT_SECONDS}s under "
            f"{data_dir / 'wizard'}"
        )
        return False

    base_url = f"http://127.0.0.1:{chosen_port}"
    session_token = _auth_wizard(base_url, setup_token)
    if session_token is None:
        return False
    if not _walk_wizard(
        base_url, session_token, manager_port, data_dir,
    ):
        return False
    if not _poll_sentinel(data_dir, launcher_proc):
        return False
    return _poll_manager_health(manager_port, log_out, log_err)


__all__ = ["drive_install"]
