# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frozen-environment path resolution for both PyInstaller and source modes.

This module is the single source of truth for resolving application paths
across frozen (PyInstaller one-dir) and source (development) environments.
Every bundle collects the whole ``shared`` package; :func:`get_data_dir`
and :func:`get_identity_dir` import ``shared.settings_file`` late (it
imports this module), so this file is not usable copied out on its own.

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
env_path(name)
    Absolute path from an env var, or ``None``.
get_data_dir(component)
    A component's data folder (env > data root > settings file > default).
get_identity_dir(component)
    A component's identity folder (never moves with the data folder).
get_caddy_path()
    Caddy binary path (frozen installer or dev tree).
get_branding_dir()
    Directory holding bundled brand assets (wordmark, tray icons).
"""

import os
import platform
import re
import sys
from pathlib import Path
from typing import Optional

DATA_ROOT_ENV = "SETHLANS_DATA_ROOT"
_COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


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


def env_path(name: str) -> Optional[Path]:
    """Return the absolute path in env var ``name``, or ``None`` when unset/empty.

    A relative value raises ``ValueError`` rather than resolving against
    the current directory.
    """
    raw = os.environ.get(name)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path, got: {raw}")
    return path


def _component_env_dir(component: str) -> Optional[Path]:
    if not _COMPONENT_RE.match(component):
        raise ValueError(f"Invalid component name: {component!r}")
    return env_path(f"SETHLANS_{component.upper()}_DATA_DIR")


def get_data_dir(component: str) -> Path:
    """Return the data folder of ``component`` (``'manager'``, ``'worker'``).

    The data folder holds bulk, movable state (database, media, logs,
    tools, assets, renders). First match wins:

    1. ``SETHLANS_{COMPONENT}_DATA_DIR`` (e.g. Docker's ``/data``);
    2. ``SETHLANS_DATA_ROOT/<component>``;
    3. ``[paths] data_dir/<component>`` from the settings file
       (:func:`shared.settings_file.settings_path`);
    4. ``get_settings_dir()/<component>`` - the OS per-user folder
       (``%LOCALAPPDATA%\\Sethlans``, ``~/Library/Application
       Support/Sethlans``, ``$XDG_DATA_HOME/sethlans`` or
       ``~/.local/share/sethlans``), today's default layout. From source
       the manager's default is its source folder (``<repo>/manager``),
       where a source-mode manager has always kept its database, media,
       static files and logs.

    Identity files (TLS keys, the secret key, the worker's
    ``config.json``) never move with it: see :func:`get_identity_dir`.
    """
    override = _component_env_dir(component)
    if override is not None:
        return override
    root = env_path(DATA_ROOT_ENV)
    if root is not None:
        return root / component
    from shared import settings_file  # late: settings_file imports this module
    configured = settings_file.configured_data_root()
    if configured is not None:
        return configured / component
    if component == "manager" and not is_frozen():
        return get_manager_dir()
    return settings_file.get_settings_dir() / component


def get_identity_dir(component: str) -> Path:
    """Return the folder holding ``component``'s identity files.

    ``SETHLANS_{COMPONENT}_DATA_DIR`` when set (identity stays exactly
    where it is today, e.g. Docker's ``/data``), else
    ``get_settings_dir()/<component>``. ``SETHLANS_DATA_ROOT`` and
    ``[paths] data_dir`` never move it. With no override this equals
    :func:`get_data_dir`'s default (except for a manager run from
    source, whose data default is ``<repo>/manager``), so a default
    install's layout is unchanged.
    """
    override = _component_env_dir(component)
    if override is not None:
        return override
    from shared import settings_file  # late: settings_file imports this module
    return settings_file.get_settings_dir() / component


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
