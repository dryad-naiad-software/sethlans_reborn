#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Unified CLI for Sethlans Reborn development.
#
# Post-Waitress-migration topology (matches production minus the tray
# helper and launcher): Caddy terminates TLS on :8080 and reverse-
# proxies to two loopback Waitress listeners (public 8090, internal
# 8088). The worker connects to https://127.0.0.1:8080 just like a
# real deployment.
#
# Commands:
#   dev      Full dev environment: config + deps + admin + frontend +
#            Caddyfile + start (manager + caddy + worker)
#   clean    Remove the development artifacts inside the repo (no
#            confirmation); `clean --all` also removes the per-user
#            data folder
#   start    Start caddy + manager + worker (assumes already set up)
#   manager  Start caddy + manager only
#   worker   Start worker only
#   stop     Stop background caddy + manager + worker
#   status   Show running caddy / manager / worker processes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"
MANAGER_DIR="$PROJECT_ROOT/manager"
FRONTEND_DIR="$MANAGER_DIR/frontend"
CONFIG_FILE="$MANAGER_DIR/sethlans.ini"
MANAGE_PY="$MANAGER_DIR/manage.py"
WORKER_DIR="$PROJECT_ROOT/worker"
AGENT_DIR="$WORKER_DIR/sethlans_worker_agent"
# Keep all worker runtime state (logs, tools, assets, sentinel, TLS,
# enrollment config) inside the repo in dev mode so `clean` is a
# trivial rm -rf and logs are next to the code. Honoured by the
# worker's config_store.get_data_dir() via SETHLANS_WORKER_DATA_DIR.
WORKER_DATA_DIR="$WORKER_DIR/.dev-data"
PID_DIR="$PROJECT_ROOT/.pids"
CADDY_DIR="$PROJECT_ROOT/.venv-build/caddy"
CADDY_BIN="$CADDY_DIR/caddy"
CADDYFILE="$MANAGER_DIR/caddy/Caddyfile"
DEV_BOOTSTRAP="$SCRIPT_DIR/_dev_bootstrap.py"

# Port layout — mirrors production defaults in sethlans.ini.example.
PUBLIC_TLS_PORT=8080
CADDY_LOOPBACK_PORT=8089
WAITRESS_PUBLIC_PORT=8090
WAITRESS_INTERNAL_PORT=8088

usage() {
    echo "Usage: bash tools/sethlans.sh <command>"
    echo ""
    echo "  dev      Setup everything from scratch and start services"
    echo "  clean    Remove the development artifacts inside the repo"
    echo "           --all  also remove the per-user data folder (identity"
    echo "                  files of an installed manager/worker)"
    echo "  start    Start caddy + manager + worker (must run dev first)"
    echo "  manager  Start caddy + manager in the background"
    echo "  worker   Start worker in the background"
    echo "  stop     Stop background caddy + manager + worker"
    echo "  status   Show running caddy/manager/worker processes"
}

ensure_dirs() { mkdir -p "$PID_DIR"; }

get_saved_pid() {
    local pid_file="$PID_DIR/$1.pid"
    if [ -f "$pid_file" ]; then
        local pid; pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then echo "$pid"; return; fi
        rm -f "$pid_file"
    fi
    echo ""
}

save_pid() { ensure_dirs; echo "$2" > "$PID_DIR/$1.pid"; }
remove_pid() { rm -f "$PID_DIR/$1.pid"; }

read_enrollment_key() {
    # Canonical key from the ManagerSettings DB row.
    python "$DEV_BOOTSTRAP" enrollment-key --manager-dir "$MANAGER_DIR" \
        2>/dev/null
}

ensure_caddy_binary() {
    if [ -x "$CADDY_BIN" ]; then return; fi
    echo "--- Fetching Caddy binary into $CADDY_DIR ---"
    python "$SCRIPT_DIR/fetch_caddy.py" --target-dir "$CADDY_DIR"
}

render_caddyfile() {
    python "$DEV_BOOTSTRAP" render-caddyfile \
        --manager-dir "$MANAGER_DIR" \
        --public-tls-port "$PUBLIC_TLS_PORT" \
        --loopback-plaintext-port "$CADDY_LOOPBACK_PORT" \
        --waitress-public-port "$WAITRESS_PUBLIC_PORT" \
        --waitress-internal-port "$WAITRESS_INTERNAL_PORT" \
        > /dev/null
    echo "[OK] Caddyfile rendered: $CADDYFILE"
}

