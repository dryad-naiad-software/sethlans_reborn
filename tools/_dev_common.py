# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the ``tools/dev_*.py`` harness scripts.

The five dev scripts (``dev_wizard.py``, ``dev_manager.py``,
``dev_launcher.py``, ``dev_worker.py``, ``dev_clean.py``) share these
cross-cutting helpers:

* :func:`load_dotenv`     — minimal stdlib ``.env`` loader.
* :func:`pick_free_port`  — try-bind port picker over a candidate list.
* :func:`ensure_dev_cert` — generate / reuse a self-signed dev cert.
* :func:`print_banner`    — pre-spawn banner with credentials + URLs.
* :func:`stream_subprocess` — spawn child, prefix lines, forward SIGINT.
* :func:`resolve_data_root` — honour ``SETHLANS_DEV_DATA_ROOT``.
* :func:`generate_token`  — URL-safe random token (FR-L3 style).

Stdlib + ``cryptography`` only (the latter via lazy import in
:func:`ensure_dev_cert`).
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "temp" / "dev-data"

ENV_DATA_ROOT = "SETHLANS_DEV_DATA_ROOT"
ENV_TLS_REUSE = "SETHLANS_DEV_TLS_REUSE"
ENV_LOG_LEVEL = "SETHLANS_DEV_LOG_LEVEL"
ENV_WIZARD_PORT = "SETHLANS_DEV_WIZARD_PORT"
ENV_MANAGER_PORT = "SETHLANS_DEV_MANAGER_PORT"
ENV_WORKER_UI_PORT = "SETHLANS_DEV_WORKER_UI_PORT"


# ---------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------

def load_dotenv(path: Optional[Path] = None) -> dict[str, str]:
    """Populate ``os.environ`` from a ``.env`` file (shell wins).

    Lines beginning with ``#`` are comments. ``KEY=value`` lines set
    :data:`os.environ` only when the variable is not already present —
    so values exported in the parent shell take precedence over file
    defaults. Missing file is a no-op.

    Returns the dict of values actually applied (handy for debug logs).
    """
    target = Path(path) if path is not None else PROJECT_ROOT / ".env"
    applied: dict[str, str] = {}
    if not target.is_file():
        return applied
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return applied
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


# ---------------------------------------------------------------------
# Free-port picker
# ---------------------------------------------------------------------

def pick_free_port(
    candidates: Sequence[int], host: str = "127.0.0.1",
) -> Optional[int]:
    """Return the first port from *candidates* that ``bind()`` accepts.

    Probes via a transient TCP socket; the kernel-side reservation is
    released as soon as the ``with`` block exits. Returns ``None`` when
    every candidate is in use.
    """
    for port in candidates:
        try:
            with socket.socket(
                socket.AF_INET, socket.SOCK_STREAM,
            ) as sock:
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEADDR, 1,
                )
                sock.bind((host, port))
            return port
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------

def resolve_data_root() -> Path:
    """Return the dev data root, honouring ``SETHLANS_DEV_DATA_ROOT``.

    Relative paths resolve against the project root; absolute paths
    pass through. Directory creation is deferred to the per-component
    helpers.
    """
    raw = os.environ.get(ENV_DATA_ROOT)
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        return candidate
    return DEFAULT_DATA_ROOT


# ---------------------------------------------------------------------
# Self-signed dev cert (reuse-by-default)
# ---------------------------------------------------------------------

def ensure_dev_cert(tls_dir: Path) -> tuple[Path, Path]:
    """Generate or reuse a self-signed cert under *tls_dir*.

    Reuses ``cert.pem`` + ``key.pem`` when both exist
    (``SETHLANS_DEV_TLS_REUSE=0`` forces regeneration). Delegates to
    :func:`shared.cert_utils.generate_self_signed_cert` so the cert
    matches what the manager / wizard would produce in production.
    """
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tls_dir / "cert.pem"
    key_path = tls_dir / "key.pem"

    reuse = os.environ.get(ENV_TLS_REUSE, "1").strip().lower()
    reuse_ok = reuse not in ("0", "false", "no", "")

    if reuse_ok and cert_path.is_file() and key_path.is_file():
        return cert_path, key_path

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from shared.cert_utils import generate_self_signed_cert

    generate_self_signed_cert(cert_path, key_path)
    return cert_path, key_path


# ---------------------------------------------------------------------
# Banner printer
# ---------------------------------------------------------------------

def print_banner(title: str, fields: Iterable[tuple[str, str]]) -> None:
    """Print a framed banner to stdout BEFORE the child process spawns.

    ``fields`` is an ordered iterable of (label, value) pairs — labels
    are right-padded to the longest in the set so values line up.
    """
    items = list(fields)
    max_label = max((len(label) for label, _ in items), default=0)
    rule = "=" * max(48, len(title) + 12)
    sys.stdout.write(f"\n{rule}\n")
    sys.stdout.write(f"  {title}\n")
    sys.stdout.write(f"{rule}\n")
    for label, value in items:
        sys.stdout.write(f"  {label.ljust(max_label)}  {value}\n")
    sys.stdout.write("  Press Ctrl+C to stop.\n")
    sys.stdout.write(f"{rule}\n\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------
# Subprocess streamer
# ---------------------------------------------------------------------

def _stream_pipe(pipe, prefix: str, dest) -> None:
    """Background pump: forward *pipe* lines to *dest* with *prefix*."""
    try:
        for raw_line in iter(pipe.readline, b""):
            try:
                line = raw_line.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — defensive
                line = repr(raw_line)
            dest.write(f"{prefix} {line}")
            dest.flush()
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _terminate_child(proc: "subprocess.Popen[bytes]", prefix: str) -> int:
    """Best-effort terminate -> wait -> kill cascade. Returns final rc."""
    sys.stderr.write(f"\n{prefix} caught Ctrl+C; terminating\n")
    sys.stderr.flush()
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        return proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        pass
    sys.stderr.write(f"{prefix} did not exit in 5s; sending SIGKILL\n")
    sys.stderr.flush()
    try:
        proc.kill()
    except OSError:
        pass
    try:
        return proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        return -9


def stream_subprocess(
    cmd: Sequence[str],
    *,
    prefix: str,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
) -> int:
    """Run *cmd*, prefix each output line with *prefix*, return rc.

    Stdout + stderr are line-merged into the parent's streams. SIGINT
    (Ctrl+C) is forwarded to the child via :meth:`Popen.terminate`;
    SIGKILL after a 5 s grace.
    """
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=proc_env,
        cwd=str(cwd) if cwd else None,
        bufsize=0,
    )

    threads = [
        threading.Thread(
            target=_stream_pipe, args=(proc.stdout, prefix, sys.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=_stream_pipe, args=(proc.stderr, prefix, sys.stderr),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()

    try:
        rc: Optional[int] = proc.wait()
    except KeyboardInterrupt:
        rc = _terminate_child(proc, prefix)

    for t in threads:
        t.join(timeout=2.0)

    return rc if rc is not None else 1


# ---------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------

def generate_token(nbytes: int = 32) -> str:
    """URL-safe random token. Matches FR-L3 / FR-L4 conventions."""
    return secrets.token_urlsafe(nbytes)
