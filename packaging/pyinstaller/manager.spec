# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Manager.

Freezes the Django ASGI manager into a one-dir bundle.
Usage: pyinstaller packaging/pyinstaller/manager.spec
"""

import os
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
ICON_WIN = SPEC_DIR.parent / 'windows' / 'sethlans.ico'

# collect_submodules spawns isolated subprocesses that import each module
# to walk its package tree. Our workers app's views/urls import DRF, which
# refuses to load without DJANGO_SETTINGS_MODULE and the manager source
# on sys.path — without these, the isolated import fails and the affected
# submodules (workers.urls, workers.views, workers.signals, etc.) never
# make it into the frozen bundle, producing a cascade of ModuleNotFound
# errors at runtime.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sethlans_manager.settings')
os.environ['PYTHONPATH'] = os.pathsep.join(
    [str(MANAGER_DIR), str(PROJECT_ROOT), os.environ.get('PYTHONPATH', '')]
).rstrip(os.pathsep)
# is_package()'s top-level check runs in THIS process (not the isolated
# subprocess), so the spec's interpreter also needs manager/ on sys.path
# to see 'workers', 'sethlans_manager', and 'shared'.
for _p in (str(MANAGER_DIR), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
# Django pulls many submodules dynamically (INSTALLED_APPS models/admin,
# template-tag libraries, middleware, views, migrations, etc.). Static
# analysis only follows explicit imports and misses most of these, so
# bundle the entire django package. Bundle-size cost is acceptable for
# correctness.
hiddenimports += collect_submodules('django')

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
# Third-party + Django contrib migrations — collect_data_files handles these
# because the packages live under site-packages and PyInstaller's isolated
# subprocess can import them without pathex tweaks.
for app in [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'rest_framework.authtoken',
]:
    datas += collect_data_files(app, subdir='migrations',
                                include_py_files=True)

# Our in-tree 'workers' app lives at manager/workers/ and is only on
# sys.path via our custom pathex. PyInstaller's collect_data_files runs
# in an isolated subprocess that does NOT inherit pathex, so
# `import workers` there fails and the helper silently skips the app
# with "not a package". Bypass it by globbing migration files directly.
_workers_migrations = MANAGER_DIR / 'workers' / 'migrations'
for _py in _workers_migrations.glob('*.py'):
    datas.append((str(_py), str(Path('workers') / 'migrations')))

# Django and DRF template files
datas += collect_data_files('django.contrib.admin', subdir='templates')
datas += collect_data_files('rest_framework', subdir='templates')

# Django i18n: conf/locale .mo files are binary data, not Python, so
# collect_submodules misses them. Without these, django.setup() crashes with
# "No translation files found for default language en-us" when USE_I18N=True.
datas += collect_data_files('django', subdir='conf/locale')
for app in ['django.contrib.admin', 'django.contrib.auth',
            'django.contrib.contenttypes', 'django.contrib.sessions']:
    datas += collect_data_files(app, subdir='locale')
datas += collect_data_files('rest_framework', subdir='locale')

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

# Windows .ico only (file is absent on macOS/Linux builds); the guard
# keeps the spec cross-platform without a file-missing crash.
icon_path = str(ICON_WIN) if ICON_WIN.exists() else None

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
    icon=icon_path,
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
