# tools/

Developer-side scripts that wrap the project's components for local
iteration. Production entry points stay in their respective packages
(`launcher/run_launcher.py`, `wizard/run_wizard.py`,
`manager/run_manager.py`, `worker/run_worker.py`); these scripts are
thin wrappers around them.

## Dev-mode harness scripts

Five scripts let a developer run any single component (or the full
launcher stack) against a project-local `temp/dev-data/` directory,
with credentials printed to stdout for copy-paste and `.env` for
overrides.

- `dev_wizard.py` — runs the standalone wizard. Generates a fresh setup
  token + IPC secret, picks a free loopback port (8099/8101..8104),
  and spawns `wizard/run_wizard.py`. Output prefixed `[wizard]`.
- `dev_manager.py` — runs the Django manager. Provisions a clean
  `<dev-data>/manager/` data dir, generates a self-signed cert, and
  forwards `--port` / `--seed-pending` to the manager. Output prefixed
  `[manager]`.
- `dev_launcher.py` — runs the full launcher (wizard + manager + worker
  + tray). Generates a tray IPC secret and exports it as
  `SETHLANS_TRAY_IPC_SECRET`. Output prefixed `[launcher]`.
- `dev_worker.py` — runs the worker agent. Seeds a default UI password
  (`dev`) so the embedded web UI is testable. Optional `--manager-url`
  + `--enrollment-key` for testing enrollment against a `dev_manager`
  instance. Output prefixed `[worker]`.
- `dev_clean.py` — wipes `temp/dev-data/` (or a single component
  subdir). Confirms before deleting; `--yes` skips the prompt;
  `--dry-run` lists targets only.

All five share helpers in `_dev_common.py` (free-port picker, .env
loader, banner printer, subprocess streamer, dev cert generator).

### `.env` overrides

Each script reads `<project_root>/.env` if present. Shell env wins over
`.env`; `.env` is gitignored. See `.env.example` at the repo root for
the complete list of supported variables.

Common ones:

- `SETHLANS_DEV_DATA_ROOT` — relocate dev data root (default
  `temp/dev-data`).
- `SETHLANS_DEV_WIZARD_PORT` / `SETHLANS_DEV_MANAGER_PORT` /
  `SETHLANS_DEV_WORKER_UI_PORT` — pin a specific port.
- `SETHLANS_DEV_TLS_REUSE` — `1` (default) to reuse the cached dev
  cert, `0` to regenerate.
- `SETHLANS_DEV_LOG_LEVEL` — pass-through log level.

Pre-existing `SETHLANS_*` env vars the components honour
(`SETHLANS_WIZARD_PORT`, `SETHLANS_MANAGER_HOST`,
`SETHLANS_TRAY_IPC_SECRET`, etc.) pass through unchanged.

### Data dir layout

```
temp/dev-data/
  wizard/          # consumed by tools/dev_wizard.py
    .setup_token   # chmod-600; consumed-and-unlinked by wizard
    .ipc_secret    # chmod-600; consumed-and-unlinked by wizard
    setup_token    # tray-readable copy (FR-L13)
    tls/cert.pem   # self-signed dev cert
    tls/key.pem
  manager/
    tls/cert.pem
    tls/key.pem
  worker/
    config.json    # written by worker on first start
  launcher/        # reserved (launcher writes log files here)
  pending_setup.json   # seeded by `dev_manager.py --seed-pending`
```

The worker's `config.ini` lives in the source tree
(`worker/config.ini`, gitignored) and is updated by `dev_worker.py`'s
`--ui-password` seeding. `dev_clean.py` does NOT touch it; delete by
hand if you want a fully fresh worker.

### Ctrl+C semantics

Each script blocks on its child subprocess. Ctrl+C forwards `SIGTERM`
to the child and waits up to 5 s before sending `SIGKILL`. Data dirs
are NOT cleaned automatically on exit — use `dev_clean.py` for that.

## Other tools

- `_dev_bootstrap.py` — Django + Caddyfile bootstrap helpers used by
  `sethlans.sh` / `sethlans.ps1`.
- `_dev_common.py` — shared helpers for the five `dev_*.py` scripts.
- `fetch_caddy.py` — populates `.venv-build/caddy/` with the Caddy
  binary used by frozen builds.
- `wizard_smoke.py` — smoke test for the wizard backend.
- `run_*.{sh,ps1}` — test/CI runners.
- `build_*.sh` — installer build scripts.