generate_config() {
    # Delegated to the Python helper so MSYS/Git-Bash path translation
    # applies to the argv (string literals inside `python -c` are NOT
    # translated and blew up on /c/... paths under Git Bash).
    python "$DEV_BOOTSTRAP" generate-config \
        --manager-dir "$MANAGER_DIR" \
        --port "$PUBLIC_TLS_PORT"
}

start_services() {
    echo ""; cmd_manager; echo ""; cmd_worker; echo ""; cmd_status
    echo ""
    echo "============================================================"
    echo "  Manager UI:  https://127.0.0.1:$PUBLIC_TLS_PORT (Caddy TLS)"
    echo "  Worker UI:   https://127.0.0.1:8081 (Caddy TLS)"
    echo "  Swagger API: https://127.0.0.1:$PUBLIC_TLS_PORT/api/docs/"
    echo "  Admin login: testuser / test12345"
    echo "============================================================"
}

# -- dev -----------------------------------------------------------------
cmd_dev() {
    echo "============================================================"
    echo "  Sethlans Reborn -- Dev Environment"
    echo "============================================================"
    echo ""
    echo "--- Configuration ---"
    generate_config
    echo ""
    echo "--- Python dependencies ---"
    pip install -r "$MANAGER_DIR/requirements.txt" \
                -r "$WORKER_DIR/requirements.txt" \
                -r "$PROJECT_ROOT/requirements-dev.txt"
    echo ""
    echo "--- Caddy binary ---"
    ensure_caddy_binary
    echo ""
    echo "--- Database migrations ---"
    # Migration 0017 seeds the ManagerSettings row with a fresh
    # enrollment key the first time it runs — no extra step needed.
    python "$MANAGE_PY" migrate
    echo ""
    echo "--- Admin account ---"
    # SetupGateMiddleware's defense-in-depth treats "superuser exists"
    # as equivalent to sentinel-present, so the gate stays open.
    DJANGO_SUPERUSER_PASSWORD=test12345 python "$MANAGE_PY" createsuperuser \
        --username testuser --email "" --noinput 2>/dev/null || true
    echo "[OK] Admin ready (testuser / test12345)"
    echo ""
    echo "--- Caddyfile ---"
    render_caddyfile
    if [ -d "$FRONTEND_DIR" ]; then
        echo ""
        echo "--- Frontend ---"
        export NG_CLI_ANALYTICS=false
        # npm --prefix is unreliable on Windows/MSYS (silently ignored,
        # so it walks back to $PWD/package.json); pushd is portable.
        pushd "$FRONTEND_DIR" > /dev/null
        if [ ! -d "node_modules" ]; then
            npm install --no-progress --no-fund --no-audit
        fi
        npm run build --no-progress
        popd > /dev/null
        echo "[OK] Frontend built"
    fi
    start_services
}

