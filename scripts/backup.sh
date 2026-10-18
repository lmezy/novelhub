#!/bin/sh
# NovelHub backup loop.
#
# Two very different things are backed up:
#
#   Postgres  -- accounts, library, reading progress, tags, bookmarks.  A few
#                hundred MB and irreplaceable: a nightly ``pg_dump`` takes
#                minutes.
#   storage/  -- the book files (~250 GiB of markdown + images).
#
# The storage side used to be a fresh ``tar -I zstd`` of the whole tree once a
# day.  On the 4-core NAS that ran for ~19 hours, saturated the disk (search
# indexing dropped to a few dozen documents a minute while it ran) and needed
# 7 x 250 GiB of backup space.  It is a *hardlink snapshot* now: the previous
# snapshot is seeded with ``cp -al`` (instant, no extra space), only files whose
# mtime changed since the last run are copied, and deletions are swept
# afterwards.  Every snapshot still looks like a complete tree; a day costs only
# the changed files, and N snapshots cost the storage once plus deltas.
#
# Environment:
#   BACKUP_STORAGE=0            database only, no file snapshots
#   BACKUP_INTERVAL_SECONDS     between cycles (default 86400)
#   BACKUP_*_KEEP               how many daily/weekly copies to retain
#   BACKUP_SOURCE / BACKUP_ROOT  paths (defaults match the compose service)
#   BACKUP_WAIT_SECONDS         how long to wait for Postgres on a cold start
set -u

BACKUP_ROOT="${BACKUP_ROOT:-/backup}"
BACKUP_SOURCE="${BACKUP_SOURCE:-/app/storage}"
BACKUP_STORAGE="${BACKUP_STORAGE:-1}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_DB_DAILY_KEEP="${BACKUP_DB_DAILY_KEEP:-7}"
BACKUP_DB_WEEKLY_KEEP="${BACKUP_DB_WEEKLY_KEEP:-4}"
BACKUP_STORAGE_DAILY_KEEP="${BACKUP_STORAGE_DAILY_KEEP:-7}"
BACKUP_STORAGE_WEEKLY_KEEP="${BACKUP_STORAGE_WEEKLY_KEEP:-4}"
BACKUP_WAIT_SECONDS="${BACKUP_WAIT_SECONDS:-300}"
# Copies in flight while the snapshot is written.  The storage is hundreds of
# thousands of *small* files on spinning disks: a single-threaded ``cp -a`` gave
# the array no queue depth and managed ~1.2 MB/s on the live 250 GiB tree
# (measured 2026-10-18, i.e. 2.4 days for the first snapshot).  Four parallel
# copies are the difference between "overnight" and "next week"; raise it only
# if the pool is idle.
BACKUP_COPY_JOBS="${BACKUP_COPY_JOBS:-4}"

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGDATABASE="${PGDATABASE:-novelhub}"
PGUSER="${PGUSER:-novelhub}"

DB_DIR="$BACKUP_ROOT/daily"
DB_WEEKLY_DIR="$BACKUP_ROOT/weekly"
SNAP_DIR="$BACKUP_ROOT/storage/daily"
SNAP_WEEKLY_DIR="$BACKUP_ROOT/storage/weekly"
# Markers live outside the snapshots: a file inside would look like a file that
# the live tree does not have, and the deletion sweep would remove it.
STATE_DIR="$BACKUP_ROOT/.state"
LAST_CYCLE="$STATE_DIR/last_cycle"
LAST_SNAPSHOT="$STATE_DIR/last_snapshot"

log() {
    echo "[backup $(date '+%Y-%m-%d %H:%M:%S')] $*"
}

wait_for_db() {
    waited=0
    while [ "$waited" -lt "$BACKUP_WAIT_SECONDS" ]; do
        if pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; then
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
    done
    return 1
}

