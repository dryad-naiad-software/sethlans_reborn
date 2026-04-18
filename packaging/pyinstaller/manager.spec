# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Manager.

Freezes the Django ASGI manager into a one-dir bundle.
Usage: pyinstaller packaging/pyinstaller/manager.spec
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
MANAGER_DIR = PROJECT_ROOT / 'manager'
FRONTEND_DIST = MANAGER_DIR / 'frontend' / 'dist'

# --- Hidden imports: all submodules for Django apps and DRF ---
hiddenimports = []
hiddenimports += collect_submodules('workers')
hiddenimports += collect_submodules('sethlans_manager')
hiddenimports += collect_submodules('rest_framework')
hiddenimports += collect_submodules('drf_spectacular')
hiddenimports += collect_submodules('django_filters')
hiddenimports += collect_submodules('shared')
# whitenoise.runserver_nostatic: no-op when frozen, but Django imports every INSTALLED_APPS entry at startup.
hiddenimports += collect_submodules('whitenoise')

# Uvicorn internals (lazy-loaded)
hiddenimports += [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'httptools',
]
# uvloop is Linux/macOS only
if sys.platform != 'win32':
    hiddenimports.append('uvloop')

# Explicit hidden imports for packages with lazy loading
hiddenimports += [
    'cryptography',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.hashes',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.hazmat.backends',
    'cryptography.x509',
    'psutil',
    'django.db.backends.sqlite3',
    '_sqlite3',
]

# Pillow image codec plugins for image assembly
hiddenimports += [
    'PIL.JpegImagePlugin',
    'PIL.PngImagePlugin',
    'PIL.TiffImagePlugin',
    'PIL.ExifTags',
]

# --- Data files: migration directories (include .py files) ---
datas = []
for app in [
    'workers',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'rest_framework.authtoken',
]:
    datas += collect_data_files(app, subdir='migrations',
                                include_py_files=True)

# Django and DRF template files
datas += collect_data_files('django.contrib.admin', subdir='templates')
datas += collect_data_files('rest_framework', subdir='templates')

# Angular frontend build output
if FRONTEND_DIST.exists():
    datas.append((str(FRONTEND_DIST), 'frontend/dist'))

# --- Runtime hooks ---
runtime_hooks = [
    str(SPEC_DIR / 'hooks' / 'hook-django.py'),
]

# --- Platform-specific console setting ---
is_windows = sys.platform == 'win32'

a = Analysis(
    [str(MANAGER_DIR / 'run_manager.py')],
    pathex=[str(MANAGER_DIR), str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(SPEC_DIR / 'hooks')],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_manager',
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
    name='manager',
)
