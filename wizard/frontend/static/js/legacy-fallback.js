// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Legacy-browser fallback for the wizard pages.
//
// Loaded via `<script nomodule src=".../legacy-fallback.js"></script>`
// on every wizard HTML page. Modern browsers (which support ES
// modules) skip nomodule scripts entirely; legacy browsers (which
// don't) execute this file instead of the per-page module under
// `js/`.
//
// Phase G security carry-forward (Issue #146): this file replaces the
// inline `<script nomodule>` blocks that previously lived in
// index.html / topology.html / redirecting.html, so the CSP
// `script-src` directive can drop `'unsafe-inline'`. Keeping the
// fallback identical across pages also matches what the prior inline
// blocks did — the user only ever sees the unsupported-browser alert,
// regardless of which page they hit first.
//
// Intentionally written in ES5 (var, function expressions, no arrow
// functions, no template literals) so the legacy browsers we are
// trying to warn can actually run it.

document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('app');
    if (!root) {
        return;
    }
    root.innerHTML =
        '<div class="container py-5">' +
        '  <div class="alert alert-danger" role="alert">' +
        '    <h2 class="h5 mb-2">Unsupported browser</h2>' +
        '    <p class="mb-0">' +
        '      The Sethlans setup wizard requires a modern browser ' +
        '      (Chrome 90+, Firefox 90+, Safari 14+, or Edge 90+). ' +
        '      Please open this page in a recent browser.' +
        '    </p>' +
        '  </div>' +
        '</div>';
});
