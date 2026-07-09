#!/usr/bin/env bash
# Server-side deploy: pull, sync deps, restart the service, verify it came up.
#
# Runs on the production box as diytrackeruser, usually via
# scripts/deploy_remote.sh from a dev machine. Requires the sudoers drop-in
# (deploy/sudoers-diytracker) so the restart needs no password.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Non-interactive ssh sessions don't source the login profile, so uv's
# install dir isn't on PATH. Add the usual locations.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null; then
    echo "uv not found on PATH (checked ~/.local/bin and ~/.cargo/bin too)" >&2
    exit 1
fi

echo "==> git pull"
git pull --ff-only

echo "==> uv sync"
uv sync

echo "==> busting cache (rm /instance/cache/*)"
rm -f /instance/cache/*

echo "==> restarting diytracker.service"
# -n: fail immediately if the sudoers rule is missing instead of hanging on a
# password prompt we can't answer over a non-interactive ssh session.
sudo -n systemctl restart diytracker.service

# Type=notify: "active" means gunicorn actually finished starting, but give
# systemd a moment in case restart returned before the state settled.
for _ in $(seq 1 10); do
    state="$(systemctl is-active diytracker.service || true)"
    [[ "$state" == "active" ]] && break
    sleep 1
done

if [[ "$state" != "active" ]]; then
    echo "diytracker.service is '$state' after restart; check: journalctl -u diytracker -e" >&2
    exit 1
fi

echo "==> deployed $(git log -1 --oneline)"
systemctl status diytracker.service --no-pager --lines=0
