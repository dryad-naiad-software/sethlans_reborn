# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Tray Helper.

Freezes the tray helper into a one-dir bundle. On macOS, produces
an .app bundle required for tray icon display.
Usage: pyinstaller packaging/pyinstaller/tray_helper.spec
"""

import sys
from pathlib import Path

# --- Project paths ---
SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent.parent
SHARED_DIR = PROJECT_ROOT / 'shared'

# --- Hidden imports ---
# Explicit list per tray-helper-unified.md FR-25a: NO collect_submodules
# for plyer.  Only the per-platform backends we actually import are
# pulled in.
hiddenimports = [
    'pystray',
    'requests',
    'requests.adapters',
    'urllib3',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'psutil',
    'plyer',
    'plyer.notification',
    'shared',
    'shared.tray',
    'shared.tray.app',
    'shared.tray.clipboard',
    'shared.tray.icons',
    'shared.tray.ipc',
    'shared.tray.launcher_watch',
    'shared.tray.menu_manager',
    'shared.tray.menu_worker',
    'shared.tray.notifications',
    'shared.tray.poller',
    'shared.tray.topology',
    'shared.frozen_paths',
    'launcher.logging_setup',
]

# Platform-specific notification + pystray backends.
if sys.platform == 'win32':
    hiddenimports += [
        'pystray._win32',
        'plyer.platforms.win.notification',
    ]
elif sys.platform == 'darwin':
    hiddenimports += [
        'pystray._darwin',
        'AppKit',
        'Foundation',
        'objc',
        'plyer.platforms.macosx.notification',
    ]
else:
    hiddenimports += [
        'pystray._xorg',
        'pystray._appindicator',
        'gi',
        'plyer.platforms.linux.notification',
    ]

# --- Data files (tray icon PNGs) ---
datas = [
    (
        str(SHARED_DIR / 'tray' / 'assets'),
        'shared/tray/assets',
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
# uninstall.sh) keep working.  Bundle dir name stays 'tray_helper'
# per tray-helper-unified.md FR-2.
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='tray_helper',
)

# On macOS, produce an .app bundle (required for tray icon display)
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
