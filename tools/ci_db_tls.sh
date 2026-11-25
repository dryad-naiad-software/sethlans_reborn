#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

# TLS for the database service containers in python-ci.yml (CI only).
#
# GitHub service containers start before any step runs, so they cannot
# be given certificate files or server arguments at start. This script
# runs after they are healthy:
#
#   1. Makes a throwaway CA and a server certificate for 127.0.0.1 and
#      localhost (IP and DNS SANs, key usages, SKI/AKI, so Python's
#      VERIFY_X509_STRICT and libpq verify-full both accept them).
#      The CA key is deleted once the server certificate is signed.
#   2. Copies ca.pem, server.pem and server.key into /etc/sethlans-tls
#      in each container, owned by the server's account, key mode 0600.
#   3. Turns TLS on with those files:
#        mysql     SET GLOBAL ssl_ca/ssl_cert/ssl_key, then
#                  ALTER INSTANCE RELOAD TLS (no restart);
#        mariadb   a conf.d file with ssl_ca/ssl_cert/ssl_key, then
#                  docker restart and wait for healthy again;
#        postgres  ALTER SYSTEM SET ssl_cert_file/ssl_key_file/ssl,
#                  then pg_reload_conf() (no restart).
#   4. Checks the effect with the manager's own drivers: PyMySQL with
#      ssl={'ca': ca.pem} (checks the chain and the host name) must
#      report an Ssl_version, and psycopg with sslmode=verify-full must
#      show ssl = true in pg_stat_ssl. Any failure exits 1.
#
# Usage:
#   tools/ci_db_tls.sh <out-dir> <engine>=<container-id> [...]
#   engine: mysql | mariadb | postgres
#
# Env: SETHLANS_CI_DB_PASSWORD (root / postgres password of the
# containers). Ports: mysql and mariadb on 127.0.0.1:3306, postgres on
# 127.0.0.1:5432 (the ports the workflow maps). Needs docker, openssl
# and a Python with pymysql and psycopg installed.
#
# Exit codes: 0 TLS on and checked, 1 a step failed, 2 usage error.

set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <out-dir> <engine>=<container-id> [...]" >&2
    exit 2
fi
: "${SETHLANS_CI_DB_PASSWORD:?SETHLANS_CI_DB_PASSWORD must be set}"

OUT_DIR="$1"
shift
IN_CONTAINER=/etc/sethlans-tls
HEALTH_WAIT_S=120

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

make_certs() {
    mkdir -p "${OUT_DIR}"
    chmod 700 "${OUT_DIR}"
    cat > "${OUT_DIR}/ca.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Sethlans CI database test CA
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF
    cat > "${OUT_DIR}/server.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = 127.0.0.1
[v3_server]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = IP:127.0.0.1, DNS:localhost
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
EOF
    openssl req -x509 -new -newkey rsa:2048 -nodes -days 7 \
        -keyout "${OUT_DIR}/ca.key" -out "${OUT_DIR}/ca.pem" \
        -config "${OUT_DIR}/ca.cnf" -extensions v3_ca 2>/dev/null
    openssl req -new -newkey rsa:2048 -nodes \
        -keyout "${OUT_DIR}/server.key" -out "${OUT_DIR}/server.csr" \
        -config "${OUT_DIR}/server.cnf" 2>/dev/null
    openssl x509 -req -in "${OUT_DIR}/server.csr" -days 7 \
        -CA "${OUT_DIR}/ca.pem" -CAkey "${OUT_DIR}/ca.key" \
        -set_serial "0x$(openssl rand -hex 8)" \
        -extfile "${OUT_DIR}/server.cnf" -extensions v3_server \
        -out "${OUT_DIR}/server.pem" 2>/dev/null
    rm -f "${OUT_DIR}/ca.key" "${OUT_DIR}/server.csr" "${OUT_DIR}/ca.cnf" "${OUT_DIR}/server.cnf"
    chmod 600 "${OUT_DIR}/server.key"
    chmod 644 "${OUT_DIR}/ca.pem" "${OUT_DIR}/server.pem"
    openssl verify -x509_strict -CAfile "${OUT_DIR}/ca.pem" "${OUT_DIR}/server.pem" >/dev/null \
        || fail "the generated server certificate does not verify against the CA"
    echo "OK: CA and server certificate in ${OUT_DIR}"
}

copy_files() {  # <container> <owner>
    docker exec "$1" mkdir -p "${IN_CONTAINER}"
    local f
    for f in ca.pem server.pem server.key; do
        docker cp "${OUT_DIR}/${f}" "$1:${IN_CONTAINER}/${f}" >/dev/null
    done
    docker exec "$1" chown -R "$2" "${IN_CONTAINER}"
    docker exec "$1" chmod 700 "${IN_CONTAINER}"
    docker exec "$1" chmod 600 "${IN_CONTAINER}/server.key"
    docker exec "$1" chmod 644 "${IN_CONTAINER}/ca.pem" "${IN_CONTAINER}/server.pem"
}

