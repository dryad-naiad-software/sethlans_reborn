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

# --- Hidden imports ---
hiddenimports = []
hiddenimports += collect_submodules('sethlans_worker_agent')
hiddenimports += collect_submodules('shared')

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

# --- Platform-specific console setting ---
is_windows = sys.platform == 'win32'

a = Analysis(
    [str(WORKER_DIR / 'run_worker.py')],
    pathex=[str(WORKER_DIR), str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(SPEC_DIR / 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

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
