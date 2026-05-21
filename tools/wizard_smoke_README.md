# wizard_smoke.py

Wizard PyInstaller bundle smoke test (DEVOPS-MED-5, Phase F3).

Single source of truth for the AC-B2 / AC-B4 / NF-4 wizard checks.
Used by both `.github/workflows/build-installers.yml` (CI) and the
local `tools/build_*_installer.sh` scripts so a developer can
reproduce CI smoke failures locally without re-engineering the
checks.

## What it verifies

1. **AC-B2 — bundle introspection.** `pathlib.rglob` (exact-name match,
   mirroring the spec verbatim) confirms `django`, `workers`,
   `psycopg`, `pymysql` are absent from `dist/wizard`.
   DEVOPS-HIGH-3: this replaces the prefix-glob shell loops the CI
   previously used (PowerShell `-like "$name*"`, `find -name
   "${name}*"`), which would false-positive on benign files like
   `psycopg2-binary-X.dist-info` AND would miss sub-packages like
   `_internal/django_extensions/`.

2. **Issue #190 — common-passwords resource presence + SHA-256.**
   `pathlib.rglob` locates `common-passwords.txt` anywhere under
   the bundle and `hashlib.sha256` verifies its integrity against
   the pinned `COMMON_PASSWORDS_SHA256` from
   `wizard.sethlans_wizard.password_validators`. PyInstaller's
   static walker does not copy arbitrary data resources, so a spec
   edit can silently drop the file — that surfaces at runtime as
   `common_passwords_resource_invalid` on the admin-user step and
   blocks first-run setup. Failing the build here catches it before
   the installer ships.

3. **NF-4 / DEVOPS-MED-11 — bundle size.** Asserts `dist/wizard` is
   at most 95 MB. On overage, prints the top-10 largest files for
   diagnosis without re-running the build. The cap matches the
   GitHub-hosted CI runner's heavier toolcache Python weight
   (libpython3.14 + cryptography Rust binding alone account for
   ~44 MB on Linux); see issue #154. Raised 85→95 MB during the
   wizard standalone migration (Spec 2) per the project's policy
   of treating alpha-phase NF ceilings as elastic — see project
   memory `feedback_bundle_ceilings`. Trim is a post-development
   pass.

4. **AC-B4 — spawn-and-poll smoke.** Provisions a fresh tmpdir per run
   (DEVOPS-MED-9), writes `.setup_token` and `.ipc_secret`,
   spawns `run_wizard`, READS THE PORT FILE at
   `<tmpdir>/wizard/loopback_port` (DEVOPS-HIGH-4: previously the
   CI hit `https://localhost:8100/` directly, which only worked
   because `SETHLANS_WIZARD_PORT=8100` pinned the bind — that
   bypassed the actual `bootstrap.write_port_file()` code path so
   future regressions there would silently survive smoke), then
   polls `GET /` over plain HTTP until 200 within 30 s. SIGTERMs
   the wizard and dumps captured stdout/stderr on failure
   (DEVOPS-LOW-6). Issue #170: the standalone wizard is plain HTTP
   now (Caddy fronts it in production via the launcher); the smoke
   spawns the wizard alone so it tests the loopback HTTP listener.

## Wall-clock budget

The script self-enforces a 60-second hard ceiling
(AC-B4 / DEVOPS-v22-MED-2) via SIGALRM (POSIX) or a threading
watchdog (Windows).

## Usage

```
python tools/wizard_smoke.py [--bundle dist/wizard] [--port 8100]
python tools/wizard_smoke.py --skip-spawn   # only AC-B2 + NF-4
```

## Exit codes

- `0` — all checks passed.
- `1` — a check failed; the failure reason is printed to stderr.
- `2` — usage error / prerequisite missing (e.g. bundle dir absent).