# -- clean ---------------------------------------------------------------
# The per-user data folder (identity files of an installed manager or
# worker on this account). Sets USER_DATA_DIR, or USER_DATA_DIR_ERROR
# with the reason when the environment gives no safe absolute path: an
# empty HOME / LOCALAPPDATA or a relative base is refused, never turned
# into a relative or root-based path. A relative XDG_DATA_HOME is
# refused too (the XDG spec says to ignore relative values).
resolve_user_data_dir() {
    USER_DATA_DIR=""
    USER_DATA_DIR_ERROR=""
    local base name="sethlans"
    case "$(uname -s)" in
        Darwin)
            name="Sethlans"
            if [ -z "${HOME:-}" ]; then USER_DATA_DIR_ERROR="HOME is empty"; return; fi
            base="$HOME/Library/Application Support" ;;
        MINGW*|MSYS*|CYGWIN*)
            name="Sethlans"
            if [ -z "${LOCALAPPDATA:-}" ]; then USER_DATA_DIR_ERROR="LOCALAPPDATA is empty"; return; fi
            # Check the raw Windows value first (same rule as sethlans.ps1):
            # cygpath would turn a drive-less "\rooted" into "/d/rooted".
            local win_abs_re='^([A-Za-z]:[\/]|\\\\[^\/]+[\/][^\/]+)'
            if ! [[ "$LOCALAPPDATA" =~ $win_abs_re ]]; then
                USER_DATA_DIR_ERROR="LOCALAPPDATA is not an absolute path: '$LOCALAPPDATA'"; return
            fi
            if ! command -v cygpath >/dev/null 2>&1; then
                USER_DATA_DIR_ERROR="cygpath not found, cannot convert LOCALAPPDATA ($LOCALAPPDATA)"; return
            fi
            base="$(cygpath -u "$LOCALAPPDATA")" ;;
        *)
            if [ -n "${XDG_DATA_HOME:-}" ]; then
                base="$XDG_DATA_HOME"
            elif [ -n "${HOME:-}" ]; then
                base="$HOME/.local/share"
            else
                USER_DATA_DIR_ERROR="HOME is empty and XDG_DATA_HOME is unset"; return
            fi ;;
    esac
    case "$base" in
        /*) ;;
        *) USER_DATA_DIR_ERROR="base folder is not an absolute path: '$base'"; return ;;
    esac
    local dir="$base/$name"
    if [ "$(basename "$dir")" != "$name" ]; then
        USER_DATA_DIR_ERROR="last folder of '$dir' is not '$name'"; return
    fi
    USER_DATA_DIR="$dir"
}

# True when path $2 starts an argument of command line $1: at the start
# of the line, or right after a space or a quote. So "/x/repo" never
# matches inside "/other/x/repo".
arg_starts_with() {
    case "$1" in
        "$2"*|*" $2"*|*"\"$2"*|*"'$2"*) return 0 ;;
    esac
    return 1
}

# True when command line $2 belongs to this checkout's $1 (python or
# caddy) process. $3 is the project root in the same path form as the
# command line. Fixed-string match on the full script / Caddyfile path
# under the root, anchored at the start of an argument, so another
# checkout (including a sibling folder whose name starts with this
# one's, or a folder that merely ends with this path) never matches.
cmdline_is_ours() {
    local c="$2" r="$3"
    case "$1" in
        python)
            arg_starts_with "$c" "$r/manager/run_manager.py" && return 0
            arg_starts_with "$c" "$r/worker/run_worker.py" && return 0
            if arg_starts_with "$c" "$r/manager/manage.py"; then
                case "$c" in *"$r/manager/manage.py"*runserver*) return 0 ;; esac
            fi ;;
        caddy)
            arg_starts_with "$c" "$r/manager/caddy/Caddyfile" && return 0 ;;
    esac
    return 1
}

is_windows_bash() {
    case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; esac
    return 1
}

# Windows form of cmdline_is_ours: '\' becomes '/' and the compare is
# case-insensitive, against the root in D:/... form. PowerShell starts
# processes with D:\... paths, Git Bash with D:/... paths.
win_cmdline_is_ours() {
    local root rc=1 restore_nocase=true
    command -v cygpath >/dev/null 2>&1 || return 1
    root=$(cygpath -m "$PROJECT_ROOT") || return 1
    root="${root%/}"
    [ -n "$root" ] || return 1
    shopt -q nocasematch && restore_nocase=false
    shopt -s nocasematch
    cmdline_is_ours "$1" "${2//\\//}" "$root" && rc=0
    [ "$restore_nocase" = true ] && shopt -u nocasematch
    return $rc
}

# Process name of PID $1 without folder or .exe: MSYS /proc exename (Git
# Bash), Linux /proc comm, else ps (macOS). Fails when it cannot be read.
proc_name() {
    local n=""
    if [ -r "/proc/$1/exename" ]; then
        n=$(cat "/proc/$1/exename" 2>/dev/null) || return 1
    elif [ -r "/proc/$1/comm" ]; then
        n=$(cat "/proc/$1/comm" 2>/dev/null) || return 1
    else
        n=$(ps -o comm= -p "$1" 2>/dev/null) || return 1
    fi
    n="${n##*/}"; n="${n%.exe}"
    [ -n "$n" ] || return 1
    printf '%s' "$n"
}

# Full command line of PID $1 (/proc on Linux and Git Bash, ps on
# macOS). Fails when it cannot be read or is empty.
proc_cmdline() {
    local c=""
    if [ -r "/proc/$1/cmdline" ]; then
        c=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null) || return 1
    else
        c=$(ps -ww -o args= -p "$1" 2>/dev/null) || return 1
    fi
    [ -n "$c" ] || return 1
    printf '%s' "$c"
}

