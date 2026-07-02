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

Day-to-day:

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

## MOTD (`update-motd.d/50-diytracker`)

Dynamic login banner showing service state, last scrape, DB and disk usage
(Debian/Ubuntu `pam_motd`):

```bash
sudo install -m 755 deploy/update-motd.d/50-diytracker /etc/update-motd.d/50-diytracker
sudo run-parts --test /etc/update-motd.d/    # sanity check it's picked up
```

The output regenerates on each login. To preview: `sudo /etc/update-motd.d/50-diytracker`.
