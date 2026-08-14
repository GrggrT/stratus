#!/bin/sh
set -eu

if [ -n "${STRATUS_SAFETY_PASSWORD_FILE:-}" ]; then
    if [ -n "${STRATUS_RUNTIME_PASSWORD_FILE:-}" ]; then
        echo "runtime and safety database credentials cannot be mounted together" >&2
        exit 1
    fi
    password_file="$STRATUS_SAFETY_PASSWORD_FILE"
    credential_kind="safety"
else
    password_file="${STRATUS_RUNTIME_PASSWORD_FILE:-/run/secrets/stratus_runtime_password}"
    credential_kind="runtime"
fi
if [ ! -r "$password_file" ]; then
    echo "$credential_kind database password secret is missing or unreadable" >&2
    exit 1
fi

database_password="$(cat "$password_file")"
if [ "${#database_password}" -lt 24 ]; then
    echo "$credential_kind database password must contain at least 24 characters" >&2
    exit 1
fi
case "$database_password" in
    *[!A-Za-z0-9._~-]*)
        echo "$credential_kind database password must use URL-safe unreserved characters" >&2
        exit 1
        ;;
esac

unset database_password

if [ "$credential_kind" = "runtime" ]; then
    python -m stratus.runtime_db_boundary
fi
exec "$@"
