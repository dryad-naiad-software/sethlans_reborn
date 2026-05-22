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
    Root application / project directory (caller's own component dir
    in frozen mode — see function docstring for the self-component
    semantic).
get_install_root()
    Cross-component install boundary (``bin/`` in the frozen layout,
    project root in source mode). Use this — not :func:`get_app_dir` —
    for bounds checks that must hold across component process
    boundaries (e.g. the manager-exe resolver in the launcher).
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
get_branding_dir()
    Directory holding bundled brand assets (wordmark, tray icons).
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


def get_install_root() -> Path:
    """Return the cross-component install boundary (issue #192).

    Canonical install-tree anchor. Returns the same path regardless
    of which component's process is the caller (launcher, manager,
    worker, wizard, tray_helper). Use this — NOT :func:`get_app_dir`
    — for bounds checks that must hold across component process
    boundaries (e.g. the launcher resolving the manager binary).

    Frozen mode (Windows / Linux, flat ``bin/<component>/<exe>``
    layout): ``Path(sys.executable).parent.parent`` — each component
    exe lives at ``bin/<component>/run_<component>``, so two levels
    up always lands on ``bin/``.

    Frozen mode (macOS, asymmetric ``.app`` layout per
    ``packaging/macos/build_dmg.sh``): two caller positions detected
    structurally via path-component names (no filesystem probes):

        * Launcher: ``sys.executable`` is
          ``<Sethlans.app>/Contents/MacOS/sethlans`` (renamed from
          ``run_launcher`` at DMG staging). Install root is
          ``.parent.parent / "Resources" / "bin"``.
        * Component (manager/worker/wizard/tray_helper): exe is
          ``<Sethlans.app>/Contents/Resources/bin/<component>/run_<component>``.
          Install root is ``.parent.parent`` directly.

    Source mode: ``get_app_dir()`` (project root).
    """
    if not is_frozen():
        return get_app_dir()
    exe = Path(sys.executable).resolve()
    if platform.system() == "Darwin":
        if exe.parent.name == "MacOS":
            return exe.parent.parent / "Resources" / "bin"
        if exe.parent.parent.name == "bin":
            return exe.parent.parent
        raise RuntimeError(
            f"get_install_root(): unrecognized macOS layout for {exe}"
        )
    return exe.parent.parent


def get_manager_dir() -> Path:
    """Return the manager source or bundle directory.

    Frozen mode: ``<install_root>/manager/`` — stable across all
    caller processes via :func:`get_install_root` (issue #192).
    Source mode: ``<project_root>/manager/``.
    """
    if is_frozen():
        return get_install_root() / 'manager'
    return get_app_dir() / 'manager'


def get_worker_dir() -> Path:
    """Return the worker source or bundle directory.

    Frozen mode: ``<install_root>/worker/`` — stable across all
    caller processes via :func:`get_install_root` (issue #192).
    Source mode: ``<project_root>/worker/``.
    """
    if is_frozen():
        return get_install_root() / 'worker'
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
        Lives under the bundle's ``_internal/`` contents directory —
        e.g. ``dist/launcher/_internal/caddy`` on Unix or
        ``dist/launcher/_internal/caddy.exe`` on Windows. PyInstaller
        6.x places ``binaries=[...]`` entries (declared in
        ``packaging/pyinstaller/launcher.spec``) under the
        contents_directory rather than next to the entry-point exe.
        ``sys._MEIPASS`` resolves to that ``_internal/`` dir at runtime.
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


def get_branding_dir() -> Path:
    """Return the directory holding bundled brand assets.

    Frozen mode (PyInstaller one-dir):
        ``sys._MEIPASS / 'branding'`` — populated by the ``datas=``
        entry in ``packaging/pyinstaller/launcher.spec`` that copies
        ``packaging/branding/logo-text-dark.png`` into the bundle.
    Source mode:
        ``<project_root>/packaging/branding/``.

    The startup splash uses this to resolve the wordmark PNG at
    runtime:

        >>> from shared.frozen_paths import get_branding_dir
        >>> logo = get_branding_dir() / "logo-text-dark.png"

    The returned path is **not** verified to exist. Callers that
    need a present asset must check ``path.is_file()`` themselves.
    """
    if is_frozen():
        meipass = Path(getattr(sys, '_MEIPASS', ''))
        return meipass / 'branding'
    return get_app_dir() / 'packaging' / 'branding'


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
