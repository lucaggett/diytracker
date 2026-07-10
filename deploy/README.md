# Deployment files

Server-side config for the production box (app checked out at
`/home/diytrackeruser/diytracker`, running as `diytrackeruser`). If either
of those changes, update the paths in both files here.

## systemd unit (`diytracker.service`)

Runs gunicorn in the foreground under systemd instead of the old
`manage.py start` daemonization. First-time switch-over:

```bash
cd /home/diytrackeruser/diytracker
uv sync                                   # make sure .venv/bin/gunicorn exists

# stop the old self-daemonized gunicorn if it's still running
# (manage.py's start/stop commands were removed when systemd took over)
kill -TERM "$(cat instance/gunicorn.pid)" 2>/dev/null || true

sudo cp deploy/diytracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now diytracker
systemctl status diytracker
```

Day-to-day: deploy with `deploy/deploy.sh` (see "Remote deploy" below), or
manually:

```bash
sudo systemctl restart diytracker    # after a git pull / uv sync
systemctl status diytracker
journalctl -u diytracker -e          # gunicorn stdout/stderr; app logs stay in logs/
```

Don't use `systemctl reload` (the unit deliberately has no ExecReload): a
HUP reload respawns the workers, and the scrape scheduler only starts in
the first worker the master ever forks, so a reload would silently kill
the scraper. Restart instead.

Server lifecycle is systemctl's job; `manage.py` only keeps `logs` and
`user …`.

## Remote deploy (`deploy.sh`, `sudoers-diytracker`)

`deploy/deploy.sh` runs the whole day-to-day procedure on the server:
`git pull --ff-only`, `uv sync`, `sudo systemctl restart diytracker`, then
waits for the service to report active. It needs a passwordless sudo rule
scoped to exactly that restart command.

One-time setup on the server (after pulling these files):

```bash
# sudoers matches full paths: check `command -v systemctl` is /usr/bin/systemctl
sudo install -m 440 deploy/sudoers-diytracker /etc/sudoers.d/diytracker
sudo visudo -c                                # syntax check

# verify as diytrackeruser — must restart without a password prompt
sudo -n systemctl restart diytracker.service
```

Then deploy from the dev machine with one command:

```bash
DIYTRACKER_DEPLOY_HOST=diytrackeruser@<host> scripts/deploy_remote.sh
# or: scripts/deploy_remote.sh diytrackeruser@<host>
```

`scripts/deploy_remote.sh` just ssh-es in and runs `deploy/deploy.sh`,
streaming its output; a failed pull, sync, or restart exits non-zero.

## nginx (`nginx-diytracker.conf`)

Reference copy of the nginx site config — the live one is
`/etc/nginx/sites-available/diytracker` on the box and is NOT tracked here,
so apply changes by hand and keep the two in sync. It covers:

- **Canonicalization (SEO):** 301s from plain HTTP and from
  `www.diytracker.ch` to `https://diytracker.ch`, so canonical tags,
  `og:url` and the sitemap all agree on one origin. Pair with
  `CANONICAL_HOST=https://diytracker.ch` in the production `.env`.
- **Static files from disk** with long-lived cache headers.
- **App-down error page:** `error_page 502 503 504` serves
  `static/errors/offline.html` — a self-contained zine-styled page that
  auto-retries every 30 s — instead of nginx's default white error page
  whenever gunicorn is unreachable. App-rendered errors (the styled
  404/500 pages) are untouched since `proxy_intercept_errors` stays off.

After editing the live config:

```bash
sudo nginx -t && sudo systemctl reload nginx
# simulate an outage to see the offline page:
sudo systemctl stop diytracker && curl -sk https://diytracker.ch | head -5
sudo systemctl start diytracker
```

## MOTD (`update-motd.d/50-diytracker`)

Dynamic login banner showing service state, last scrape, DB and disk usage
(Debian/Ubuntu `pam_motd`):

```bash
sudo install -m 755 deploy/update-motd.d/50-diytracker /etc/update-motd.d/50-diytracker
sudo run-parts --test /etc/update-motd.d/    # sanity check it's picked up
```

The output regenerates on each login. To preview: `sudo /etc/update-motd.d/50-diytracker`.
