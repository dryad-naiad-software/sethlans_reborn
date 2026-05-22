# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard PyInstaller bundle smoke harness.

Runs build-time guards against the wizard bundle: forbidden-module
absence, common-passwords resource integrity, bundle size ceiling,
and a spawn-and-poll HTTP smoke. See ``wizard_smoke_README.md`` for
the full pipeline description and acceptance-criterion mapping.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

# Expose tools/ (sibling helpers) and the repo root (so the wizard
# package is importable for ``COMMON_PASSWORDS_SHA256`` — issue #190).
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from _data_dir_smoke import check_data_dir_alignment  # noqa: E402
from _resolver_smoke import check_manager_exe_resolver  # noqa: E402
from _wizard_smoke_helpers import (  # noqa: E402
    bundle_size_bytes, check_common_passwords_resource,
    check_health_endpoint, check_manager_manage_mode,
    dump_logs, err, http_get_ok, install_wall_clock_watchdog,
    terminate, wait_for_port_file,
)
from wizard.sethlans_wizard.password_validators import (  # noqa: E402
    COMMON_PASSWORDS_SHA256,
)

FORBIDDEN_NAMES = ("django", "workers", "psycopg", "pymysql")
SIZE_LIMIT_BYTES = 95 * 1024 * 1024
COMMON_PASSWORDS_FILENAME = "common-passwords.txt"
STARTUP_BUDGET_SECONDS = 30
WALL_CLOCK_BUDGET_SECONDS = 60
PORT_FILE_POLL_TIMEOUT_SECONDS = 15
POLL_INTERVAL_SECONDS = 1.0


def check_bundle_introspection(bundle: pathlib.Path) -> bool:
    """AC-B2: assert no forbidden module name appears under bundle."""
    leaks = []
    for name in FORBIDDEN_NAMES:
        hits = list(bundle.rglob(name))
        if hits:
            leaks.append((name, hits))
    if leaks:
        err("--- forbidden bundle entries detected (AC-B2) ---")
        for name, hits in leaks:
            err(f"  {name}:")
            for hit in hits:
                err(f"    {hit}")
        err("AC-B2 FAILED: wizard bundle leaked forbidden modules")
        return False
    print(
        "AC-B2 passed: none of "
        + ", ".join(FORBIDDEN_NAMES)
        + f" found in {bundle}"
    )
    return True


def check_bundle_size(bundle: pathlib.Path) -> bool:
    """NF-4 / DEVOPS-MED-11: assert bundle <= 95 MB."""
    size = bundle_size_bytes(bundle)
    print(f"Wizard bundle size: {size} bytes (ceiling {SIZE_LIMIT_BYTES})")
    if size > SIZE_LIMIT_BYTES:
        err(f"--- top 10 largest files in {bundle} ---")
        files = [
            (p.stat().st_size, p)
            for p in bundle.rglob("*")
            if p.is_file()
        ]
        files.sort(reverse=True)
        for sz, path in files[:10]:
            err(f"  {sz / 1024 / 1024:8.2f} MB  {path}")
        err(
            f"NF-4 FAILED: wizard bundle size {size} bytes "
            f"exceeds {SIZE_LIMIT_BYTES} bytes"
        )
        return False
    print("NF-4 passed")
    return True


def _provision_smoke_dir(tmp_root: pathlib.Path) -> pathlib.Path:
    """Write .setup_token + .ipc_secret into <tmp_root>/wizard/."""
    wizard_dir = tmp_root / "wizard"
    wizard_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    secret = secrets.token_hex(32)
    token_path = wizard_dir / ".setup_token"
    secret_path = wizard_dir / ".ipc_secret"
    token_path.write_text(token, encoding="utf-8")
    secret_path.write_text(secret, encoding="utf-8")
    if sys.platform != "win32":
        # FR-L3a / FR-L4a: chmod 600. On Windows %TEMP% ACLs already
        # restrict to the current user, so the chmod is unnecessary
        # (and ineffectual against the Windows file ACL model).
        os.chmod(token_path, 0o600)
        os.chmod(secret_path, 0o600)
    return wizard_dir


def _poll_until_ok(
    url: str, proc: subprocess.Popen,
    started: float,
    log_out: pathlib.Path, log_err: pathlib.Path,
) -> bool:
    """Poll ``GET url`` until 200 within STARTUP_BUDGET_SECONDS."""
    deadline = time.monotonic() + STARTUP_BUDGET_SECONDS
    while time.monotonic() < deadline:
        if http_get_ok(url):
            elapsed = time.monotonic() - started
            print(f"AC-B4 passed: GET {url} -> 200 in {elapsed:.1f}s")
            return True
        if proc.poll() is not None:
            elapsed = time.monotonic() - started
            err(
                f"AC-B4 FAILED: wizard exited (code {proc.poll()}) "
                f"before HTTP 200 (elapsed {elapsed:.1f}s)"
            )
            dump_logs(log_out, log_err)
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
    elapsed = time.monotonic() - started
    err(
        f"AC-B4 FAILED: GET {url} did not return 200 within "
        f"{STARTUP_BUDGET_SECONDS}s (elapsed {elapsed:.1f}s)"
    )
    dump_logs(log_out, log_err)
    return False


