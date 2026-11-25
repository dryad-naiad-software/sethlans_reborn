# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_manager --setup`` and ``run_manager --hash-password`` (FR-SM-1, -2, -5, FR-UA-2).

Both set ``DJANGO_SETTINGS_MODULE`` to ``settings_setup`` by assignment
(a frozen bundle's start hook has already set the run-time module), run
``django.setup()``, and refuse to go on unless that module is in use and
the ``workers`` app is neither installed nor imported.

Exit codes: 0 stopped (or hash printed), 1 the password on stdin is not
acceptable, 2 a usage or start failure (``--setup`` with ``--dev`` or
``--manage``, no setup token, no front-end build in a frozen bundle, the
port taken), 10 setup saved.
"""
import os
import sys

from shared.frozen_paths import is_frozen

SETUP_SETTINGS = "sethlans_manager.settings_setup"
KNOWN_FLAGS = frozenset({"--setup", "--hash-password"})
EXCLUSIVE_FLAGS = ("--dev", "--manage")
EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2
MAX_PASSWORD_BYTES = 4096
SETUP_PORT_ENV = "SETHLANS_SETUP_PORT"
DEFAULT_SETUP_PORT = 8080


def _fail(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return EXIT_USAGE


def _argument_problem(argv) -> str:
    if "--setup" in argv and "--hash-password" in argv:
        return "--setup and --hash-password cannot be combined"
    for flag in EXCLUSIVE_FLAGS:
        if flag in argv:
            return f"{flag} cannot be combined with --setup or --hash-password"
    if "--answers" in argv:
        return "--answers is not available yet"
    unknown = [arg for arg in argv if arg not in KNOWN_FLAGS]
    if unknown:
        return f"Unknown arguments: {' '.join(unknown)}"
    return ""


def _setup_django() -> str:
    """Load ``settings_setup``; return a problem, or "" when all is as it must be."""
    import django
    from django.conf import settings

    django.setup()
    if settings.SETTINGS_MODULE != SETUP_SETTINGS:
        return f"Setup mode needs {SETUP_SETTINGS}, but {settings.SETTINGS_MODULE} was loaded"
    if "workers" in settings.INSTALLED_APPS or any(
            name == "workers" or name.startswith("workers.") for name in sys.modules):
        return "Setup mode must not load the workers app"
    return ""


def _read_password(stdin):
    """``(password, "")`` or ``(None, problem)``: one line ending stripped, then the cap."""
    # Room for the password and one line ending; the cap is on the password.
    raw = getattr(stdin, "buffer", stdin).read(MAX_PASSWORD_BYTES + 3)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    for ending in (b"\r\n", b"\n", b"\r"):
        if raw.endswith(ending):
            raw = raw[:-len(ending)]
            break
    if len(raw) > MAX_PASSWORD_BYTES:
        return None, "The password is longer than 4096 bytes."
    try:
        password = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "The password is not UTF-8."
    return (password, "") if password else (None, "No password on stdin.")


def hash_password(stdin, stdout, stderr) -> int:
    """Read one password on stdin, print its ``pbkdf2_sha256$`` hash (FR-UA-2)."""
    from django.contrib.auth.hashers import make_password
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    password, problem = _read_password(stdin)
    if password is None:
        print(problem, file=stderr)
        return EXIT_INVALID
    try:
        validate_password(password)
    except ValidationError as exc:
        for message in exc.messages:
            print(message, file=stderr)
        return EXIT_INVALID
    print(make_password(password), file=stdout)
    return EXIT_OK


def setup_port(ini_path) -> int:
    """The port people reach setup on: the launcher's ``SETHLANS_SETUP_PORT``,
    else the earlier ``[server] port``, else 8080. A port in use there
    is setup's own and not refused by the port check."""
    from shared.settings_file import SettingsFileError, read_settings

    raw = os.environ.get(SETUP_PORT_ENV, "").strip()
    if not raw:
        try:
            raw = read_settings(ini_path).get("server", "port", fallback="").strip()
        except (SettingsFileError, OSError):
            raw = ""
    return int(raw) if raw.isdigit() else DEFAULT_SETUP_PORT


def serve_setup() -> int:
    import logging

    from django.conf import settings

    from shared.settings_file import settings_path

    from sethlans_manager.setup_mode import auth, leftovers, logging_setup, serve, views
    from sethlans_manager.waitress_config import get_waitress_internal_port, get_waitress_public_port

    try:
        logging_setup.configure(logging_setup.build_logging())
    except (OSError, ValueError) as exc:  # dictConfig wraps a handler's OSError in ValueError
        return _fail(f"Cannot open the setup log: {exc}")
    logger = logging.getLogger(__name__)
    if is_frozen() and not (settings.SETUP_FRONTEND_DIR / "index.html").is_file():
        logger.error("The setup page is missing from this install (%s)", settings.SETUP_FRONTEND_DIR)
        return EXIT_USAGE
    leftovers.remove_pending_setup()
    try:
        auth.configure(auth.load_token())
    except auth.SetupTokenError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE
    ini_path = settings_path()
    public_port = get_waitress_public_port(ini_path)
    views.configure(setup_port(ini_path), {public_port, get_waitress_internal_port(ini_path)})
    return serve.run(public_port)


def run(argv) -> int:
    """Dispatch ``--setup`` or ``--hash-password``; return the exit code."""
    problem = _argument_problem(argv)
    if problem:
        return _fail(problem)
    os.environ["DJANGO_SETTINGS_MODULE"] = SETUP_SETTINGS
    problem = _setup_django()
    if problem:
        return _fail(problem)
    if "--hash-password" in argv:
        return hash_password(sys.stdin, sys.stdout, sys.stderr)
    return serve_setup()
