# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Filesystem-trust bridge: manager writes the local worker config directly.

When the setup wizard topology is ``manager_worker``, the manager must
bootstrap the co-located worker by writing its ``config.json`` with
enrollment data (API token, certificate fingerprint, manager URL and ID).

**FR-FT1** — The manager uses its OWN atomic-write utility.  Nothing
from ``worker/`` is imported.

**FR-FT3** — Durable write ordering:
  1. ``json.dump`` to temp file (``tempfile.mkstemp`` in same dir)
  2. ``os.fsync()`` the temp file descriptor
  3. ``os.replace()`` temp → final path
  4. ``os.fsync()`` parent directory (POSIX only; skipped on Windows)
"""

import json
import logging
import os
import platform
import tempfile
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"


def get_worker_config_path() -> Path:
    """Return the path to the worker's ``config.json``.

    Uses the same per-OS data directory convention as the worker
    agent, duplicated here to satisfy FR-FT1 (no worker imports).

    Creates the directory tree if it does not exist.

    Env override: ``SETHLANS_WORKER_DATA_DIR`` (must be absolute).

    Platform defaults:
      - Windows:  ``%LOCALAPPDATA%/Sethlans/worker/``
      - macOS:    ``~/Library/Application Support/Sethlans/worker/``
      - Linux:    ``$XDG_DATA_HOME/sethlans/worker/``
                  (fallback ``~/.local/share/sethlans/worker/``)
    """
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / CONFIG_FILENAME


def write_worker_config(
    config_path: Path,
    api_token: str,
    cert_fingerprint: str,
    manager_url: str,
    manager_id: str,
) -> None:
    """Persist enrollment data into the worker's ``config.json``.

    Parses *manager_url* to extract host and port, builds the
    canonical JSON structure, and writes it using the FR-FT3 durable
    write protocol.

    If *config_path* already exists, its contents are read and the
    ``manager`` and ``enrollment`` keys are merged on top — existing
    keys in other sections are preserved.
    """
    parsed = urllib.parse.urlparse(manager_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080

    new_data = {
        "manager": {
            "api_token": api_token,
            "cert_fingerprint": cert_fingerprint,
            "manager_id": manager_id,
            "host": host,
            "port": port,
        },
        "enrollment": {
            "wizard_complete": True,
        },
    }

    merged = _merge_existing(config_path, new_data)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    _durable_write(config_path, merged)

    logger.info(
        "Worker config written to %s (manager_id=%s)",
        config_path, manager_id,
    )


# ---- Internal helpers ------------------------------------------------


def _resolve_data_dir() -> Path:
    """Compute the per-OS worker data directory.

    Mirrors ``worker/sethlans_worker_agent/config_store/paths.py``
    without importing it (FR-FT1).
    """
    env_override = os.environ.get("SETHLANS_WORKER_DATA_DIR")
    if env_override:
        p = Path(env_override)
        if not p.is_absolute():
            raise ValueError(
                f"SETHLANS_WORKER_DATA_DIR must be absolute, "
                f"got: {env_override}"
            )
        return p

    system = platform.system()

    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            userprofile = os.environ.get("USERPROFILE")
            if userprofile:
                base = os.path.join(
                    userprofile, "AppData", "Local",
                )
            else:
                base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "Sethlans" / "worker"

    if system == "Darwin":
        return (
            Path.home() / "Library" / "Application Support"
            / "Sethlans" / "worker"
        )

    # Linux / other POSIX
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sethlans" / "worker"
    return Path.home() / ".local" / "share" / "sethlans" / "worker"


def _merge_existing(config_path: Path, new_data: dict) -> dict:
    """Read existing config and overlay *new_data* on top.

    Keys outside ``manager`` and ``enrollment`` are preserved.
    Returns the merged dict ready for writing.
    """
    existing: dict = {}
    if config_path.exists():
        try:
            text = config_path.read_text(encoding="utf-8")
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Could not read existing worker config at %s: %s",
                config_path, exc,
            )

    existing.update(new_data)
    return existing


def _durable_write(path: Path, data: dict) -> None:
    """FR-FT3: atomic, fsync-durable JSON write.

    1. Write JSON to temp file (same directory).
    2. ``os.fsync()`` the temp fd.
    3. ``os.replace()`` temp → final.
    4. ``os.fsync()`` parent directory (POSIX only).
    5. ``os.chmod(0o600)`` on POSIX.
    """
    parent = str(path.parent)

    fd, tmp_path = tempfile.mkstemp(
        dir=parent, prefix=".config_", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())

        os.replace(tmp_path, str(path))

        # From here, data is safely on disk — remaining steps are
        # best-effort hardening (concurrency review fix).
        _harden_permissions(path, parent)

    except Exception:
        # Clean up the temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _harden_permissions(path: Path, parent: str) -> None:
    """Best-effort post-write hardening: fsync dir + restrict perms."""
    try:
        if platform.system() == "Windows":
            import subprocess as _sp
            _sp.run(
                ["icacls", str(path), "/inheritance:r",
                 "/grant:r", f"{os.getlogin()}:F"],
                capture_output=True, check=False,
            )
        else:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            os.chmod(str(path), 0o600)
    except OSError:
        logger.warning("Post-write hardening failed for %s", path)
