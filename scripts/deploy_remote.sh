#!/usr/bin/env bash
# Trigger a production deploy (git pull + uv sync + service restart) over ssh.
#
# Usage: scripts/deploy_remote.sh [user@host]
#        DIYTRACKER_DEPLOY_HOST=diytrackeruser@example.com scripts/deploy_remote.sh
#
# Runs deploy/deploy.sh in the checkout on the server (~/diytracker) and
# streams its output; the exit code propagates.

set -euo pipefail

host="${1:-${DIYTRACKER_DEPLOY_HOST:-}}"
if [[ -z "$host" ]]; then
    echo "Usage: $0 [user@host]  (or set DIYTRACKER_DEPLOY_HOST)" >&2
    exit 1
fi

exec ssh "$host" diytracker/deploy/deploy.sh