# One dump, written to ``*.part`` first: a crashed/killed dump must never look
# like a usable backup, and an empty file (the connection-refused failure the
# old script wrote silently) is discarded here instead of being kept for days.
dump_database() {
    ts="$(date +%Y%m%d_%H%M)"
    target="$DB_DIR/db_${ts}.dump"
    tmp="${target}.part"
    if pg_dump -Fc -f "$tmp"; then
        if [ -s "$tmp" ]; then
            mv "$tmp" "$target"
            log "database dump ok: $target ($(du -h "$target" | cut -f1))"
        else
            rm -f "$tmp"
            log "database dump came back EMPTY, discarded"
        fi
    else
        rm -f "$tmp"
        log "database dump FAILED (see the pg_dump error above)"
    fi
}

# Copy the files whose mtime changed since the last snapshot and drop the ones
# that disappeared.  ``--remove-destination`` is load-bearing: plain ``cp`` over
# a path that is a hardlink to the previous snapshot would truncate the shared
# inode and silently corrupt that snapshot.
#
# ``xargs -0`` rather than ``find -print0 | while read -d ''``: the container's
# ``sh`` is dash, whose ``read`` has no ``-d``.  The loop silently did nothing
# there (found by running this script in the postgres:17 image), which would
# have produced snapshots that never picked up a single change.
export BACKUP_SOURCE
sync_changed_files() {
    dest="$1"
    (
        cd "$BACKUP_SOURCE" || exit 0
        find . -type f -newer "$LAST_SNAPSHOT" -print0 2>/dev/null |
            xargs -0 -r -n 200 -P "$BACKUP_COPY_JOBS" \
                cp -p --parents --remove-destination -t "$dest" 2>/dev/null
    )

    export DEST="$dest"
    find "$dest" -type f -print0 2>/dev/null |
        xargs -0 -r sh -c '
            for old in "$@"; do
                rel="${old#"$DEST"/}"
                [ -e "$BACKUP_SOURCE/$rel" ] || rm -f "$old"
            done
        ' sh
    unset DEST
    find "$dest" -depth -type d -empty -delete 2>/dev/null || true
}

snapshot_storage() {
    if [ ! -d "$BACKUP_SOURCE" ]; then
        log "storage path $BACKUP_SOURCE is missing, skipping the file snapshot"
        return 0
    fi
    ts="$(date +%Y%m%d_%H%M%S)"
    dest="$SNAP_DIR/$ts"
    previous="$(ls -1d "$SNAP_DIR"/*/ 2>/dev/null | tail -n 1)"

    log "storage snapshot starting: $dest (previous: ${previous:-none})"
    mkdir -p "$dest"
    seeded=0
    if [ -n "$previous" ] && [ -f "$LAST_SNAPSHOT" ]; then
        # Seed with hardlinks: instant, and unchanged files cost no space.
        # Never hardlink the *live* tree: the crawler rewrites chapter files in
        # place, which would change the snapshot through the shared inode.
        if cp -al "$previous." "$dest/" 2>/dev/null && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
            seeded=1
            sync_changed_files "$dest"
        else
            log "hardlink seed from $previous failed, falling back to a full copy"
        fi
    fi
    if [ "$seeded" != "1" ]; then
        log "no usable previous snapshot, copying everything (the slow one, once)"
        (
            cd "$BACKUP_SOURCE" || exit 0
            find . -type f -print0 2>/dev/null |
                xargs -0 -r -n 200 -P "$BACKUP_COPY_JOBS" \
                    cp -p --parents --remove-destination -t "$dest" 2>/dev/null
        )
        # ``find | xargs`` copies files only, so a directory holding no file is
        # not recreated.  The live storage has none (every book directory holds
        # its metadata.json and its chapters), so this is a non-issue there; a
        # restore simply shows no empty directories.
    fi

    touch "$LAST_SNAPSHOT"
    log "storage snapshot done: $dest"
}

