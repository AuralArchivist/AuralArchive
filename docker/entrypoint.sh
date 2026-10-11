#!/bin/bash
# AuralArchive container entrypoint.
#
# Started as root (the default): aligns the built-in `auralarchive` user with
# PUID/PGID, takes ownership of the app-data folder and the built-in media
# folders, checks that bind-mounted media folders are writable, then drops to
# that user. Bind-mounted media is never chowned - it is shared with other
# apps and its ownership belongs to the host.
#
# Started with `user:` / --user: skips all of the above and just runs.
set -euo pipefail

APP_USER=auralarchive
PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-002}
DATA_DIR=/config

log()  { echo "[entrypoint] $*"; }
warn() { echo "[entrypoint] WARNING: $*" >&2; }

umask "$UMASK"

echo "-----------------------------------"
echo "AuralArchive container"
echo "PUID=${PUID} PGID=${PGID} UMASK=${UMASK}"
echo "-----------------------------------"

if [ "$(id -u)" -ne 0 ]; then
    log "Started as UID $(id -u); skipping user and permission setup"
    exec "$@"
fi

if [ "$PUID" -eq 0 ]; then
    warn "PUID=0 - running as root is not recommended"
    export AURALARCHIVE_ALLOW_ROOT=true
    exec "$@"
fi

# -o allows sharing an ID with an existing host-mapped user/group.
[ "$(id -g "$APP_USER")" = "$PGID" ] || groupmod -o -g "$PGID" "$APP_USER"
[ "$(id -u "$APP_USER")" = "$PUID" ] || usermod -o -u "$PUID" "$APP_USER"

owned_by_app() { [ "$(stat -c %u:%g "$1")" = "${PUID}:${PGID}" ]; }

# Take ownership of an app-owned folder. Recursive only when the top level is
# wrong (first run, or PUID/PGID changed), so normal restarts stay fast.
claim() {
    owned_by_app "$1" && return 0
    log "Setting owner of $1 to ${PUID}:${PGID}"
    chown -R "${PUID}:${PGID}" "$1"
}

is_mounted() { mountpoint -q "$1" 2>/dev/null; }

# App data
mkdir -p "$DATA_DIR"
claim "$DATA_DIR"

# Media folders (fixed paths; host folders are chosen with bind mounts)
for name in library downloads import work; do
    dir="/$name"
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        chown "${PUID}:${PGID}" "$dir"
    fi

    if is_mounted "$dir"; then
        # User-provided storage: check, never chown.
        if ! gosu "$APP_USER" test -w "$dir"; then
            warn "$name folder $dir is not writable by ${PUID}:${PGID} - fix its ownership/permissions on the host"
        fi
    else
        claim "$dir"
        case "$name" in
            library|downloads)
                warn "$name folder $dir is not a bind mount; its contents will be LOST when the container is recreated" ;;
            *)
                log "$name folder $dir is inside the container (not mounted)" ;;
        esac
    fi
done

log "Starting AuralArchive as ${APP_USER} (${PUID}:${PGID})"
echo "-----------------------------------"
exec gosu "$APP_USER" "$@"
