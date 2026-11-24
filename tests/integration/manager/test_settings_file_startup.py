# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The manager reads ``sethlans.ini`` and keeps the identity / data layout.

Each case cold-starts the manager's configuration in a subprocess (the
Django settings, ``run_manager``'s bind and port resolvers, the TLS
setup) with a clean environment, the way a real start sees it:

* the manager reads host, port and database from the settings file named
  by ``SETHLANS_SETTINGS_FILE``;
* a Docker-style env-only start (``SETHLANS_MANAGER_DATA_DIR`` set, no
  settings file) keeps the TLS pair, database, settings file, secret key
  and logs under that one folder;
* an installed (frozen) manager with ``[paths] data_dir`` moves its data
  but never its identity files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"

_DRIVER = """
import json, os, sys
from pathlib import Path
sys.path[:0] = [{repo!r}, {manager!r}]
if {frozen!r}:
    exe = Path({install!r}) / "bin" / "manager" / "run_manager.exe"
    sys.frozen = True
    sys.executable = str(exe)
    sys._MEIPASS = str(exe.parent / "_internal")
os.environ["DJANGO_SETTINGS_MODULE"] = "sethlans_manager.settings"
import django
django.setup()
from django.conf import settings
import run_manager
from sethlans_manager import logging_config, runtime_init, tls_setup, waitress_config
from shared.frozen_paths import get_data_dir, get_identity_dir
from workers.services.parts_check.ffmpeg_download_pkg.locate import get_ffmpeg_dir
ini = tls_setup.get_config_file_path()
cert_path = key_path = ""
if {certs!r}:
    cert_path, key_path, _cert = tls_setup.setup_certificates(
        dev_mode=False, manager_dir=run_manager.MANAGER_DIR, project_root=run_manager.PROJECT_ROOT,
    )
db = settings.DATABASES["default"]
print(json.dumps({{
    "ini": str(ini),
    "bind": list(run_manager.get_manager_bind()),
    "public_port": waitress_config.get_waitress_public_port(ini),
    "internal_port": waitress_config.get_waitress_internal_port(ini),
    "enrollment": runtime_init.get_enrollment_config(ini),
    "db_engine": db["ENGINE"], "db_name": str(db["NAME"]),
    "db_host": db.get("HOST", ""), "db_port": db.get("PORT", ""),
    "db_password": db.get("PASSWORD", ""),
    "cert": str(cert_path), "key": str(key_path),
    "tls_dir": str(tls_setup.get_tls_dir(False, run_manager.MANAGER_DIR, run_manager.PROJECT_ROOT)),
    "ffmpeg_dir": str(get_ffmpeg_dir(get_data_dir("manager"))),
    "secret_key_dir": str(get_identity_dir("manager")),
    "media": str(settings.MEDIA_ROOT), "static": str(settings.STATIC_ROOT),
    "logs": str(logging_config.LOGS_DIR),
}}))
"""

_SCRUB_PREFIXES = ("SETHLANS_", "DJANGO_")