# Hardlink copies are free, so a weekly keeps the existing weekly semantics
# without a second pass over 250 GiB.
rotate_weekly() {
    ts="$(date +%Y%m%d)"
    newest_db="$(ls -1t "$DB_DIR"/db_*.dump 2>/dev/null | head -n 1)"
    if [ -n "$newest_db" ]; then
        cp -p "$newest_db" "$DB_WEEKLY_DIR/db_${ts}.dump"
    fi
    newest_snap="$(ls -1d "$SNAP_DIR"/*/ 2>/dev/null | tail -n 1)"
    if [ -n "$newest_snap" ] && [ "$BACKUP_STORAGE" != "0" ]; then
        target="$SNAP_WEEKLY_DIR/$(basename "$newest_snap")"
        if [ ! -d "$target" ]; then
            mkdir -p "$target"
            cp -al "$newest_snap." "$target/" 2>/dev/null || true
        fi
    fi
}

prune() {
    find "$DB_DIR" -maxdepth 1 -name 'db_*.dump*' -mtime "+$BACKUP_DB_DAILY_KEEP" -delete 2>/dev/null || true
    find "$DB_WEEKLY_DIR" -maxdepth 1 -name 'db_*.dump*' \
        -mtime "+$((BACKUP_DB_WEEKLY_KEEP * 7))" -delete 2>/dev/null || true

    # Directory snapshots are pruned by count, newest kept.
    for dir in "$SNAP_DIR" "$SNAP_WEEKLY_DIR"; do
        [ -d "$dir" ] || continue
        case "$dir" in
            "$SNAP_DIR") keep="$BACKUP_STORAGE_DAILY_KEEP" ;;
            *) keep="$BACKUP_STORAGE_WEEKLY_KEEP" ;;
        esac
        count="$(ls -1d "$dir"/*/ 2>/dev/null | wc -l | tr -d ' ')"
        count="${count:-0}"
        [ "$count" -gt "$keep" ] || continue
        ls -1d "$dir"/*/ 2>/dev/null | head -n $((count - keep)) | while IFS= read -r old; do
            log "pruning $old"
            rm -rf "$old"
        done
    done
}

run_cycle() {
    log "cycle start"
    if wait_for_db; then
        dump_database
    else
        log "Postgres did not answer within ${BACKUP_WAIT_SECONDS}s, no database dump this cycle"
    fi

    if [ "$BACKUP_STORAGE" != "0" ]; then
        snapshot_storage
    else
        log "BACKUP_STORAGE=0: skipping the file snapshot"
    fi

    if [ "$(date +%u)" = "7" ]; then
        rotate_weekly
    fi
    prune

    date +%s > "$LAST_CYCLE"
    log "cycle done"
}

sleep_until_next_cycle() {
    # A container restart used to start a fresh full backup immediately, which
    # is how a redeploy at 01:27 turned into 19 hours of disk thrash.  Resume
    # from the last cycle instead.
    if [ ! -f "$LAST_CYCLE" ]; then
        log "first run: starting a backup now"
        return 0
    fi
    last="$(cat "$LAST_CYCLE" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age=$((now - last))
    if [ "$age" -lt 0 ]; then
        age=0
    fi
    if [ "$age" -lt "$BACKUP_INTERVAL_SECONDS" ]; then
        wait=$((BACKUP_INTERVAL_SECONDS - age))
        log "last cycle was ${age}s ago; waiting ${wait}s"
        sleep "$wait"
    fi
}

main() {
    mkdir -p "$DB_DIR" "$DB_WEEKLY_DIR" "$SNAP_DIR" "$SNAP_WEEKLY_DIR" "$STATE_DIR"
    log "NovelHub backup starting (storage=${BACKUP_STORAGE}, interval=${BACKUP_INTERVAL_SECONDS}s)"
    # ``BACKUP_ONCE=1`` runs a single cycle and exits: the manual "back up now"
    # switch, and what the tests drive the script with.
    if [ "${BACKUP_ONCE:-0}" = "1" ]; then
        run_cycle
        exit 0
    fi
    sleep_until_next_cycle
    while true; do
        run_cycle
        sleep "$BACKUP_INTERVAL_SECONDS"
    done
}

main "$@"
