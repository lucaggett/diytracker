#!/usr/bin/env bash
# Rebuild a corrupted SQLite database in place via `sqlite3 .recover`.
#
# Runs on the production box (stops/starts diytracker.service around the
# swap) or locally with --no-service. The original file plus its -wal/-shm
# siblings are kept next to it as *.corrupt.<timestamp>; the rebuilt file
# only replaces the original after it passes PRAGMA integrity_check.
#
# Usage: scripts/recover_db.sh [--no-service] [--force] [path/to/events.db]
#   --no-service  don't touch diytracker.service (local/dev copies)
#   --force       rebuild even if integrity_check already reports ok

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

manage_service=1
force=0
db=""
for arg in "$@"; do
    case "$arg" in
        --no-service) manage_service=0 ;;
        --force) force=1 ;;
        -*) echo "unknown option: $arg" >&2; exit 1 ;;
        *) db="$arg" ;;
    esac
done
db="${db:-$repo_root/instance/events.db}"

if [[ ! -f "$db" ]]; then
    echo "no database at $db" >&2
    exit 1
fi
command -v sqlite3 >/dev/null || { echo "sqlite3 not found on PATH" >&2; exit 1; }

echo "==> integrity check: $db"
check="$(sqlite3 "$db" 'PRAGMA integrity_check;')"
if [[ "$check" == "ok" && "$force" -eq 0 ]]; then
    echo "database is healthy; nothing to do (use --force to rebuild anyway)"
    exit 0
fi
[[ "$check" == "ok" ]] || sed 's/^/    /' <<<"$check"

service_stopped=0
restart_service() {
    if [[ "$service_stopped" -eq 1 ]]; then
        echo "==> starting diytracker.service"
        sudo -n systemctl start diytracker.service
        service_stopped=0
    fi
}
trap restart_service EXIT

if [[ "$manage_service" -eq 1 ]]; then
    echo "==> stopping diytracker.service"
    # -n: fail immediately if the sudoers rule is missing instead of hanging
    # on a password prompt (see deploy/sudoers-diytracker).
    sudo -n systemctl stop diytracker.service
    service_stopped=1
fi

stamp="$(date +%Y%m%d-%H%M%S)"
workdir="$(mktemp -d "${TMPDIR:-/tmp}/recover_db.XXXXXX")"
new_db="$workdir/rebuilt.db"

echo "==> backing up to $db.corrupt.$stamp"
cp "$db" "$db.corrupt.$stamp"
for sibling in "$db-wal" "$db-shm"; do
    [[ -f "$sibling" ]] && cp "$sibling" "$sibling.corrupt.$stamp"
done

echo "==> recovering into $new_db"
sqlite3 "$db" .recover > "$workdir/recovered.sql"
sqlite3 "$new_db" < "$workdir/recovered.sql"

echo "==> verifying rebuilt database"
new_check="$(sqlite3 "$new_db" 'PRAGMA integrity_check;')"
if [[ "$new_check" != "ok" ]]; then
    sed 's/^/    /' <<<"$new_check"
    echo "rebuilt database failed integrity_check; $db left untouched" >&2
    echo "recovery output kept in $workdir" >&2
    exit 1
fi

echo "==> row counts (old scan may double-count on a corrupt b-tree)"
for table in $(sqlite3 "$new_db" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"); do
    old_count="$(sqlite3 "$db" "SELECT count(*) FROM \"$table\";" 2>/dev/null || echo '?')"
    new_count="$(sqlite3 "$new_db" "SELECT count(*) FROM \"$table\";")"
    printf '    %-24s %6s -> %6s\n' "$table" "$old_count" "$new_count"
done

echo "==> swapping in rebuilt database"
rm -f "$db-wal" "$db-shm"
mv "$new_db" "$db"
rm -rf "$workdir"

restart_service
trap - EXIT

fk="$(sqlite3 "$db" 'PRAGMA foreign_key_check;' | cut -d'|' -f1,3 | sort | uniq -c)"
if [[ -n "$fk" ]]; then
    echo "==> note: pre-existing dangling references remain (count, table|referenced):"
    sed 's/^/    /' <<<"$fk"
fi

echo "==> done; backup kept at $db.corrupt.$stamp"
