# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Worker Agent.

Freezes the standalone worker into a one-dir bundle.
Usage: pyinstaller packaging/pyinstaller/worker.spec
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
)

# --- Project paths ---
SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent.parent
WORKER_DIR = PROJECT_ROOT / 'worker'
WEB_UI_STATIC = (
    WORKER_DIR / 'sethlans_worker_agent' / 'web_ui' / 'static'
)
ICON_WIN = SPEC_DIR.parent / 'windows' / 'sethlans.ico'

# Import the Windows VERSIONINFO helper (issue #109). The spec file
# lives at packaging/pyinstaller/ so SPEC_DIR on sys.path makes
# ``version_info`` importable by its module name.
if str(SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(SPEC_DIR))
from version_info import make_version_info  # noqa: E402

# --- Hidden imports ---
hiddenimports = []
hiddenimports += collect_submodules('sethlans_worker_agent')
hiddenimports += collect_submodules('shared')

# Waitress WSGI server (Phase 5 of the Waitress migration wired the
# worker's embedded web UI to Waitress). Waitress has several submodules
# PyInstaller's static import walker can miss (``waitress.task``,
# ``waitress.wasyncore``, ``waitress.adjustments``, ``waitress.parser``,
# ``waitress.utilities``). collect_submodules gives belt-and-braces
# coverage; the explicit top-level import below ensures the package
# root is resolvable even if the submodule walk returns empty on some
# platforms.
hiddenimports += collect_submodules('waitress')
hiddenimports += ['waitress']

# Explicit hidden imports for hardware detection and networking
hiddenimports += [
    'psutil',
    'pystray',
    'requests',
    'requests.adapters',
    'urllib3',
    'tqdm',
]

# Platform-specific notification/tray libraries
if sys.platform == 'win32':
    hiddenimports += [
        'pystray._win32',
        'PIL',
    ]
elif sys.platform == 'darwin':
    hiddenimports += [
        'pystray._darwin',
        'PIL',
        'AppKit',
        'Foundation',
    ]
else:
    hiddenimports += [
        'pystray._xorg',
        'pystray._appindicator',
        'PIL',
        'gi',
    ]

# --- Data files ---
datas = []
if WEB_UI_STATIC.exists():
    datas.append((
        str(WEB_UI_STATIC),
        'sethlans_worker_agent/web_ui/static',
    ))

# --- Caddy binary ---
# Phase 5 of the worker Waitress migration introduced a Caddy front
# proxy for TLS termination. The worker supervises Caddy as a child
# process (sethlans_worker_agent.caddy_supervisor), so the frozen
# worker bundle must ship the Caddy binary alongside run_worker.
#
# Source path: .venv-build/caddy/caddy[.exe] — populated by
# tools/fetch_caddy.py via the CI workflows and dev-setup script.
# Destination: '.' → root of the one-dir bundle, next to run_worker.
_CADDY_NAME = 'caddy.exe' if sys.platform == 'win32' else 'caddy'
_CADDY_SRC = PROJECT_ROOT / '.venv-build' / 'caddy' / _CADDY_NAME
if not _CADDY_SRC.is_file():
    raise SystemExit(
        f"Caddy binary not found at {_CADDY_SRC}. Run "
        "`python tools/fetch_caddy.py --target-dir .venv-build/caddy` "
        "or `python tools/dev_setup.py` before building the worker."
    )
caddy_binaries = [(str(_CADDY_SRC), '.')]

# --- Platform-specific console setting ---
is_windows = sys.platform == 'win32'

a = Analysis(
    [str(WORKER_DIR / 'run_worker.py')],
    pathex=[str(WORKER_DIR), str(PROJECT_ROOT)],
    binaries=caddy_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(SPEC_DIR / 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Windows .ico only (file is absent on macOS/Linux builds); the guard
# keeps the spec cross-platform without a file-missing crash.
icon_path = str(ICON_WIN) if ICON_WIN.exists() else None

# Windows VERSIONINFO resource (issue #109). Windows-only: the
# ``make_version_info`` helper imports ``pefile`` (via PyInstaller's
# win32 versioninfo module), which is not installed on macOS/Linux
# PyInstaller deps. The misleading "no platform guard needed" comment
# was true for the EXE ``version=`` kwarg but false for the function
# call itself — calling it on non-Windows raised
# ``ModuleNotFoundError: No module named 'pefile'`` at PyInstaller
# startup. EXE() accepts ``version=None`` cleanly on every platform.
# Issue #138.
_version_resource = (
    make_version_info('run_worker.exe', 'run_worker')
    if is_windows else None
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=not is_windows,
    icon=icon_path,
    version=_version_resource,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='worker',
)