wait_healthy() {  # <container>
    local end=$((SECONDS + HEALTH_WAIT_S)) health=""
    while [ "${SECONDS}" -lt "${end}" ]; do
        health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$1")
        [ "${health}" = "healthy" ] && return 0
        sleep 2
    done
    docker logs --tail 50 "$1" >&2 || true
    fail "container $1 is '${health}' after ${HEALTH_WAIT_S}s, expected 'healthy'"
}

tls_mysql() {  # <container>
    copy_files "$1" mysql:mysql
    docker exec -e MYSQL_PWD="${SETHLANS_CI_DB_PASSWORD}" "$1" mysql -uroot -e "
        SET GLOBAL ssl_ca = '${IN_CONTAINER}/ca.pem';
        SET GLOBAL ssl_cert = '${IN_CONTAINER}/server.pem';
        SET GLOBAL ssl_key = '${IN_CONTAINER}/server.key';
        ALTER INSTANCE RELOAD TLS;" \
        || fail "MySQL did not load the TLS files"
}

tls_mariadb() {  # <container>
    copy_files "$1" mysql:mysql
    local cnf="${OUT_DIR}/zz-sethlans-tls.cnf"
    printf '[mariadbd]\nssl_ca = %s/ca.pem\nssl_cert = %s/server.pem\nssl_key = %s/server.key\n' \
        "${IN_CONTAINER}" "${IN_CONTAINER}" "${IN_CONTAINER}" > "${cnf}"
    docker exec "$1" mkdir -p /etc/mysql/conf.d
    docker cp "${cnf}" "$1:/etc/mysql/conf.d/zz-sethlans-tls.cnf" >/dev/null
    docker exec "$1" chmod 644 /etc/mysql/conf.d/zz-sethlans-tls.cnf
    rm -f "${cnf}"
    docker exec "$1" mariadbd --print-defaults | grep -Eq -- '--ssl[-_]cert=' \
        || fail "MariaDB does not read /etc/mysql/conf.d/zz-sethlans-tls.cnf"
    docker restart "$1" >/dev/null
    wait_healthy "$1"
}

tls_postgres() {  # <container>
    copy_files "$1" postgres:postgres
    docker exec -u postgres "$1" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c "ALTER SYSTEM SET ssl_cert_file = '${IN_CONTAINER}/server.pem'" \
        -c "ALTER SYSTEM SET ssl_key_file = '${IN_CONTAINER}/server.key'" \
        -c "ALTER SYSTEM SET ssl = on" \
        -c "SELECT pg_reload_conf()" >/dev/null \
        || fail "PostgreSQL did not take the TLS settings"
}

check_tls() {  # <engine>
    python - "$1" "${OUT_DIR}/ca.pem" <<'PY'
import os
import sys
import time

engine, ca = sys.argv[1], sys.argv[2]
password = os.environ["SETHLANS_CI_DB_PASSWORD"]


def mysql_tls():
    import pymysql
    conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password=password,
                           ssl={"ca": ca}, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute("SHOW SESSION STATUS LIKE 'Ssl_version'")
        row = cur.fetchone()
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
    conn.close()
    return f"{row[1]} ({version})" if row and row[1] else None


def postgres_tls():
    import psycopg
    with psycopg.connect(host="127.0.0.1", port=5432, user="postgres", password=password,
                         dbname="postgres", sslmode="verify-full", sslrootcert=ca,
                         connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
    return row[1] if row and row[0] else None


probe = postgres_tls if engine == "postgres" else mysql_tls
error = None
for _ in range(15):
    try:
        result = probe()
        if result:
            print(f"OK: {engine} TLS session, CA and host name checked: {result}")
            sys.exit(0)
        error = "connected, but the session is not encrypted"
    except Exception as exc:  # noqa: BLE001 - report the last error below
        error = f"{type(exc).__name__}: {exc}"
    time.sleep(1)
print(f"FAIL: {engine} TLS check: {error}", file=sys.stderr)
sys.exit(1)
PY
}

for pair in "$@"; do
    engine="${pair%%=*}"
    container="${pair#*=}"
    case "${engine}" in
        mysql | mariadb | postgres) ;;
        *) echo "Unknown engine in '${pair}' (mysql, mariadb or postgres)" >&2; exit 2 ;;
    esac
    if [ -z "${container}" ] || [ "${container}" = "${pair}" ]; then
        echo "No container id in '${pair}'" >&2
        exit 2
    fi
done

make_certs
for pair in "$@"; do
    engine="${pair%%=*}"
    container="${pair#*=}"
    "tls_${engine}" "${container}"
    check_tls "${engine}" || fail "${engine}: TLS check failed"
done
echo "OK: TLS ready; CA file ${OUT_DIR}/ca.pem"