# True when PID $1 is this checkout's $2 (python or caddy): the process
# name must be python / python3* / Python (macOS framework) or caddy,
# and its command line must pass the checkout match. An editor, pager
# or `tail -f` with the script path open is never ours. A process whose
# name or command line cannot be read is not ours.
pid_is_ours() {
    local name args
    name=$(proc_name "$1") || return 1
    case "$2" in
        python) case "$name" in python|python3*|Python) ;; *) return 1 ;; esac ;;
        caddy) [ "$name" = "caddy" ] || return 1 ;;
        *) return 1 ;;
    esac
    args=$(proc_cmdline "$1") || return 1
    if is_windows_bash; then
        win_cmdline_is_ours "$2" "$args"
    else
        cmdline_is_ours "$2" "$args" "$PROJECT_ROOT"
    fi
}

# PIDs of this checkout's $1 (python or caddy) processes found by pgrep.
# No-op where pgrep is missing (Git Bash).
posix_our_pids() {
    command -v pgrep >/dev/null 2>&1 || return 0
    local pattern pid
    case "$1" in
        python) pattern='run_manager\.py|run_worker\.py|manage\.py' ;;
        caddy) pattern='Caddyfile' ;;
        *) return 0 ;;
    esac
    for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        pid_is_ours "$pid" "$1" && echo "$pid"
    done
    return 0
}

# PowerShell script for windows_process_list: one line per python.exe /
# caddy.exe, "Name<TAB>ProcessId<TAB>CommandLine", with CR, LF and TAB
# inside the command line turned into spaces, written as UTF-8 with LF.
WIN_PROC_PS=$(cat <<'PS'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='caddy.exe'" | ForEach-Object {
    $c = [string]$_.CommandLine -replace "[\r\n\t]", " "
    [Console]::Out.Write($_.Name + "`t" + $_.ProcessId + "`t" + $c + "`n")
}
PS
)

# "Name<TAB>ProcessId<TAB>CommandLine" lines from powershell.exe (Git
# Bash). The script goes in as -EncodedCommand (base64 UTF-16LE) so no
# quoting survives MSYS-to-Windows argument passing; the MSYS variables
# stop Git Bash from rewriting any argument as a path. No output when
# powershell.exe, iconv or base64 is missing.
windows_process_list() {
    command -v powershell.exe >/dev/null 2>&1 || return 0
    command -v iconv >/dev/null 2>&1 || return 0
    command -v base64 >/dev/null 2>&1 || return 0
    local encoded
    encoded=$(printf '%s' "$WIN_PROC_PS" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\r\n') || return 0
    MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1 \
        powershell.exe -NoProfile -NonInteractive -EncodedCommand "$encoded" 2>/dev/null \
        | tr -d '\r' || true
}

# "<kind> <pid>" for this checkout's python.exe / caddy.exe processes on
# Windows (Git Bash). Fixed-string match, see win_cmdline_is_ours.
windows_our_procs() {
    is_windows_bash || return 0
    local name pid cmd kind
    while IFS=$'\t' read -r name pid cmd; do
        case "$name" in
            [Pp][Yy][Tt][Hh][Oo][Nn].[Ee][Xx][Ee]) kind=python ;;
            [Cc][Aa][Dd][Dd][Yy].[Ee][Xx][Ee]) kind=caddy ;;
            *) continue ;;
        esac
        case "$pid" in ''|*[!0-9]*) continue ;; esac
        win_cmdline_is_ours "$kind" "$cmd" && echo "$kind $pid"
    done < <(windows_process_list)
    return 0
}

# POSIX sweep of this checkout's orphaned processes (see pid_is_ours).
sweep_posix_processes() {
    local pid killed=false
    for pid in $(posix_our_pids python); do
        echo "[KILL] Stopping PID $pid"; kill "$pid" 2>/dev/null || true; killed=true
    done
    for pid in $(posix_our_pids caddy); do
        echo "[KILL] Stopping Caddy PID $pid"; kill "$pid" 2>/dev/null || true; killed=true
    done
    [ "$killed" = true ] && sleep 1
    return 0
}

