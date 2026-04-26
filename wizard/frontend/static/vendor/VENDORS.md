<!-- SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Vendored Frontend Assets

This directory contains third-party libraries vendored into the
Sethlans setup wizard frontend bundle. Per Spec 1 NF-3 the wizard MUST
NOT load any asset from a CDN at runtime — every script and stylesheet
ships from this directory inside the PyInstaller bundle.

Per Spec 1 NF-2, vendored library files under `static/vendor/` are
**exempt** from the project SPDX header requirement. Their upstream
license headers are preserved verbatim — do not modify the files in
any way.

## Manifest

Each row pins the vendored file's exact upstream version, source URL,
SHA-256 hash, and license. CI verifies the on-disk SHA-256 against the
hash recorded here (see `tests/unit/wizard/test_vendored_assets.py`).

| File | Vendor | Version | License | SHA-256 | Source |
|------|--------|---------|---------|---------|--------|
| petite-vue.js | Petite-vue | 0.4.1 | MIT | `f17d90e493295133d491e84c04e3a56a77e82eaafe20fd75f3625aa6a2c62ddd` | https://unpkg.com/petite-vue@0.4.1/dist/petite-vue.es.js |
| bootstrap.min.css | Bootstrap | 5.3.3 | MIT | `3c8f27e6009ccfd710a905e6dcf12d0ee3c6f2ac7da05b0572d3e0d12e736fc8` | https://github.com/twbs/bootstrap/releases/download/v5.3.3/bootstrap-5.3.3-dist.zip (path: `css/bootstrap.min.css`) |
| bootstrap.bundle.min.js | Bootstrap | 5.3.3 | MIT | `0833b2e9c3a26c258476c46266e6877fc75218625162e0460be9a3a098a61c6c` | https://github.com/twbs/bootstrap/releases/download/v5.3.3/bootstrap-5.3.3-dist.zip (path: `js/bootstrap.bundle.min.js`) |

### Per-file usage notes

- **petite-vue.js** — Lightweight progressive-enhancement Vue subset
  (~6 kB gzipped). Loaded as an ES module via
  `<script type="module" src="/static/vendor/petite-vue.js"></script>`.
  Page scripts call `PetiteVue.createApp(...).mount()` after the
  vendored bundle has parsed. The file is the verbatim
  `dist/petite-vue.es.js` from the upstream npm tarball; no header
  comment because upstream ships none in the minified ES module
  bundle.
- **bootstrap.min.css** — Bootstrap 5.3.3 minified CSS bundle. Linked
  via `<link rel="stylesheet" href="/static/vendor/bootstrap.min.css">`.
  Upstream license header preserved at the top of the file.
- **bootstrap.bundle.min.js** — Bootstrap 5.3.3 minified JS bundle
  with Popper.js included (so no separate Popper file is needed).
  Loaded with `<script src="/static/vendor/bootstrap.bundle.min.js"></script>`
  before any page script that uses Bootstrap component APIs (modals,
  tooltips, etc.). Upstream license header preserved at the top.

## Updating vendored files

1. Download the new file from the vendor's official source (the
   release tarball/zip on GitHub Releases or the npm tarball — never a
   third-party CDN mirror).
2. Verify the upstream signature/checksum if the project publishes
   one (Bootstrap publishes SHA-256 on their releases; Petite-vue does
   not, but unpkg serves a verbatim copy of the npm tarball, which is
   itself signed).
3. Compute the new SHA-256 locally:
   - Linux/macOS: `sha256sum <file>`
   - PowerShell: `Get-FileHash <file> -Algorithm SHA256`
   - Python: `python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>`
4. Replace the file in this directory verbatim — do **not** strip
   upstream license headers or reformat.
5. Update the manifest table above with the new version + hash +
   source URL.
6. Run the integrity test:
   `pytest tests/unit/wizard/test_vendored_assets.py -v`
7. Smoke-test the wizard pages in a browser to confirm nothing
   regressed (Petite-vue v0.x and Bootstrap v5.x are both stable; bumps
   between minor versions should be drop-in but verify anyway).

## Why no Subresource Integrity (SRI) attribute on the `<link>` / `<script>` tags?

The wizard never loads these files over the network in production —
they are served from `localhost` over the same TLS connection that
served the HTML page. SRI mitigates man-in-the-middle on third-party
CDN traffic; loopback delivery already has integrity guarantees from
the TLS connection itself. The hash check in this manifest covers
build-time tampering instead.
