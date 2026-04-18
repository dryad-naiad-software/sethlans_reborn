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
from PyInstaller.utils.hooks import collect_submodules

# --- Project paths ---
SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent.parent
WORKER_DIR = PROJECT_ROOT / 'worker'

# --- Hidden imports ---
hiddenimports = [
    'pystray',
    'requests',
    'requests.adapters',
    'urllib3',
    'PIL',
    'PIL.Image',
]
hiddenimports += collect_submodules('shared')

# Platform-specific notification libraries
if sys.platform == 'win32':
    hiddenimports += [
        'pystray._win32',
        'win10toast',
    ]
elif sys.platform == 'darwin':
    hiddenimports += [
        'pystray._darwin',
        'AppKit',
        'Foundation',
        'objc',
    ]
else:
    hiddenimports += [
        'pystray._xorg',
        'pystray._appindicator',
        'gi',
    ]

a = Analysis(
    [str(WORKER_DIR / 'run_tray_helper.py')],
    pathex=[str(WORKER_DIR), str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Tray helper is always a GUI process (console=False on all platforms)
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
