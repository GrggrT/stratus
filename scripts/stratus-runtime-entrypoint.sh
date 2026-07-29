#!/bin/sh
set -eu

password_file="${STRATUS_RUNTIME_PASSWORD_FILE:-/run/secrets/stratus_runtime_password}"
if [ ! -r "$password_file" ]; then
    echo "runtime database password secret is missing or unreadable" >&2
    exit 1
fi

runtime_password="$(cat "$password_file")"
if [ "${#runtime_password}" -lt 24 ]; then
    echo "runtime database password must contain at least 24 characters" >&2
    exit 1
fi
case "$runtime_password" in
    *[!A-Za-z0-9._~-]*)
        echo "runtime database password must use URL-safe unreserved characters" >&2
        exit 1
        ;;
esac

unset runtime_password

python -m stratus.runtime_db_boundary
exec "$@"
