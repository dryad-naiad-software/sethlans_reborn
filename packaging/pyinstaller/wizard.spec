# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller spec file for the Sethlans Setup Wizard.

Freezes the standalone setup wizard into a one-dir bundle. The wizard
is a minimal HTTPS server (Waitress) that runs alongside the launcher
during first-run setup. It MUST NOT pull in Django, the manager, or
the worker — it is an independent process supervised by the launcher
via the IPC contract in ``wizard/sethlans_wizard/ipc.py``.

NF-4 SIZE CEILING: the wizard one-dir bundle MUST stay at or under
85 MB (raised 25 → 30 → 35 → 85 MB; see Spec 1 NF-4 measurement
note). The 35 → 85 MB jump (issue #154) absorbs the GitHub-hosted CI
runner's heavier toolcache Python — libpython3.14 plus the
cryptography Rust binding alone account for ~44 MB on Linux, which
the prior Docker python:3.14-slim measurement did not see. AC-B2
(forbidden module names: django, workers, psycopg, pymysql) remains
the structural guard against the wizard accidentally absorbing the
manager. Be wary of adding new hidden imports or datas. Per Spec 1
NF-9, no new top-level dependencies (httpx, requests, certifi, etc.)
may be introduced beyond what the manager already pulls in.

Usage: pyinstaller packaging/pyinstaller/wizard.spec
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# --- Project paths ---
SPEC_DIR = Path(SPECPATH)
PROJECT_ROOT = SPEC_DIR.parent.parent
WIZARD_DIR = PROJECT_ROOT / 'wizard'
FRONTEND_DIR = WIZARD_DIR / 'frontend'
ICON_WIN = SPEC_DIR.parent / 'windows' / 'sethlans.ico'

# Import the Windows VERSIONINFO helper (issue #109). The spec file
# lives at packaging/pyinstaller/ so SPEC_DIR on sys.path makes
# ``version_info`` importable by its module name.
if str(SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(SPEC_DIR))
from version_info import make_version_info  # noqa: E402

# --- Hidden imports ---
# Wizard is intentionally minimal:
#   * sethlans_wizard package (collect_submodules picks up handlers/*)
#   * shared.frozen_paths / shared.version only —
#     NOT collect_submodules('shared') because that pulls in the
#     PySide6-dependent shared.tray.* and the launcher-only
#     shared.caddy_supervisor.*, both of which inflate the bundle and
#     would crash with ImportError if anything ever transitively
#     touched them (PySide6 is in ``excludes`` below). DEVOPS-HIGH-1
#     (Phase F3): enumerate the wizard's actual ``shared`` usage
#     explicitly. Verified by ``grep -n "^from shared" wizard/`` ->
#     only frozen_paths + version after issue #170 dropped the
#     wizard's TLS plumbing (the launcher now generates the wizard
#     cert via shared.cert_utils, which keeps cryptography off the
#     wizard bundle entirely).
#   * waitress WSGI server (lazy submodules: task, wasyncore, parser)
hiddenimports = []
hiddenimports += collect_submodules('sethlans_wizard')
hiddenimports += [
    'shared.frozen_paths',
    'shared.version',
]

# Waitress has several submodules PyInstaller's static import walker
# misses (``waitress.task``, ``waitress.wasyncore``,
# ``waitress.adjustments``, ``waitress.parser``, ``waitress.utilities``).
# collect_submodules gives belt-and-braces coverage; the explicit
# top-level import below ensures the package root is resolvable even
# if the submodule walk returns empty on some platforms.
hiddenimports += collect_submodules('waitress')
hiddenimports += ['waitress']

# --- Data files ---
datas = []

# VERSION file: ``shared.version.get_version()`` reads it at
# ``sys._MEIPASS / 'VERSION'`` in frozen mode, so PyInstaller must
# copy it to the bundle's contents dir (dest '.' resolves to _MEIPASS).
_VERSION_SRC = PROJECT_ROOT / 'VERSION'
if not _VERSION_SRC.is_file():
    raise SystemExit(
        f"VERSION file not found at {_VERSION_SRC}. "
        "Expected the repo-root VERSION file to be present before "
        "running the wizard build."
    )
datas.append((str(_VERSION_SRC), '.'))

# Vendored Petite-vue + Bootstrap frontend. Phase B (B1-B4) populates
# wizard/frontend/ with the static HTML/CSS/JS. During A5 the directory
# does not yet exist; the conditional keeps this spec buildable now and
# auto-includes the frontend once Phase B lands without further edits.
if FRONTEND_DIR.exists():
    datas.append((str(FRONTEND_DIR), 'wizard/frontend'))

# --- Excludes ---
# The wizard is a standalone process. Explicitly exclude server-side
# components so a stray transitive import does not bloat the bundle
# past the NF-4 35 MB ceiling or pull in forbidden modules.
# AC-B2 (Phase C) verifies these absences via pathlib.rglob assertions.
#
# DEVOPS-HIGH-1 (Phase F3): explicitly exclude shared.tray and
# shared.caddy_supervisor — the wizard does not use either; without
# the exclude they would still ride along via the prior
# ``collect_submodules('shared')`` (now removed). ``shared.run_tray``
# is the launcher's tray entry-point script, also wizard-irrelevant.
excludes = [
    'django',
    'rest_framework',
    'drf_spectacular',
    'django_filters',
    'workers',
    'sethlans_manager',
    'sethlans_worker_agent',
    'psycopg',
    'psycopg2',
    'pymysql',
    'PIL',
    'PySide6',
    'pystray',
    'shared.tray',
    'shared.caddy_supervisor',
    'shared.run_tray',
    # Issue #170: wizard no longer generates a TLS cert (Caddy fronts
    # it from the launcher), so the cryptography Rust binding is dead
    # weight (~28 MB on Linux). Excluding here also forces a build
    # failure if a future change accidentally reintroduces the
    # dependency.
    'cryptography',
    'shared.cert_utils',
    # NF-9: no new top-level deps beyond manager's set.
    'httpx',
    'requests',
    'certifi',
]

a = Analysis(
    [str(WIZARD_DIR / 'run_wizard.py')],
    pathex=[str(WIZARD_DIR), str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# --- Platform-specific console setting ---
is_windows = sys.platform == 'win32'

# Windows .ico only (file is absent on macOS/Linux builds); the guard
# keeps the spec cross-platform without a file-missing crash.
icon_path = str(ICON_WIN) if ICON_WIN.exists() else None

# Windows VERSIONINFO resource (issue #109). Windows-only: the
# ``make_version_info`` helper imports ``pefile`` (via PyInstaller's
# win32 versioninfo module), which is not installed on macOS/Linux
# PyInstaller deps. EXE() accepts ``version=None`` cleanly on every
# platform. Issue #138.
_version_resource = (
    make_version_info('run_wizard.exe', 'run_wizard')
    if is_windows else None
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_wizard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Console mode on every platform — the wizard is supervised by the
    # launcher, which captures stdout/stderr for diagnostics. A windowed
    # build would suppress those streams.
    console=True,
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
    name='wizard',
)
