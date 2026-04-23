# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Bootstrap Launcher.

Freezes the launcher into a one-dir bundle. This is the binary the
user double-clicks from Start Menu / Applications / desktop launcher.
Minimal dependencies — stdlib-only where possible, no Django.
Usage: pyinstaller packaging/pyinstaller/launcher.spec
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# --- Project paths ---
SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent.parent
LAUNCHER_DIR = PROJECT_ROOT / 'launcher'
MANAGER_DIR = PROJECT_ROOT / 'manager'
ICON_WIN = SPEC_DIR.parent / 'windows' / 'sethlans.ico'

# Import the Windows VERSIONINFO helper (issue #109). The spec file
# lives at packaging/pyinstaller/ so SPEC_DIR on sys.path makes
# ``version_info`` importable by its module name.
if str(SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(SPEC_DIR))
from version_info import make_version_info  # noqa: E402

# --- Hidden imports ---
# Launcher is minimal: stdlib + shared.frozen_paths only.
# `workers.multicast_broadcaster` is pure-stdlib (no Django) and is
# imported by the launcher's BroadcasterSupervisor to run UDP discovery
# in the frozen bundle (issue #101). Do NOT use
# collect_submodules('workers') here — it would pull in Django-
# dependent submodules (views, models, serializers) and break the
# build.
hiddenimports = [
    'launcher.logging_setup',
    'workers.multicast_broadcaster',
    # Dynamic import in launcher.caddy_launcher._load_manager_renderer;
    # PyInstaller's static analyzer can't see it (issue #100).
    'sethlans_manager.caddy_template',
    # Startup splash (PySide6). The launcher imports PySide6 lazily
    # from launcher.splash_runner when the splash path is enabled;
    # PySide6 ships a PyInstaller hook that usually resolves these
    # automatically, but declaring them explicitly keeps the bundle
    # deterministic.
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'launcher.splash',
    'launcher.splash_runner',
    'launcher.orchestration_thread',
]
hiddenimports += collect_submodules('shared')

# --- Caddy binary ---
# The launcher supervises Caddy as a child process (manager Phase 3+
# and worker Phase 5+). This spec is **shared by both the manager and
# worker native installers** — adding Caddy here means the worker
# installer also carries a ~40MB Caddy binary starting from manager
# spec Phase 1, even though the worker does not invoke it until a
# later worker-spec phase. The cost is accepted per the spec; the
# binary is present-but-unused in the worker process tree until worker
# supervision wires it up.
#
# Source path: .venv-build/caddy/caddy[.exe] — populated by
# tools/fetch_caddy.py via the CI workflows and dev-setup script.
# Destination: '.' → root of the one-dir bundle, next to run_launcher.
_CADDY_NAME = 'caddy.exe' if sys.platform == 'win32' else 'caddy'
_CADDY_SRC = PROJECT_ROOT / '.venv-build' / 'caddy' / _CADDY_NAME
if not _CADDY_SRC.is_file():
    raise SystemExit(
        f"Caddy binary not found at {_CADDY_SRC}. Run "
        "`python tools/fetch_caddy.py --target-dir .venv-build/caddy` "
        "or `python tools/dev_setup.py` before building the launcher."
    )
caddy_binaries = [(str(_CADDY_SRC), '.')]

# --- Branding assets ---
# The startup splash loads ``logo-text-dark.png`` via
# ``shared.frozen_paths.get_branding_dir()`` which resolves to
# ``sys._MEIPASS / 'branding'`` in frozen mode. PyInstaller copies the
# file to that subdirectory of the bundle's contents dir.
_BRANDING_SRC = PROJECT_ROOT / 'packaging' / 'branding' / 'logo-text-dark.png'
if not _BRANDING_SRC.is_file():
    raise SystemExit(
        f"Branding asset not found at {_BRANDING_SRC}. "
        "Expected packaging/branding/logo-text-dark.png to be "
        "present in the repo before running the launcher build."
    )
branding_datas = [(str(_BRANDING_SRC), 'branding')]

# --- VERSION file ---
# The repo-root ``VERSION`` file is the single source of truth for the
# Sethlans version string. ``shared.version.get_version()`` reads it at
# ``sys._MEIPASS / 'VERSION'`` in frozen mode, so PyInstaller must copy
# it to the bundle's contents dir (dest '.' resolves to _MEIPASS).
_VERSION_SRC = PROJECT_ROOT / 'VERSION'
if not _VERSION_SRC.is_file():
    raise SystemExit(
        f"VERSION file not found at {_VERSION_SRC}. "
        "Expected the repo-root VERSION file to be present before "
        "running the launcher build."
    )
version_datas = [(str(_VERSION_SRC), '.')]

a = Analysis(
    [str(LAUNCHER_DIR / 'run_launcher.py')],
    pathex=[str(LAUNCHER_DIR), str(PROJECT_ROOT), str(MANAGER_DIR)],
    binaries=caddy_binaries,
    datas=branding_datas + version_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'django',
        'rest_framework',
        'PIL',
        'psutil',
        'cryptography',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Console: False on Windows/macOS, True on Linux (headless stdout)
is_linux = sys.platform == 'linux'
icon_path = str(ICON_WIN) if ICON_WIN.exists() else None

# Windows VERSIONINFO resource (issue #109). PyInstaller silently
# ignores ``version=`` on macOS/Linux builds, so no platform guard is
# needed here.
_version_resource = make_version_info('run_launcher.exe', 'run_launcher')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=is_linux,
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
    name='launcher',
)

# On macOS, produce an .app bundle with a visible dock icon
if sys.platform == 'darwin':
    icns_path = SPEC_DIR.parent / 'macos' / 'sethlans.icns'
    icon_arg = str(icns_path) if icns_path.exists() else None
    app = BUNDLE(
        coll,
        name='Sethlans.app',
        icon=icon_arg,
        bundle_identifier='com.dryadandnaiad.sethlans',
        info_plist={
            'LSUIElement': False,
            'CFBundleShortVersionString': '0.1.0',
            'CFBundleVersion': '0.1.0',
            'LSMinimumSystemVersion': '14.0',
            'NSHighResolutionCapable': True,
        },
    )
