// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// FR-CHK3-LABELS — checkpoint name → human-readable step label.
//
// The label MUST match the destination page's <h1> exactly so the
// resume banner ("we've taken you back to the X step") tells the
// user the same name they see on the page.
//
// The spec body (FR-CHK3-LABELS) declares this constant lives in
// common.js; this module re-exports it for ergonomic per-page imports
// without having to also pull in wizardFetch / sessionStorage helpers.

export { RESUME_STEP_LABELS } from '/static/js/common.js';
