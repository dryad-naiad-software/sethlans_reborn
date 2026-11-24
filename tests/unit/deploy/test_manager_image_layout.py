# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source-tree checks for the manager Docker images (issue #296).

The published manager image exited at once with ``No module named
'shared'`` because it copied only ``manager/``, and nothing in it
started Caddy on port 8080. These tests pin the fix in the Dockerfiles,
the dev compose file and the CI workflow:

* the images keep the source-tree layout (``/app/manager``,
  ``/app/shared``, ``/app/launcher``), so ``shared`` is found as the
  parent of ``manager/``;
* the entrypoint is ``launcher/container_entrypoint.py``, which starts
  Caddy and the manager;
* the Caddy binary sits where ``shared.frozen_paths.get_caddy_path()``
  looks in source mode, and every ``{$VAR}`` in the static Caddyfile is
  supplied by the manager's Caddy env overlay;
* the publish workflow runs ``tools/manager_boot_smoke.sh`` before it
  pushes the manager image.

Like the other Dockerfile checks, these read files on disk and are
skipped inside the Docker test image, which does not copy ``deploy/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "deploy" / "docker"
DOCKERFILE = DOCKER_DIR / "manager" / "Dockerfile"
DOCKERFILE_DEV = DOCKER_DIR / "manager" / "Dockerfile.dev"
DEV_ENTRYPOINT = DOCKER_DIR / "manager" / "dev-entrypoint.sh"
CADDYFILE = DOCKER_DIR / "manager" / "Caddyfile"
COMPOSE_DEV = DOCKER_DIR / "docker-compose.dev.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"
SMOKE_SCRIPT = REPO_ROOT / "tools" / "manager_boot_smoke.sh"

if not DOCKERFILE.is_file():
    pytest.skip(
        "manager image layout checks need the source tree; deploy/ is "
        "not copied into the Docker test image.",
        allow_module_level=True,
    )

CADDY_IN_IMAGE = "/app/.venv-build/caddy/caddy"
ENTRYPOINT = '["python", "/app/launcher/container_entrypoint.py"]'


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _copies(dockerfile: Path) -> dict:
    """Map COPY destination -> source for one Dockerfile."""
    pairs = re.findall(
        r"^COPY\s+(?:--\S+\s+)*(\S+)\s+(\S+)\s*$", _text(dockerfile), re.M,
    )
    return {dest: src for src, dest in pairs}


@pytest.mark.parametrize("dest, src", [
    ("/app/shared/", "shared/"),
    ("/app/launcher/", "launcher/"),
    ("/app/manager/", "manager/"),
])
def test_image_keeps_source_tree_layout(dest, src):
    assert _copies(DOCKERFILE).get(dest) == src


def test_image_does_not_flatten_manager_into_app():
    assert "/app/" not in _copies(DOCKERFILE)


def test_image_entrypoint_is_container_entrypoint():
    text = _text(DOCKERFILE)
    assert f"ENTRYPOINT {ENTRYPOINT}" in text
    assert (REPO_ROOT / "launcher" / "container_entrypoint.py").is_file()


@pytest.mark.parametrize("dockerfile", [DOCKERFILE, DOCKERFILE_DEV])
def test_caddy_binary_where_source_mode_looks(dockerfile):
    from shared.frozen_paths import get_app_dir, get_caddy_path

    copies = _copies(dockerfile)
    assert copies.get(CADDY_IN_IMAGE) == ".tmp/caddy/linux-${TARGETARCH}/caddy"
    expected = get_caddy_path().parent.relative_to(get_app_dir()).as_posix()
    assert Path(CADDY_IN_IMAGE).parent.relative_to("/app").as_posix() == expected


@pytest.mark.parametrize("dockerfile", [DOCKERFILE, DOCKERFILE_DEV])
def test_static_caddyfile_env_points_at_copied_file(dockerfile):
    copies = _copies(dockerfile)
    assert copies.get("/etc/caddy/Caddyfile") == "deploy/docker/manager/Caddyfile"
    assert "SETHLANS_MANAGER_CADDYFILE_PATH=/etc/caddy/Caddyfile" in _text(dockerfile)


