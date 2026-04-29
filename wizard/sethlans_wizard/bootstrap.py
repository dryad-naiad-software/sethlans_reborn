# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Startup helpers for the standalone wizard entry point.

Lives next to the other wizard modules so the top-level
``wizard/run_wizard.py`` script can stay short and dispatch-only.
The functions here implement the FR-W3 port-file write, the FR-W6
secret-file read + unlink, the FR-W11 log rotation, the FR-W17 SIGTERM
graceful-shutdown handler, and the FR-W3 port-scan loop.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional, Tuple

from wizard.sethlans_wizard import ipc, server, shutdown

logger = logging.getLogger("wizard.bootstrap")

WIZARD_SUBDIR = "wizard"
SETUP_TOKEN_FILENAME = ".setup_token"
IPC_SECRET_FILENAME = ".ipc_secret"
# Issue #170: the wizard writes its plain-HTTP loopback port to
# ``loopback_port``; the public-facing ``port`` file is now written
# by the launcher (after Caddy binds) and reports the Caddy public
# TLS port — that's the URL the tray + browser open.
PORT_FILENAME = "loopback_port"
LOG_FILENAME = "wizard.log"
LOG_PREV_FILENAME = "wizard.log.prev"

_LOG_FORMAT = (
    "[%(asctime)s] [%(levelname)s] [%(name)s] "
    "[pid=%(process)d tid=%(thread)d] %(message)s"
)
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def wizard_subdir(data_dir: Path) -> Path:
    """Return ``<data_dir>/wizard/`` (matches FR-CFG2 / FR-CFG6 layout)."""
    return Path(data_dir) / WIZARD_SUBDIR


def configure_logging(subdir: Path) -> None:
    """Install console + file handlers per FR-W11.

    Renames ``wizard.log`` → ``wizard.log.prev`` (deleting any older
    ``.prev`` first) so the previous run's log is preserved without
    unbounded growth (SEC-LOW-4).
    """
    subdir.mkdir(parents=True, exist_ok=True)
    log_path = subdir / LOG_FILENAME
    prev_path = subdir / LOG_PREV_FILENAME

    try:
        prev_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    if log_path.exists():
        try:
            log_path.replace(prev_path)
        except OSError:
            pass

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    root.addHandler(console)

    try:
        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.INFO)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("Could not open wizard log file %s: %s", log_path, exc)


def read_secrets(subdir: Path) -> Tuple[bytes, bytes]:
    """Read setup token + IPC secret and unlink them (FR-W6, SEC-MED-11).

    Raises ``FileNotFoundError`` (subclass of ``OSError``) when the
    launcher hasn't provisioned a file. Raises ``ValueError`` if a
    file is present but empty after stripping.
    """
    token_path = subdir / SETUP_TOKEN_FILENAME
    secret_path = subdir / IPC_SECRET_FILENAME
    setup_token = ipc.read_secret_file(token_path)
    ipc_secret = ipc.read_secret_file(secret_path)
    if not setup_token:
        raise ValueError(f"setup token at {token_path} is empty")
    if not ipc_secret:
        raise ValueError(f"IPC secret at {secret_path} is empty")
    return setup_token, ipc_secret


def write_port_file(subdir: Path, port: int) -> None:
    """Atomically write the chosen port to ``<subdir>/port`` (FR-CFG2).

    Best-effort; failures are logged but never block startup (the IPC
    ``.wizard_done`` marker is the authoritative source per FR-W3).
    """
    target = subdir / PORT_FILENAME
    tmp = target.with_suffix(target.suffix + ".tmp")
    data = f"{port}\n".encode("ascii")
    try:
        fd = os.open(
            str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
        )
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(target))
    except OSError as exc:
        logger.warning("Could not write wizard port file %s: %s", target, exc)
        return
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass


_signal_count_lock = threading.Lock()
_signal_count = 0


def _reset_signal_count_for_tests() -> None:
    """Test-only: reset the signal-count counter. Production never calls."""
    global _signal_count
    with _signal_count_lock:
        _signal_count = 0


