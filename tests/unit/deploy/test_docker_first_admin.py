# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""How each Docker and installer start of the manager gets its first admin.

The manager exits 12 with no admin account (FR-FS-2a). These checks keep
the files that start a manager on an empty database in step with the
names and exit code the first-start code uses:

* FR-DK-3: the compose files, ``.env.example`` and the README give the
  admin only in the ``_FILE`` form (username + a Docker secret);
* FR-DK-4: the boot smoke expects exit 12 and the logged reason;
* FR-FS-2a: the installer smoke steps (run only on tags) pass an admin.

Like the other Dockerfile checks, these read files on disk and are
skipped inside the Docker test image, which does not copy ``deploy/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "deploy" / "docker"
COMPOSE = DOCKER_DIR / "docker-compose.yml"
COMPOSE_DEV = DOCKER_DIR / "docker-compose.dev.yml"
ENV_EXAMPLE = DOCKER_DIR / ".env.example"
README = REPO_ROOT / "README.md"
SMOKE_SCRIPT = REPO_ROOT / "tools" / "manager_boot_smoke.sh"
INSTALLERS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-installers.yml"

if not COMPOSE.is_file():
    pytest.skip(
        "Docker file checks need the source tree; deploy/ is not copied "
        "into the Docker test image.",
        allow_module_level=True,
    )

SECRET = "sethlans_admin_password"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_compose_gives_the_admin_through_a_password_file_secret():
    """FR-DK-3: username from .env, password only from the Docker secret."""
    from sethlans_manager.first_start.admin_from_env import (
        EMAIL_ENV, PASSWORD_FILE_ENV, USERNAME_ENV,
    )

    text = _text(COMPOSE)
    assert f"- {USERNAME_ENV}=${{{USERNAME_ENV}:-}}" in text
    assert f"- {EMAIL_ENV}=${{{EMAIL_ENV}:-}}" in text
    # Set only with a username, so "no SETHLANS_ADMIN_*" stays exit 12
    # (FR-DK-4) instead of exit 11 for a password without a username.
    assert f"- {PASSWORD_FILE_ENV}=${{{USERNAME_ENV}:+/run/secrets/{SECRET}}}" in text
    assert re.search(rf"^    secrets:\n      - {SECRET}$", text, re.M)
    assert re.search(rf"^secrets:\n(?:  #.*\n)*  {SECRET}:\n    file: \./secrets/admin_password$", text, re.M)
    # The dev stack swaps in the file tools/docker-dev.sh / .ps1 make.
    assert f"\nsecrets:\n  {SECRET}:\n    file: ${{SETHLANS_DEV_ADMIN_PASSWORD_FILE:?" in _text(COMPOSE_DEV)


@pytest.mark.parametrize("path", [COMPOSE, COMPOSE_DEV, ENV_EXAMPLE, README], ids=lambda p: p.name)
def test_docker_docs_show_only_the_file_form(path):
    """FR-DK-3 / SR-F8: never the plain password variable."""
    text = _text(path)
    assert not re.search(r"SETHLANS_ADMIN_PASSWORD(?!_FILE)", text)


def test_readme_names_exit_12_and_the_createsuperuser_fallback():
    """FR-FS-2a / FR-DK-4: the README is the pointer the exit-12 message relies on."""
    text = _text(README)
    assert "exits with code 12" in text
    assert "SETHLANS_ADMIN_PASSWORD_FILE" in text
    # The image's entrypoint would pass the command on to run_manager.py,
    # so the fallback clears it.
    assert 'docker compose run --rm --entrypoint "" manager python manage.py createsuperuser' in text


def test_boot_smoke_expects_exit_12_with_the_logged_reason():
    """FR-DK-4: the smoke's expectation follows the manager's code and message."""
    from sethlans_manager.first_start import NO_ADMIN_REASON
    from shared.manager_exit_codes import EXIT_NO_ADMIN

    text = _text(SMOKE_SCRIPT)
    step_e = text.split("# --- E. no admin", 1)[1]
    assert f'[ "${{rc}}" = "{EXIT_NO_ADMIN}" ]' in step_e
    logged = re.search(r'grep -q "First start failed: ([^"]+)"', step_e).group(1)
    assert NO_ADMIN_REASON.startswith(logged)
    # The container under test gets no admin variables.
    run = step_e.split("timeout 120 docker run", 1)[1].split("rc=$?", 1)[0]
    assert "SETHLANS_ADMIN" not in run and "ADMIN_ARGS" not in run


def test_installer_manager_smoke_steps_pass_an_admin():
    """FR-FS-2a: each "Manager starts" step must create an admin or it exits 12."""
    steps = _text(INSTALLERS_WORKFLOW).split("- name: Smoke test — Manager starts")[1:]
    assert len(steps) == 3
    for step in steps:
        body = step.split("\n      - name:", 1)[0]
        assert "SETHLANS_ADMIN_USERNAME" in body
        assert "SETHLANS_ADMIN_PASSWORD_FILE" in body