def smoke_spawn(bundle: pathlib.Path, port: int) -> bool:
    """AC-B4: spawn wizard, poll port file, confirm GET / returns 200."""
    exe_name = "run_wizard.exe" if sys.platform == "win32" else "run_wizard"
    exe = bundle / exe_name
    if not exe.is_file():
        err(f"AC-B4 FAILED: wizard executable not found at {exe}")
        return False

    tmp_root = pathlib.Path(
        tempfile.mkdtemp(prefix="sethlans-wizard-smoke-")
    )
    wizard_dir = _provision_smoke_dir(tmp_root)

    env = os.environ.copy()
    env["SETHLANS_WIZARD_PORT"] = str(port)
    env["SETHLANS_DATA_DIR"] = str(tmp_root)

    log_out = tmp_root / "wizard-stdout.log"
    log_err = tmp_root / "wizard-stderr.log"

    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    started = time.monotonic()
    with open(log_out, "w", encoding="utf-8") as out_fh, \
            open(log_err, "w", encoding="utf-8") as err_fh:
        proc = subprocess.Popen(
            [str(exe)],
            stdout=out_fh, stderr=err_fh, env=env, **popen_kwargs,
        )
        try:
            # Issue #170: wizard subprocess writes its loopback port
            # to ``loopback_port`` (the launcher writes ``port`` after
            # Caddy binds, but the smoke spawns the wizard alone).
            port_file = wizard_dir / "loopback_port"
            chosen_port = wait_for_port_file(
                port_file, proc, PORT_FILE_POLL_TIMEOUT_SECONDS,
            )
            if chosen_port is None:
                elapsed = time.monotonic() - started
                err(
                    "AC-B4 FAILED: wizard did not write port file at "
                    f"{port_file} within "
                    f"{PORT_FILE_POLL_TIMEOUT_SECONDS}s "
                    f"(elapsed {elapsed:.1f}s, exit code {proc.poll()})"
                )
                dump_logs(log_out, log_err)
                return False
            if chosen_port != port:
                err(
                    f"AC-B4 WARN: wizard bound port {chosen_port}, "
                    f"expected {port} (SETHLANS_WIZARD_PORT pin); "
                    "polling actual port from file"
                )
            # Issue #170: standalone wizard speaks plain HTTP. Caddy
            # in front (production) terminates TLS; the smoke skips
            # Caddy and connects to the loopback HTTP listener.
            base_url = f"http://127.0.0.1:{chosen_port}"
            url = base_url + "/"
            if not _poll_until_ok(url, proc, started, log_out, log_err):
                return False
            # Issue #160: launcher health probe target. Both AC-B4 and
            # the health check must pass for the installer to ship.
            return check_health_endpoint(base_url, log_out, log_err)
        finally:
            terminate(proc)
            shutil.rmtree(tmp_root, ignore_errors=True)


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bundle", default="dist/wizard",
        help="Path to wizard PyInstaller bundle (default: dist/wizard)",
    )
    parser.add_argument(
        "--port", type=int, default=8100,
        help="SETHLANS_WIZARD_PORT pin (default: 8100)",
    )
    parser.add_argument(
        "--skip-spawn", action="store_true",
        help="Run only AC-B2 + NF-4 (skip the spawn-and-poll smoke).",
    )
    parser.add_argument(
        "--skip-resolver-smoke", action="store_true",
        help=(
            "Skip the issue #192 resolver harness "
            "(check_manager_exe_resolver). Useful for iterative dev "
            "runs that haven't rebuilt launcher/manager bundles."
        ),
    )
    parser.add_argument(
        "--skip-data-dir-smoke", action="store_true",
        help=(
            "Skip the issue #195 data-dir alignment harness "
            "(check_data_dir_alignment). Pure-Python guard against "
            "the launcher reintroducing the legacy /manager append."
        ),
    )
    return parser


def _run_static_checks(bundle: pathlib.Path, args: argparse.Namespace) -> bool:
    """Run every non-spawn check; return False on first failure.

    Factored out of ``main`` to stay under max-complexity 10.
    """
    if not check_bundle_introspection(bundle):
        return False
    if not check_common_passwords_resource(
        bundle, COMMON_PASSWORDS_FILENAME, COMMON_PASSWORDS_SHA256,
    ):
        return False
    if not check_bundle_size(bundle):
        return False
    if not check_manager_manage_mode(bundle):
        return False
    if args.skip_resolver_smoke:
        print("--skip-resolver-smoke: #192 resolver harness skipped")
    elif not check_manager_exe_resolver(bundle):
        return False
    if args.skip_data_dir_smoke:
        print("--skip-data-dir-smoke: #195 data-dir harness skipped")
    elif not check_data_dir_alignment():
        return False
    return True


def main() -> int:
    args = _build_argparser().parse_args()

    bundle = pathlib.Path(args.bundle).resolve()
    if not bundle.is_dir():
        err(f"ERROR: wizard bundle not found at {bundle}")
        return 2

    install_wall_clock_watchdog(WALL_CLOCK_BUDGET_SECONDS)

    if not _run_static_checks(bundle, args):
        return 1
    if args.skip_spawn:
        print("--skip-spawn: AC-B4 spawn smoke skipped")
        return 0
    if not smoke_spawn(bundle, args.port):
        return 1
    print("All wizard smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