# Windows sweep (Git Bash) for this checkout's python.exe / caddy.exe
# that slipped past the PID files. No-op when powershell.exe or taskkill
# is missing.
sweep_windows_processes() {
    command -v taskkill >/dev/null 2>&1 || return 0
    local kind pid killed=false
    while read -r kind pid; do
        [ -n "$pid" ] || continue
        echo "[KILL] Taskkill $kind PID $pid"
        taskkill //F //PID "$pid" >/dev/null 2>&1 || true
        killed=true
    done < <(windows_our_procs)
    [ "$killed" = true ] && sleep 1
    return 0
}

cmd_clean() {
    local remove_user_dir=false arg
    for arg in "$@"; do
        case "$arg" in
            --all) remove_user_dir=true ;;
            *) echo "[ERROR] Unknown option for clean: $arg"; usage; exit 1 ;;
        esac
    done
    resolve_user_data_dir
    # A link would send the removal somewhere else: refuse it.
    if [ "$remove_user_dir" = true ] && [ -z "$USER_DATA_DIR_ERROR" ] && [ -L "$USER_DATA_DIR" ]; then
        USER_DATA_DIR_ERROR="$USER_DATA_DIR is a symbolic link or junction"
    fi
    # Refuse before touching anything when --all cannot name a safe folder.
    if [ "$remove_user_dir" = true ] && [ -n "$USER_DATA_DIR_ERROR" ]; then
        echo "[ERROR] Refusing to remove the per-user data folder: $USER_DATA_DIR_ERROR"
        echo "        Nothing was removed."
        exit 1
    fi
    echo "============================================================"
    echo "  Sethlans Reborn -- Clean"
    echo "============================================================"
    echo ""
    # Stop tracked services via PID files (reliable on MSYS/Git-Bash;
    # pgrep doesn't enumerate native Windows Python processes).
    cmd_stop
    sleep 1
    # Belt-and-suspenders: sweep this checkout's orphaned manager /
    # worker / Caddy processes not covered by PID files (POSIX via
    # pgrep, Windows via powershell.exe; each is a no-op where its
    # tools are missing).
    sweep_posix_processes
    sweep_windows_processes
    rm -f "$PID_DIR/manager.pid" "$PID_DIR/worker.pid" "$PID_DIR/caddy.pid"
    # Manager artifacts — source-mode state lives inside manager/.
    rm -f "$CONFIG_FILE" "$MANAGER_DIR/db.sqlite3-journal"
    rm -f "$MANAGER_DIR/broadcaster_params.json" \
          "$MANAGER_DIR/topology.json" \
          "$MANAGER_DIR/.setup_complete"
    if [ -e "$MANAGER_DIR/db.sqlite3" ]; then
        if ! rm -f "$MANAGER_DIR/db.sqlite3"; then
            echo "[ERROR] Failed to delete database file: $MANAGER_DIR/db.sqlite3"
            # Same matcher as the sweep (posix_our_pids / windows_our_procs).
            local still
            still=$({ posix_our_pids python; windows_our_procs | awk '$1 == "python" { print $2 }'; } | tr '\n' ' ')
            still="${still% }"
            [ -n "$still" ] && echo "        Still running: PID(s) $still"
            exit 1
        fi
    fi
    [ -e "$MANAGER_DIR/db.sqlite3" ] && echo "[ERROR] DB still exists after delete" && exit 1
    rm -rf "$MANAGER_DIR/staticfiles" "$MANAGER_DIR/logs" "$MANAGER_DIR/tls" \
           "$MANAGER_DIR/caddy"
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/.angular" "$FRONTEND_DIR/node_modules"
    rm -rf "${MANAGER_DIR:?}/media"
    find "$MANAGER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Manager artifacts removed"
    # Worker — wipe the in-repo dev data dir (logs, tools, assets,
    # sentinel, TLS, enrollment config) and any legacy/stale caches.
    rm -rf "$WORKER_DATA_DIR"
    rm -rf "$AGENT_DIR/managed_tools" "$AGENT_DIR/managed_assets" "$AGENT_DIR/worker_output"
    rm -rf "$AGENT_DIR/temp" "$AGENT_DIR/logs"
    find "$WORKER_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "[OK] Worker artifacts removed"
    # The per-user data folder holds the identity files (TLS keys,
    # secret key, worker enrollment) of any installed manager or worker
    # on this account, so it is removed only with --all.
    if [ "$remove_user_dir" = true ]; then
        if [ -e "$USER_DATA_DIR" ] || [ -L "$USER_DATA_DIR" ]; then
            rm -rf "$USER_DATA_DIR"
            if [ -e "$USER_DATA_DIR" ] || [ -L "$USER_DATA_DIR" ]; then
                echo "[ERROR] Could not remove the per-user data folder ($USER_DATA_DIR)"
                exit 1
            fi
            echo "[OK] Shared data dir removed ($USER_DATA_DIR)"
        fi
    elif [ -n "$USER_DATA_DIR" ] && [ -d "$USER_DATA_DIR" ]; then
        echo "[OK] Shared data dir kept ($USER_DATA_DIR); remove it with: bash tools/sethlans.sh clean --all"
    fi
    # Shared artifacts
    rm -rf "$PROJECT_ROOT/temp" "$PROJECT_ROOT/.pytest_cache" "$PROJECT_ROOT/sethlans_e2e_cache"
    rm -f "$PROJECT_ROOT/test_e2e_db.sqlite3"
    rm -rf "$PROJECT_ROOT/test_artifacts" "$PROJECT_ROOT/manual_test_output" "$PROJECT_ROOT/tools/results"
    echo "[OK] Shared artifacts removed"
    echo ""
    echo "[OK] Clean complete."
}