@pytest.mark.parametrize("dockerfile", [DOCKERFILE, DOCKERFILE_DEV])
def test_all_state_under_data_through_one_setting(dockerfile):
    """#297: one env var moves every source-mode state location to /data."""
    text = _text(dockerfile)
    assert "SETHLANS_MANAGER_DATA_DIR=/data" in text
    # The TLS dir follows the data dir now; no second knob in the image.
    assert "SETHLANS_TLS_DATA_DIR" not in text


def test_healthcheck_probes_the_configured_port():
    """#298: the probe resolves the port like the entrypoint, not a fixed 8080."""
    text = _text(DOCKERFILE)
    assert 'CMD ["python", "/app/launcher/container_healthcheck.py"]' in text
    assert "localhost:8080" not in text
    assert (REPO_ROOT / "launcher" / "container_healthcheck.py").is_file()
    assert '"/app/launcher/container_healthcheck.py"]' in _text(COMPOSE_DEV)


HOST_FIX = (
    "sudo chown -R 1000:1000 ./data ./config && sudo chmod 700 ./data/manager"
)


def test_compose_documents_the_bind_mount_chown():
    text = _text(DOCKER_DIR / "docker-compose.yml")
    assert "- ./data/manager:/data" in text
    assert HOST_FIX in text
    assert "mkdir -p ./data/manager ./config/manager &&" in text
    readme = _text(REPO_ROOT / "README.md")
    assert f"mkdir -p ./data/manager ./config/manager && {HOST_FIX}" in readme
    assert "full access" in readme
    # #301: the container writes its setup marker and documents it.
    assert "On first start it counts as set up" in readme
    assert "code 4 means it could write the folder but not its setup marker" in readme
    assert "python manage.py show_enrollment_key" in readme
    assert "python manage.py createsuperuser" in readme


def test_caddyfile_placeholders_all_come_from_the_overlay():
    from launcher.caddy_launcher import _MANAGER_ENV_OVERLAY

    config = "\n".join(
        line for line in _text(CADDYFILE).splitlines()
        if not line.lstrip().startswith("#")
    )
    placeholders = set(re.findall(r"\{\$([A-Z0-9_]+)\}", config))
    assert placeholders
    assert placeholders <= set(_MANAGER_ENV_OVERLAY.values())


def test_runtime_user_owns_only_volumes_and_static_output():
    """The runtime user may write the volumes and static output, never code.

    Logs moved to /data/logs with the rest of the state (#297), so
    /app/manager/logs is no longer created or handed over.
    """
    text = _text(DOCKERFILE)
    chowns = re.findall(r"chown\s+(?:-\S+\s+)*sethlans:sethlans((?:[^\n]*\\\n)*[^\n]*)", text)
    assert len(chowns) == 1, "expected exactly one chown in the manager Dockerfile"
    owned = set(chowns[0].replace("\\", " ").split())
    assert owned == {"/data", "/config", "/certs", "/app/manager/staticfiles"}
    assert "chown -R" not in text
    assert "--chown" not in text
    assert "USER sethlans" in text


def test_dev_entrypoint_runs_container_entrypoint_in_dev_mode():
    text = _text(DEV_ENTRYPOINT)
    assert "exec python /app/launcher/container_entrypoint.py --dev" in text
    assert "/app/frontend" not in text.replace("/app/manager/frontend", "")


@pytest.mark.parametrize("mount", [
    "../../manager:/app/manager",
    "../../shared:/app/shared",
    "../../launcher:/app/launcher",
    "../../worker/sethlans_worker_agent:/app/sethlans_worker_agent",
])
def test_dev_compose_mounts_source_tree(mount):
    assert f"- {mount}\n" in _text(COMPOSE_DEV)


@pytest.mark.parametrize("hiding_mount", ["../../manager:/app\n", "../../worker:/app\n"])
def test_dev_compose_does_not_hide_image_dirs(hiding_mount):
    assert hiding_mount not in _text(COMPOSE_DEV)


def test_publish_runs_boot_smoke_before_pushing_manager():
    text = _text(PUBLISH_WORKFLOW)
    job = text.split("  build-manager:", 1)[1].split("\n  build-", 1)[0]
    smoke = job.index("tools/manager_boot_smoke.sh")
    assert smoke < job.index("docker/login-action")
    assert smoke < job.index("push: ${{ github.event_name != 'pull_request' }}")
    assert SMOKE_SCRIPT.is_file()