def _start(tmp_path: Path, env_overrides: dict, frozen: bool = False, certs: bool = True) -> dict:
    """Run the driver; *tmp_path*/home stands in for the per-user folders.

    ``certs=False`` only resolves the TLS folder: a source start without
    ``SETHLANS_MANAGER_DATA_DIR`` would otherwise make a cert in the
    checkout's ``manager/tls``.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUB_PREFIXES)}
    home = tmp_path / "home"
    for name in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME", "USERPROFILE"):
        env[name] = str(home)
    env["SETHLANS_SECURITY_DEBUG"] = "False"
    env.update(env_overrides)
    code = _DRIVER.format(
        repo=str(REPO_ROOT), manager=str(MANAGER_DIR),
        frozen=frozen, install=str(tmp_path / "install"), certs=certs,
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_manager_reads_server_and_database_from_the_settings_file(tmp_path):
    settings_file = _write(tmp_path / "conf" / "sethlans.ini", (
        "[server]\n"
        "host = 127.0.0.1\n"
        "port = 9443\n"
        "waitress_loopback_port_public = 18090\n"
        "waitress_loopback_port_internal = 18088\n"
        "[database]\n"
        "engine = postgresql\n"
        "name = farm\n"
        "host = db.example.test\n"
        "port = 6543\n"
        "user = sethlans\n"
        "password = 50%off%(x)s\n"
        "[enrollment]\n"
        "name = Studio A\n"
    ))
    result = _start(tmp_path, {
        "SETHLANS_SETTINGS_FILE": str(settings_file),
        "SETHLANS_MANAGER_DATA_DIR": str(tmp_path / "data"),
    })

    assert Path(result["ini"]) == settings_file
    assert result["bind"] == ["127.0.0.1", "9443"]
    assert (result["public_port"], result["internal_port"]) == (18090, 18088)
    assert result["enrollment"]["name"] == "Studio A"
    assert result["db_engine"] == "django.db.backends.postgresql"
    assert (result["db_name"], result["db_host"], result["db_port"]) == ("farm", "db.example.test", "6543")
    # Read without interpolation: '%' is literal.
    assert result["db_password"] == "50%off%(x)s"


def test_sqlite_name_from_the_settings_file(tmp_path):
    db_file = tmp_path / "elsewhere" / "farm.sqlite3"
    settings_file = _write(tmp_path / "conf" / "sethlans.ini", f"[database]\nengine = sqlite\nname = {db_file}\n")
    result = _start(tmp_path, {
        "SETHLANS_SETTINGS_FILE": str(settings_file),
        "SETHLANS_MANAGER_DATA_DIR": str(tmp_path / "data"),
    })

    assert result["db_engine"] == "django.db.backends.sqlite3"
    assert Path(result["db_name"]) == db_file


def test_env_beats_the_settings_file(tmp_path):
    settings_file = _write(tmp_path / "conf" / "sethlans.ini", "[server]\nhost = 127.0.0.1\nport = 9443\n")
    result = _start(tmp_path, {
        "SETHLANS_SETTINGS_FILE": str(settings_file),
        "SETHLANS_MANAGER_DATA_DIR": str(tmp_path / "data"),
        "SETHLANS_MANAGER_PORT": "9555",
    })

    assert result["bind"] == ["127.0.0.1", "9555"]


def test_docker_style_env_only_start_keeps_everything_in_the_data_folder(tmp_path):
    data = tmp_path / "data"
    result = _start(tmp_path, {"SETHLANS_MANAGER_DATA_DIR": str(data)})

    assert Path(result["ini"]) == data / "sethlans.ini"
    assert not (data / "sethlans.ini").exists()
    assert Path(result["cert"]) == data / "tls" / "cert.pem"
    assert (data / "tls" / "cert.pem").is_file() and (data / "tls" / "key.pem").is_file()
    assert Path(result["db_name"]) == data / "db.sqlite3"
    assert Path(result["secret_key_dir"]) == data
    assert (data / "secret_key").is_file()
    assert Path(result["logs"]) == data / "logs"
    assert result["bind"] == ["0.0.0.0", "8080"]
    # Nothing landed in the stand-in per-user folder.
    assert not any((tmp_path / "home").rglob("*"))


def test_frozen_data_folder_moves_data_not_identity(tmp_path):
    # Windows: the real default (%LOCALAPPDATA%\Sethlans, here the stand-in
    # home). Elsewhere the OS default's folder name differs, so name it.
    overrides = {}
    if sys.platform == "win32":
        settings_dir = tmp_path / "home" / "Sethlans"
    else:
        settings_dir = tmp_path / "settings"
        overrides["SETHLANS_SETTINGS_DIR"] = str(settings_dir)
    data_root = tmp_path / "bulk"
    _write(settings_dir / "sethlans.ini", f"[paths]\ndata_dir = {data_root}\n")

    result = _start(tmp_path, overrides, frozen=True)

    assert Path(result["ini"]) == settings_dir / "sethlans.ini"
    # Data follows [paths] data_dir.
    assert Path(result["db_name"]) == data_root / "manager" / "db.sqlite3"
    assert Path(result["media"]) == data_root / "manager" / "media"
    assert Path(result["static"]) == data_root / "manager" / "staticfiles"
    assert Path(result["logs"]) == data_root / "manager" / "logs"
    # Identity stays in the settings folder.
    assert Path(result["cert"]) == settings_dir / "manager" / "tls" / "cert.pem"
    assert (settings_dir / "manager" / "tls" / "key.pem").is_file()
    assert Path(result["secret_key_dir"]) == settings_dir / "manager"
    assert (settings_dir / "manager" / "secret_key").is_file()
    assert not (data_root / "manager" / "tls").exists()
    assert not (data_root / "manager" / "secret_key").exists()


def test_docker_style_start_reads_an_existing_settings_file_in_the_data_folder(tmp_path):
    """``/data/sethlans.ini`` (here ``<data>/sethlans.ini``) is read when present."""
    data = tmp_path / "data"
    _write(data / "sethlans.ini", "[server]\nhost = 127.0.0.1\nport = 9443\n[enrollment]\nname = Box\n")
    result = _start(tmp_path, {"SETHLANS_MANAGER_DATA_DIR": str(data)})

    assert Path(result["ini"]) == data / "sethlans.ini"
    assert result["bind"] == ["127.0.0.1", "9443"]
    assert result["enrollment"]["name"] == "Box"


def _assert_data_moved_identity_kept(result, data_root):
    data = data_root / "manager"
    assert Path(result["db_name"]) == data / "db.sqlite3"
    assert Path(result["media"]) == data / "media"
    assert Path(result["static"]) == data / "staticfiles"
    assert Path(result["logs"]) == data / "logs"
    assert Path(result["ffmpeg_dir"]).parent.parent == data / "bin"
    # Identity: source-mode TLS stays in manager/, the secret key in the
    # per-user identity folder (the stand-in home here).
    assert Path(result["tls_dir"]) == MANAGER_DIR / "tls"
    assert not Path(result["secret_key_dir"]).is_relative_to(data_root)


def test_source_data_root_moves_all_data_together(tmp_path):
    data_root = tmp_path / "bulk"
    result = _start(tmp_path, {
        "SETHLANS_SETTINGS_FILE": str(tmp_path / "conf" / "sethlans.ini"),
        "SETHLANS_DATA_ROOT": str(data_root),
    }, certs=False)
    _assert_data_moved_identity_kept(result, data_root)


def test_source_paths_data_dir_moves_all_data_together(tmp_path):
    data_root = tmp_path / "bulk"
    settings_file = _write(tmp_path / "conf" / "sethlans.ini", f"[paths]\ndata_dir = {data_root}\n")
    result = _start(tmp_path, {"SETHLANS_SETTINGS_FILE": str(settings_file)}, certs=False)
    _assert_data_moved_identity_kept(result, data_root)


def test_source_default_data_folder_is_the_manager_folder(tmp_path):
    result = _start(tmp_path, {
        "SETHLANS_SETTINGS_FILE": str(tmp_path / "conf" / "sethlans.ini"),
        "SETHLANS_DB_NAME": str(tmp_path / "db.sqlite3"),
        "SETHLANS_MEDIA_ROOT": str(tmp_path / "media"),
    }, certs=False)
    assert Path(result["static"]) == MANAGER_DIR / "staticfiles"
    assert Path(result["logs"]) == MANAGER_DIR / "logs"
    assert Path(result["ffmpeg_dir"]).parent.parent == MANAGER_DIR / "bin"
    assert Path(result["tls_dir"]) == MANAGER_DIR / "tls"
