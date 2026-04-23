# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frozen-environment path resolution for both PyInstaller and source modes.

This module is the single source of truth for resolving application paths
across frozen (PyInstaller one-dir) and source (development) environments.
At build time, the CI pipeline copies this file into both
``manager/sethlans_manager/frozen_paths.py`` and
``worker/sethlans_worker_agent/frozen_paths.py`` so each component can
import it without cross-component ``sys.path`` complexity.

Functions
---------
is_frozen()
    Whether we are running inside a PyInstaller bundle.
get_app_dir()
    Root application / project directory.
get_manager_dir()
    Manager source or bundle directory.
get_worker_dir()
    Worker source or bundle directory.
get_frontend_dist_dir()
    Angular build output directory.
get_data_dir(component)
    OS-conventional per-user data directory for a given component.
get_caddy_path()
    Caddy binary path (frozen installer or dev tree).
"""

import os
import platform
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return ``True`` when running inside a PyInstaller bundle."""
    return getattr(sys, 'frozen', False)


def get_app_dir() -> Path:
    """Return the root application directory.

    Frozen mode (PyInstaller one-dir):
        The directory containing the frozen executable.
    Source mode:
        The project root (parent of ``manager/`` and ``worker/``).
    """
    if is_frozen():
        # In one-dir mode, sys._MEIPASS is the bundle's _internal/
        # directory.  The executable sits one level above _internal/.
        return Path(sys.executable).resolve().parent
    # Source mode: this file lives in shared/, project root is parent.
    return Path(__file__).resolve().parent.parent


def get_manager_dir() -> Path:
    """Return the manager source or bundle directory.

    Frozen mode:
        The directory containing the frozen manager executable
        (install_dir/bin/manager/).
    Source mode:
        ``<project_root>/manager/``.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return get_app_dir() / 'manager'


def get_worker_dir() -> Path:
    """Return the worker source or bundle directory.

    Frozen mode:
        The directory containing the frozen worker executable
        (install_dir/bin/worker/).
    Source mode:
        ``<project_root>/worker/``.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return get_app_dir() / 'worker'


def get_frontend_dist_dir() -> Path:
    """Return the Angular frontend build output directory.

    Frozen mode:
        The bundled frontend dist inside the PyInstaller bundle.
        PyInstaller collects it as data files relative to the
        ``_MEIPASS`` directory.
    Source mode:
        ``<project_root>/manager/frontend/dist/browser/browser/``.
    """
    if is_frozen():
        meipass = Path(getattr(sys, '_MEIPASS', ''))
        return meipass / 'frontend' / 'dist' / 'browser' / 'browser'
    return (
        get_app_dir() / 'manager' / 'frontend'
        / 'dist' / 'browser' / 'browser'
    )


def get_data_dir(component: str) -> Path:
    """Return the OS-conventional per-user data directory.

    Parameters
    ----------
    component : str
        The component name (``'manager'`` or ``'worker'``).

    Returns
    -------
    Path
        A writable directory appropriate for storing mutable state
        (database, config, media, logs, certs, tools, assets, etc.).

    The hierarchy mirrors the existing worker convention in
    ``worker/sethlans_worker_agent/config_store/paths.py``:

    - Windows: ``%LOCALAPPDATA%\\Sethlans\\{component}\\``
    - macOS: ``~/Library/Application Support/Sethlans/{component}/``
    - Linux: ``$XDG_DATA_HOME/sethlans/{component}/``
      (default ``~/.local/share/sethlans/{component}/``)

    An environment variable override is supported per component:
    ``SETHLANS_{COMPONENT}_DATA_DIR`` (e.g., ``SETHLANS_MANAGER_DATA_DIR``
    or ``SETHLANS_WORKER_DATA_DIR``).
    """
    env_key = f"SETHLANS_{component.upper()}_DATA_DIR"
    env_override = os.environ.get(env_key)
    if env_override:
        p = Path(env_override)
        if not p.is_absolute():
            raise ValueError(
                f"{env_key} must be an absolute path, got: {env_override}"
            )
        return p

    system = platform.system()

    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            userprofile = os.environ.get("USERPROFILE")
            if userprofile:
                base = os.path.join(userprofile, "AppData", "Local")
            else:
                base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "Sethlans" / component

    if system == "Darwin":
        return (
            Path.home() / "Library" / "Application Support"
            / "Sethlans" / component
        )

    # Linux / other POSIX
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sethlans" / component
    return Path.home() / ".local" / "share" / "sethlans" / component


def get_caddy_path() -> Path:
    """Return the path to the Caddy binary.

    Frozen mode (PyInstaller one-dir):
        Sits next to the frozen executable in the bundle's ``dist``
        directory — e.g. ``dist/launcher/caddy`` on Unix or
        ``dist/launcher/caddy.exe`` on Windows. ``packaging/pyinstaller/
        launcher.spec`` adds the binary to ``binaries=[...]`` so
        PyInstaller copies it to that location at build time.
    Source mode:
        ``.venv-build/caddy/caddy[.exe]`` — the path populated by
        ``tools/fetch_caddy.py`` for developer builds.

    The returned path is **not** verified to exist. Callers that need
    a live binary (e.g. the launcher supervisor) must assert
    ``path.is_file()`` and surface a clear error if the build was not
    set up correctly.
    """
    binary_name = "caddy.exe" if platform.system() == "Windows" else "caddy"
    if is_frozen():
        # One-dir bundle: launcher.spec declares caddy via binaries=[(src,
        # '.')]. PyInstaller 6.x places binaries/datas under the
        # contents_directory (_internal/), NOT next to the entry-point
        # exe. sys._MEIPASS resolves to that contents dir at runtime.
        meipass = Path(getattr(sys, '_MEIPASS', ''))
        return meipass / binary_name
    # Source / dev tree: tools/fetch_caddy.py installs into .venv-build/caddy/
    return get_app_dir() / ".venv-build" / "caddy" / binary_name


def get_shared_data_dir() -> Path:
    """Return the shared per-user Sethlans data directory (no component).

    This is the parent of ``get_data_dir("manager")`` and
    ``get_data_dir("worker")`` and is the canonical location for
    cross-component state: the setup sentinel, ``topology.json``,
    the IPC marker files (``.restart_requested`` / ``.quit_requested``),
    and shared logs.

    Distinct from ``get_data_dir(component).parent`` in that it honours
    a separate ``SETHLANS_DATA_DIR`` env override — so tests and
    operators can relocate the shared tree without also overriding each
    component's dir.  When only per-component overrides are set, we
    fall back to their common parent.
    """
    env_override = os.environ.get("SETHLANS_DATA_DIR")
    if env_override:
        p = Path(env_override)
        if not p.is_absolute():
            raise ValueError(
                f"SETHLANS_DATA_DIR must be an absolute path, got: "
                f"{env_override}"
            )
        return p
    # Derive from the manager component dir; on stock platforms this
    # resolves to %LOCALAPPDATA%\Sethlans, ~/Library/.../Sethlans, etc.
    return get_data_dir("manager").parent
