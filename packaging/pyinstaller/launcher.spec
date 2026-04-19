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
ICON_WIN = SPEC_DIR.parent / 'windows' / 'sethlans.ico'

# --- Hidden imports ---
# Launcher is minimal: stdlib + shared.frozen_paths only
hiddenimports = [
    'launcher.logging_setup',
]
hiddenimports += collect_submodules('shared')

a = Analysis(
    [str(LAUNCHER_DIR / 'run_launcher.py')],
    pathex=[str(LAUNCHER_DIR), str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'django',
        'rest_framework',
        'uvicorn',
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