# -- start ---------------------------------------------------------------
cmd_start() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] sethlans.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    start_services
}

# -- caddy (internal, invoked by cmd_manager) ----------------------------
start_caddy() {
    local existing; existing=$(get_saved_pid "caddy")
    if [ -n "$existing" ]; then
        echo "[OK] Caddy already running (PID $existing)"; return
    fi
    if [ ! -x "$CADDY_BIN" ]; then
        echo "[ERROR] Caddy binary not found at $CADDY_BIN"
        echo "        Run: bash tools/sethlans.sh dev (or python tools/fetch_caddy.py --target-dir $CADDY_DIR)"
        exit 1
    fi
    if [ ! -f "$CADDYFILE" ]; then
        echo "[ERROR] Caddyfile not found at $CADDYFILE"
        echo "        Run: bash tools/sethlans.sh dev"
        exit 1
    fi
    ensure_dirs
    nohup "$CADDY_BIN" run --config "$CADDYFILE" --adapter caddyfile \
        > "$PID_DIR/caddy.out.log" 2>&1 &
    local pid=$!; sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Caddy failed to start (see $PID_DIR/caddy.out.log)"; exit 1
    fi
    save_pid "caddy" "$pid"
    echo "[OK] Caddy started in background (PID $pid)"
}

# -- manager -------------------------------------------------------------
cmd_manager() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] sethlans.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    local existing; existing=$(get_saved_pid "manager")
    if [ -n "$existing" ]; then
        echo "[OK] Manager already running (PID $existing)"
    else
        ensure_dirs
        # Empty regular file for stdin. On Git Bash, `< /dev/null` does
        # NOT make sys.stdin.isatty() return False in native Windows
        # Python — MSYS leaves a console handle attached. A real file
        # handle does flip it.
        : > "$PID_DIR/manager.stdin.txt"
        nohup python "$MANAGER_DIR/run_manager.py" \
            < "$PID_DIR/manager.stdin.txt" \
            > "$PID_DIR/manager.out.log" \
            2> "$PID_DIR/manager.err.log" &
        local pid=$!; sleep 2
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "[ERROR] Manager failed to start (see $PID_DIR/manager.err.log)"; exit 1
        fi
        save_pid "manager" "$pid"
        echo "[OK] Manager started in background (PID $pid)"
    fi
    # Caddy comes up after Waitress is bound so the first proxy attempt
    # doesn't hit a closed socket and log spurious errors.
    start_caddy
    echo "     Stop: bash tools/sethlans.sh stop"
}

