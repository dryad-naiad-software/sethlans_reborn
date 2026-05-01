# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures for frontend-backend contract integration tests.

Two test families share this directory:

* The legacy Angular contract tests verify that the live Django API
  responds with shapes the Angular frontend expects.
* The Phase 2 (Spec 2) Playwright tests drive the live wizard
  subprocess in a real browser. They reuse the ``wizard_process``
  fixture from :mod:`tests.integration.wizard.conftest` (re-exported
  below) plus a ``browser_context_args`` override that disables HTTPS
  errors — the wizard binds plain HTTP on loopback (issue #170) so
  the override is defense-in-depth for environments that still front
  it with TLS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Re-export the wizard subprocess fixtures so Playwright tests in this
# directory can request them. Pytest fixtures are picked up by name from
# any conftest in the path; bringing them in via plain import + assigning
# to a module-scoped fixture-decorated alias is the canonical way to
# share fixtures across sibling test packages without moving them up to
# tests/integration/conftest.py (which would broaden the blast radius).
from tests.integration.wizard.conftest import (  # noqa: F401
    wizard_data_dir,
    wizard_secrets,
    wizard_process,
)

# Re-export manager-side fixtures so contract tests in this directory
# can build real Project / Asset / admin-authenticated APIClient
# instances without duplicating the setup code or moving the contract
# tests into ``tests/integration/manager/``.  The wizard-ffmpeg-rewrite
# contract tests (``test_ffmpeg_status_contract``,
# ``test_animation_video_contract``) need ``admin_client``, ``project``,
# ``asset``, ``default_version``, and ``worker_with_token``.
from tests.integration.manager.conftest import (  # noqa: F401
    admin_user,
    admin_client,
    default_version,
    project,
    asset,
    worker_with_token,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = REPO_ROOT / "manager" / "frontend" / "src" / "app"


# ---------------------------------------------------------------------
# Playwright browser context overrides for the Phase 2 wizard tests.
# ---------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Override the default Playwright browser context.

    The wizard's loopback listener is plain HTTP (issue #170), so the
    cert override is defensive — kept in case a future configuration
    fronts it with TLS again. Disabling HTTPS errors here means a
    single context can drive the whole flow even if the URL scheme
    flips between pages.
    """
    return {
        **browser_context_args,
        "ignore_https_errors": True,
    }


# ---------------------------------------------------------------------
# Wizard-ffmpeg-rewrite contract-test fixtures.  Shared across
# ``test_animation_video_contract.py`` and
# ``test_animation_video_immutable_contract.py`` so the payload + parts-
# check seed convention stays in lock-step between create-time and
# update-time rejections.
# ---------------------------------------------------------------------

@pytest.fixture
def scoped_parts_state():
    """Snapshot/restore the FFmpeg parts_check registry between tests.

    Mirrors the pattern used in
    ``tests/integration/manager/test_ffmpeg_status_api.py``.  Tests
    that seed a state into the registry MUST consume this fixture so
    later tests do not inherit the seed.
    """
    from workers.services.parts_check import registry
    prior = registry.get_status("ffmpeg")
    yield
    registry._publish("ffmpeg", prior)


@pytest.fixture
def animation_payload(project, asset):  # noqa: F811
    """Minimal animation create payload — mirrors the backend
    conftest's ``animation_payload`` fixture in
    ``tests/integration/manager/test_animation_video.py``.

    The ``project`` and ``asset`` parameters consume the fixtures
    re-exported from ``tests.integration.manager.conftest`` near the
    top of this file; flake8 flags the parameter names as redefining
    the imports, but pytest fixture wiring intentionally shadows
    names this way.
    """
    return {
        "name": "ContractAnim",
        "project": project.pk,
        "asset_id": asset.pk,
        "output_file_pattern": "frame_####.png",
        "start_frame": 1,
        "end_frame": 3,
        "frame_step": 1,
        "render_settings": {
            "render.image_settings.file_format": "PNG",
        },
    }


@pytest.fixture
def video_settings():
    """Default ``video_settings`` block used by both contract files."""
    return {"preset": "web_h264", "framerate": 24}


@pytest.fixture
def job_create_form_ts() -> str:
    """Read ``job-create-form.component.ts`` once for the parser-audit
    test in ``test_animation_video_contract.py``."""
    from ._animation_video_contract_helpers import JOB_CREATE_FORM_TS
    return JOB_CREATE_FORM_TS.read_text(encoding="utf-8")


@pytest.fixture
def job_create_form_errors_ts() -> str:
    """Read ``job-create-form.errors.ts`` once for the parser-audit
    test in ``test_animation_video_contract.py``.  The error parser
    was extracted from the component file to keep both modules under
    the 250-line cap (CLAUDE.md)."""
    from ._animation_video_contract_helpers import (
        JOB_CREATE_FORM_ERRORS_TS,
    )
    return JOB_CREATE_FORM_ERRORS_TS.read_text(encoding="utf-8")
