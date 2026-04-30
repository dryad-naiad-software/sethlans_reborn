# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Hygiene test for the ``FORM_STATE_STEPS`` constant in
``wizard/frontend/static/js/form_state.js`` (FE-6 regression).

The constant drives ``clearAllFormState()``'s ``sessionStorage``
sweep. Listing a step whose page never calls ``stashFormState`` is
misleading — readers assume a stash exists. The reviewer flagged
``'welcome'`` and ``'done'`` specifically; this test pins those two
out of the list so future hands don't quietly re-add them, and also
sanity-checks the constant against the per-page JS files.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo-relative path to the wizard frontend static JS dir.
JS_DIR = (
    Path(__file__).resolve().parents[3]
    / "wizard"
    / "frontend"
    / "static"
    / "js"
)


def _read_form_state_steps() -> list[str]:
    """Parse ``FORM_STATE_STEPS`` entries out of form_state.js as text."""
    src = (JS_DIR / "form_state.js").read_text(encoding="utf-8")
    match = re.search(
        r"FORM_STATE_STEPS\s*=\s*Object\.freeze\(\s*\[(.*?)\]\s*\)",
        src,
        re.DOTALL,
    )
    assert match, "FORM_STATE_STEPS literal block not found in form_state.js"
    body = match.group(1)
    return re.findall(r"'([^']+)'", body)


def test_welcome_and_done_not_in_form_state_steps():
    """FE-6 regression pin — the two pages that don't stash any form
    state (``welcome.html`` / ``done.html``) MUST NOT appear in the
    sweep list. They're purely transitional pages: welcome shows
    branding + Next; done POSTs and redirects.
    """
    steps = _read_form_state_steps()
    assert "welcome" not in steps, (
        "welcome.js does not call stashFormState — listing it in "
        "FORM_STATE_STEPS misleads readers (FE-6)."
    )
    assert "done" not in steps, (
        "done.js does not call stashFormState — listing it in "
        "FORM_STATE_STEPS misleads readers (FE-6)."
    )


def test_form_state_steps_match_real_step_constants():
    """Hygiene — every step in the sweep list MUST correspond to a
    real page. This test parses each candidate per-page JS file and
    confirms that any step still listed in ``FORM_STATE_STEPS`` is one
    that an actual page knows about (either declares a matching ``STEP``
    constant or otherwise references the step name).

    Pages that import ``clearAllFormState`` to clear at a boundary
    (verify) are treated as legitimate participants — their steps stay
    in the sweep list because they may stash earlier in a future
    iteration even if today they only clear.
    """
    steps = _read_form_state_steps()
    assert steps, "FORM_STATE_STEPS must not be empty"
    # Build the universe of step names known to any per-page JS file
    # (either via a STEP constant or a clearAllFormState/stashFormState
    # reference adjacent to the page's own step name).
    known_step_constants = set()
    for js_file in JS_DIR.glob("*.js"):
        if js_file.name in ("form_state.js", "common.js", "step_labels.js"):
            continue
        contents = js_file.read_text(encoding="utf-8")
        for m in re.finditer(
            r"const\s+STEP\s*=\s*['\"]([^'\"]+)['\"]",
            contents,
        ):
            known_step_constants.add(m.group(1))
    # Pages that interact with form_state at all (stash OR clear) are
    # the legitimate sweep targets. The intersection is what
    # FORM_STATE_STEPS should be a subset of.
    for step in steps:
        page_basename = step.replace("-", "_")
        page_js = JS_DIR / f"{page_basename}.js"
        # Either the page declares a matching STEP constant, OR the page
        # exists and is a legitimate transition page. We accept both
        # because some pages stash (admin_user/database/network) and
        # some only clear (verify) but all should correspond to a real
        # per-page JS file.
        assert page_js.is_file() or step in known_step_constants, (
            f"FORM_STATE_STEPS lists '{step}' but no corresponding "
            f"page file at {page_js} and no JS file declares "
            f"STEP = '{step}'."
        )