# -- worker --------------------------------------------------------------
cmd_worker() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[ERROR] sethlans.ini not found. Run: bash tools/sethlans.sh dev"; exit 1
    fi
    local existing; existing=$(get_saved_pid "worker")
    if [ -n "$existing" ]; then
        echo "[OK] Worker already running (PID $existing)"; return
    fi
    local enrollment_key; enrollment_key=$(read_enrollment_key)
    if [ -z "$enrollment_key" ]; then
        echo "[ERROR] Could not read enrollment key from ManagerSettings DB."
        echo "        Run 'bash tools/sethlans.sh dev' first to migrate the DB."
        exit 1
    fi
    ensure_dirs
    # stdin redirected to an empty regular file so sys.stdin.isatty()
    # returns False; `< /dev/null` does NOT flip isatty on Git Bash
    # (MSYS leaves a Windows console handle attached). Without this
    # the worker takes the browser-wizard path instead of unattended
    # enrollment via SETHLANS_WORKER_ENROLLMENT_KEY.
    # SETHLANS_WORKER_DATA_DIR pins all runtime state into the repo
    # (logs, tools, assets, sentinel, TLS) so `clean` is trivial and
    # logs are next to the code for debugging.
    : > "$PID_DIR/worker.stdin.txt"
    SETHLANS_WORKER_ENROLLMENT_KEY="$enrollment_key" \
    SETHLANS_MANAGER_HOST="127.0.0.1" \
    SETHLANS_MANAGER_PORT="$PUBLIC_TLS_PORT" \
    SETHLANS_IDLE_DETECTION_ENABLED="false" \
    SETHLANS_WORKER_UI_ENABLED="true" \
    SETHLANS_WORKER_DATA_DIR="$WORKER_DATA_DIR" \
    nohup python "$WORKER_DIR/run_worker.py" \
        < "$PID_DIR/worker.stdin.txt" \
        > "$PID_DIR/worker.out.log" \
        2> "$PID_DIR/worker.err.log" &
    local pid=$!; sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERROR] Worker failed to start (see $PID_DIR/worker.err.log)"; exit 1
    fi
    save_pid "worker" "$pid"
    echo "[OK] Worker started in background (PID $pid)"
    echo "     Stop: bash tools/sethlans.sh stop"
}

# -- stop ----------------------------------------------------------------
# Stop the process in PID file $1 (worker, caddy, manager) when it is
# this checkout's $2 (python or caddy, see pid_is_ours); $3 is the label.
# A live PID that is not ours, or whose name or command line cannot be
# read, is a stale PID file: warn, drop the file, leave the process.
# Returns 0 only when something was stopped.
stop_tracked() {
    local pid; pid=$(get_saved_pid "$1")
    [ -n "$pid" ] || return 1
    if ! pid_is_ours "$pid" "$2"; then
        echo "[WARN] Stale PID file for $1 (PID $pid is not ours); not stopping it"
        remove_pid "$1"
        return 1
    fi
    kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true
    remove_pid "$1"; echo "[OK] $3 stopped (PID $pid)"
    return 0
}

cmd_stop() {
    local stopped=false
    # Stop worker first so it doesn't log enrollment failures when
    # Caddy/Manager go away mid-heartbeat.
    if stop_tracked worker python "Worker"; then stopped=true; fi
    if stop_tracked caddy caddy "Caddy"; then stopped=true; fi
    if stop_tracked manager python "Manager"; then stopped=true; fi
    if [ "$stopped" = false ]; then echo "[OK] No running services found"; fi
    return 0
}

# -- status --------------------------------------------------------------
cmd_status() {
    local manager_pid; manager_pid=$(get_saved_pid "manager")
    [ -n "$manager_pid" ] && echo "Manager:  running (PID $manager_pid)" || echo "Manager:  not running"
    local caddy_pid; caddy_pid=$(get_saved_pid "caddy")
    [ -n "$caddy_pid" ] && echo "Caddy:    running (PID $caddy_pid)" || echo "Caddy:    not running"
    local worker_pid; worker_pid=$(get_saved_pid "worker")
    [ -n "$worker_pid" ] && echo "Worker:   running (PID $worker_pid)" || echo "Worker:   not running"
}

# -- Main ----------------------------------------------------------------
COMMAND="${1:-}"
[ $# -gt 0 ] && shift
# Only `clean` takes options.
if [ "$COMMAND" != "clean" ] && [ $# -gt 0 ]; then
    echo "[ERROR] '$COMMAND' takes no options"; usage; exit 1
fi
case "$COMMAND" in
    dev)     cmd_dev ;;
    clean)   cmd_clean "$@" ;;
    start)   cmd_start ;;
    manager) cmd_manager ;;
    worker)  cmd_worker ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    *)       usage; exit 1 ;;
esac
