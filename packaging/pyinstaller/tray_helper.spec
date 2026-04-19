# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Tray Helper (PySide6-based).

Freezes the tray helper into a one-dir bundle. On macOS, produces
an .app bundle required for tray icon display.

Post-migration bundle size target: ~80-120 MB (Qt libs + Python + deps).
NFR-1 requires COLLECT mode (multi-file) rather than --onefile so the
bundled Qt/PySide6 shared libraries remain replaceable, satisfying the
LGPLv3 relink clause. A user-facing Qt attribution + LGPLv3 text are
shipped under the bundle's `licenses/` directory (NFR-1, AC-14).

Usage: pyinstaller packaging/pyinstaller/tray_helper.spec
"""

import sys
from pathlib import Path

# --- Project paths ---
SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent.parent
SHARED_DIR = PROJECT_ROOT / 'shared'
LICENSES_DIR = SPEC_DIR.parent / 'licenses'

# --- Hidden imports ---
# PySide6 collection strategy (spec OQ-3): pin the three Qt modules the tray
# actually imports (QtCore, QtGui, QtWidgets) plus the shiboken6 binding runtime.
# PyInstaller's built-in PySide6 hooks (hook-PySide6.QtCore / QtGui / QtWidgets)
# pull the Qt shared libraries and the `platforms/` plugin dir automatically
# when those modules are listed as hidden imports. We avoid
# `collect_submodules('PySide6')` because it drags in QtQuick / QtQml / QtNetwork
# / QtOpenGL / etc. - roughly 120 MB of Qt modules the tray helper never touches.
hiddenimports = [
    # PySide6 surface used by shared/tray/*.py (strictly QtCore, QtGui,
    # QtWidgets). If a later change imports another PySide6 module, add
    # it here or fall back to collect_submodules('PySide6').
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'shiboken6',
    # HTTP stack (poller) + process liveness (launcher_watch).
    'requests',
    'requests.adapters',
    'urllib3',
    'psutil',
    # Tray helper package surface.
    'shared',
    'shared.tray',
    'shared.tray.about',
    'shared.tray.app',
    'shared.tray.clipboard',
    'shared.tray.icons',
    'shared.tray.ipc',
    'shared.tray.launcher_watch',
    'shared.tray.menu_manager',
    'shared.tray.menu_manager_helpers',
    'shared.tray.menu_worker',
    'shared.tray.notifications',
    'shared.tray.poller',
    'shared.tray.topology',
    'shared.frozen_paths',
    'launcher.logging_setup',
]

# --- Data files ---
# Tray icon PNGs + LGPLv3 attribution shipped inside the bundle.
# AC-14: licenses/ must contain LGPL-3.0 text and the Qt NOTICE, placed
# at the bundle root so the About dialog and installer LICENSE page can
# reference them by a stable relative path.
datas = [
    (
        str(SHARED_DIR / 'tray' / 'assets'),
        'shared/tray/assets',
    ),
    (
        str(LICENSES_DIR / 'LICENSE.LGPLv3'),
        'licenses',
    ),
    (
        str(LICENSES_DIR / 'Qt-NOTICE.txt'),
        'licenses',
    ),
]

a = Analysis(
    [str(SHARED_DIR / 'run_tray.py')],
    pathex=[str(PROJECT_ROOT), str(SHARED_DIR), str(PROJECT_ROOT / 'launcher')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Tray helper is always a GUI process (console=False on all platforms).
# EXE name kept as 'run_tray_helper' so existing installer / uninstaller
# references (packaging/windows/sethlans.nsi, packaging/linux/
# uninstall.sh, packaging/macos/build_dmg.sh) keep working. Bundle dir
# name stays 'tray_helper' per tray-helper-unified.md FR-2.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_tray_helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

# COLLECT (multi-file one-dir) mode is MANDATORY per NFR-1: the bundled
# Qt and PySide6 shared libraries must remain user-replaceable to satisfy
# the LGPLv3 relink clause. Do NOT switch to EXE(..., onefile=True).
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='tray_helper',
)

# On macOS, produce an .app bundle (required for tray icon display).
# LSUIElement=True keeps the helper menu-bar-only (no Dock tile) per NFR-2.
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='SethlansHelper.app',
        icon=None,
        bundle_identifier='com.dryadandnaiad.sethlans.helper',
        info_plist={
            'LSUIElement': True,  # No dock icon for tray helper
            'CFBundleShortVersionString': '0.1.0',
            'CFBundleVersion': '0.1.0',
            'NSHighResolutionCapable': True,
        },
    )