def install_signal_handlers() -> None:
    """Install SIGTERM/SIGINT handler → polite shutdown + escalation.

    Issue #176: the prior handler called ``server.close()`` directly,
    which did NOT reliably break the waitress run loop (observed
    30-minute hang). The new handler signals the wizard's shutdown
    event; ``server.run()`` then unblocks on the main thread, drains,
    and returns normally.

    Second-SIGINT escalation: if the operator presses Ctrl+C twice (or
    SIGTERM races with SIGINT), the second signal calls
    ``os._exit(130)`` (128 + SIGINT) so the process can be force-quit
    without ``taskkill``.

    Safe to install BEFORE the server has bound — both
    :func:`server.shutdown_server` and :func:`server.request_shutdown`
    are no-ops when the slot is empty.
    """
    def _handler(signum, _frame):
        global _signal_count
        with _signal_count_lock:
            _signal_count += 1
            count = _signal_count
        if count >= 2:
            # Operator pressed Ctrl+C a second time — force-exit. We
            # use os._exit (not sys.exit) so atexit handlers and
            # daemon-thread joins don't keep us alive.
            logger.warning(
                "Wizard caught signal %d again (count=%d); force-exit",
                signum, count,
            )
            os._exit(130)
        logger.info(
            "Wizard caught signal %d; requesting graceful shutdown",
            signum,
        )
        # request_shutdown unblocks the main-thread wait in server.run.
        # shutdown_server also closes the listening socket (drains
        # in-flight requests faster).
        server.shutdown_server()

    sigs = [signal.SIGTERM, signal.SIGINT]
    # Issue #176: on Windows, dev scripts (and the launcher) deliver
    # CTRL_BREAK_EVENT for cross-process Ctrl+C — that surfaces as
    # SIGBREAK, NOT SIGINT. Without this hook the default Windows
    # SIGBREAK behaviour hard-terminates the process before any
    # graceful-shutdown path runs.
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        sigs.append(sigbreak)
    for sig in sigs:
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Non-main thread / unsupported platform — non-fatal.
            pass


def start_post_done_threads(data_dir: Path, ipc_secret: bytes) -> None:
    """Spawn the FR-W17 (b)/(c) post-done polling thread.

    Thin wrapper so ``run_wizard.py`` doesn't need to reach into
    :mod:`wizard.sethlans_wizard.shutdown` directly. Idempotent.
    """
    shutdown.start_post_done_threads(Path(data_dir), bytes(ipc_secret))


def serve_with_port_scan(
    app, env_port: Optional[int], subdir: Path,
) -> int:
    """Run waitress, scanning the FR-W3 port range when no env override.

    Returns the process exit code (0 from a normal shutdown, 2 if every
    candidate port is taken or a single-port env override fails to bind).

    Issue #170: TLS plumbing has moved to the launcher's Caddy
    supervisor. The wizard binds plain HTTP on
    :data:`server.WIZARD_BIND_HOST` (loopback) and the launcher's
    Caddy fronts it. ``cert_path`` / ``key_path`` are no longer
    threaded through.
    """
    if env_port is not None:
        candidates: tuple[int, ...] = (env_port,)
    else:
        candidates = tuple(server.PORT_SCAN_RANGE)

    last_error: Optional[OSError] = None
    for port in candidates:
        # Re-stamp the app's wizard_port so the .wizard_done marker
        # carries the port we actually bound (FR-IPC1 / done handler).
        try:
            app._wizard_port = int(port)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        write_port_file(subdir, port)
        try:
            server.run(app, server.WIZARD_BIND_HOST, port)
        except OSError as exc:
            last_error = exc
            logger.warning(
                "Wizard could not bind http://%s:%d/: %s",
                server.WIZARD_BIND_HOST, port, exc,
            )
            if env_port is not None:
                logger.error(
                    "SETHLANS_WIZARD_PORT=%d in use; refusing to scan",
                    env_port,
                )
                return 2
            continue
        return 0

    logger.error(
        "Wizard could not bind any port in %s (last error: %s)",
        list(candidates), last_error,
    )
    return 2
