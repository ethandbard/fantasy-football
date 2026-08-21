# Fantasy football Discord bot

A Discord bot that posts ESPN fantasy football updates on a schedule, answers
slash commands, and serves a web dashboard of league history. It runs as a
Docker container with league credentials supplied through `config.env`.

## What the container runs

The entrypoint `gamedaybot/run.py` starts three parts in one process:

- A scheduler that posts recurring reports to a Discord webhook.
- A Discord gateway bot that answers slash commands, if `DISCORD_BOT_TOKEN` is
  set.
- A Shiny dashboard on port `8000`, backed by a SQLite database in `data/`.

A second container, `cloudflared`, publishes the dashboard at the hostname in
[cloudflared/config.yml](cloudflared/config.yml).

## Requirements

- Docker Desktop on Windows or macOS, or Docker Engine on Linux.
- A `config.env` file in the project root. See [Configuration](#configuration).
- A Cloudflare tunnel credentials file and a `config.yml` in `~/.cloudflared`
  on the Docker host, if you want a public dashboard. See
  [Publish the dashboard](#publish-the-dashboard-with-cloudflare-tunnel).

## Start the bot

1. Confirm `config.env` exists and holds the values you want.
2. Run the setup script for your platform:

   On Windows:

   ```cmd
   docker-setup-preconfig.bat
   ```

   On Linux or macOS:

   ```bash
   chmod +x docker-setup-preconfig.sh
   ./docker-setup-preconfig.sh
   ```

3. Check the logs to confirm the bot connected:

   ```bash
   docker-compose logs -f fantasy-bot
   ```

The startup message appears in the Discord channel that owns the webhook.

## Manage the bot

Stop the containers:

```bash
docker-compose down
```

Restart the bot after a config change:

```bash
docker-compose restart fantasy-bot
```

Rebuild after a code change:

```bash
docker-compose up -d --build
```

Show container status:

```bash
docker-compose ps
```

## Configuration

`config.env` holds every runtime setting. Docker Compose loads it through
`env_file`, so no secret is baked into the image. Restart the container after
you edit it.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | Yes | None | Destination for scheduled posts. |
| `LEAGUE_ID` | Yes | None | ESPN league ID. |
| `LEAGUE_YEAR` | No | `2026` | Season the bot reads. |
| `ESPN_S2` | Private leagues | None | ESPN auth cookie. |
| `SWID` | Private leagues | None | ESPN auth cookie. |
| `DISCORD_BOT_TOKEN` | No | None | Enables slash commands. Without it, only scheduled posts run. |
| `START_DATE` | No | `2026-09-04` | First day the scheduler fires jobs. |
| `END_DATE` | No | `2027-01-05` | Last day the scheduler fires jobs. |
| `TIMEZONE` | No | `America/New_York` | Timezone for morning and evening jobs. |
| `DAILY_WAIVER` | No | `False` | Posts the waiver report every day, not only Wednesday. |
| `MONITOR_REPORT` | No | `True` | Posts the Sunday injury report. |
| `TOP_HALF_SCORING` | No | `False` | Adds top-half scoring to standings. |
| `INIT_MSG` | No | None | Replaces the generated startup message. |
| `DASHBOARD_PORT` | No | `8000` | Port the dashboard binds inside the container. |
| `DB_PATH` | No | `/app/data/fantasy.db` | SQLite file backing the dashboard. |

`DASHBOARD_URL` is set in [docker-compose.yml](docker-compose.yml) and controls
the link that `/dashboard` returns.

Credentials for the live league are kept outside this repository, in
`~/.fantasy-football-secrets/WEBHOOK_BACKUP.md`.

`ESPN_S2` and `SWID` come from your browser cookies on espn.com. Both are
required for waiver reports, because ESPN treats transaction data as private.

## Scheduled posts

Jobs run only between `START_DATE` and `END_DATE`. Times marked "Eastern" are
pinned to `America/New_York` because they follow the NFL game clock. All other
times follow `TIMEZONE`.

| Day | Time | Report |
| --- | --- | --- |
| Monday | 9:00 AM | Score update |
| Monday | 6:30 PM Eastern | Close scores |
| Tuesday | 6:00 AM Eastern | Dashboard snapshot |
| Tuesday | 9:00 AM | Final scores and trophies for the previous week |
| Tuesday | 6:30 PM | Power rankings |
| Wednesday | 9:00 AM | Standings |
| Wednesday | 9:01 AM | Waiver report |
| Thursday | 7:30 PM Eastern | Matchups |
| Friday | 9:00 AM | Score update |
| Sunday | 9:00 AM | Player monitor report |
| Sunday | 4:00 PM and 8:00 PM Eastern | Score updates |

When `DAILY_WAIVER` is `True`, the waiver report runs at 9:01 AM every day
instead of only on Wednesday.

The Tuesday 6:00 AM snapshot writes the finished week's scores and standings to
`data/fantasy.db`. It runs after Monday night football so the week is final.

## Slash commands

Slash commands need `DISCORD_BOT_TOKEN`. The bot syncs its command tree to
every guild it joins when it connects.

| Command | Returns |
| --- | --- |
| `/matchups` | The current week's matchups. |
| `/scoreboard` | The current scoreboard. |
| `/standings` | The current standings. |
| `/power-rankings` | The current power rankings. |
| `/monitor` | The injury and player monitor report. |
| `/trophies` | This week's trophies. |
| `/waiver-report` | Recent waiver moves. Private leagues only. |
| `/dashboard` | A link to the web dashboard. |

If ESPN returns an error, the bot replies with a message explaining that the
season may not have started yet.

## Dashboard

The dashboard reads `data/fantasy.db` and offers five tabs:

- **Trend**: weekly score per team, with views for cumulative points, rank by
  week, and performance against ESPN's projection.
- **Standings**: records, point differential, current streak, and last-five
  form, alongside ESPN's official seed.
- **Spread**: the score distribution per team, with median, floor, ceiling,
  standard deviation, and coefficient of variation.
- **Head to head**: every pairing's record, tinted by average margin.
- **Trophies**: eleven season awards. Selecting one follows that team to the
  Trend tab with its week marked.

A control bar above the tabs sets which weeks are in play. It opens on the
regular season, and the boundary is derived from the data rather than
configured: a team's wins plus losses plus ties is how many games it has
played, so a 13- or 15-week league needs no setting changed. Presets jump to
the regular season, the playoffs, or the full season.

Selecting a team — from a standings row or a trophy — follows it across every
tab: other lines mute, and a panel below the stat tiles shows that team's game
log. **Reset** returns to the league view.

A dropdown in the masthead selects the season. Only seasons present in the
database appear there, so a new season stays empty until the first Tuesday
snapshot runs. With one season collected it renders as plain text rather than
a dropdown that cannot change anything.

The dashboard polls the database every 30 seconds. New snapshots reach an open
browser tab on their own, and a new season joins the dropdown without a
restart or a page refresh.

Reach the dashboard at `http://localhost:8000` locally, or at the tunnel
hostname if `cloudflared` is running.

## Publish the dashboard with Cloudflare Tunnel

The `cloudflared` service in [docker-compose.yml](docker-compose.yml) runs a
named Cloudflare tunnel. The tunnel proxies a public hostname to port `8000` on
the `fantasy-bot` container. It reads its config from `~/.cloudflared` on the
Docker host. The `cloudflared/` folder in this project is a reference copy only.

Set up the tunnel once per host:

1. Install `cloudflared` on the host.
2. Authenticate `cloudflared` to your Cloudflare account:

   ```bash
   cloudflared tunnel login
   ```

3. Create the tunnel:

   ```bash
   cloudflared tunnel create fantasy-bot
   ```

4. Route a hostname to the tunnel:

   ```bash
   cloudflared tunnel route dns fantasy-bot fantasy.yourdomain.com
   ```

5. Write `~/.cloudflared/config.yml` on the host, using the tunnel ID from
   step 3:

   ```yaml
   tunnel: <tunnel-id>
   credentials-file: /etc/cloudflared/<tunnel-id>.json
   ingress:
     - hostname: fantasy.yourdomain.com
       service: http://fantasy-bot:8000
     - service: http_status:404
   ```

6. In `docker-compose.yml`, set `DASHBOARD_URL` to the hostname from step 4.
7. Start the stack:

   ```bash
   docker-compose up -d
   ```

Tunnel credentials belong to the tunnel, not to the machine. To move the stack
to another host, copy `~/.cloudflared/config.yml` and the matching
`<tunnel-id>.json` file across. Create a second tunnel only when you want to
repoint DNS to a new one.

Run the tunnel from one host at a time. The `fantasy-bot` container also runs
the scheduler, so two running copies post every report twice.

To test without a Cloudflare account or a domain, run a quick tunnel. It prints
a random `trycloudflare.com` URL that changes on every restart:

```bash
cloudflared tunnel --url http://localhost:8000
```

## Deploy to a remote host

The container behaves the same on any host. A move takes a file transfer and
the tunnel setup on the new host. These steps assume an Ubuntu VPS.

1. Provision a Linux VPS and set up SSH key access.
2. Install Docker and the Compose plugin on the VPS:

   ```bash
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
     > /etc/apt/sources.list.d/docker.list
   apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
   ```

3. Copy the project to the VPS. Skip `data/`, which the host creates fresh:

   ```bash
   scp -r gamedaybot dev requirements.txt Dockerfile docker-compose.yml \
       .dockerignore config.env root@<vps-ip>:/opt/fantasy-football/
   ```

   The image builds `dev/`, so the copy fails without it.

4. Set up the tunnel on the VPS, so `~/.cloudflared` exists there. See
   [Publish the dashboard](#publish-the-dashboard-with-cloudflare-tunnel).
5. Create `data/`, then give it to the container's non-root user:

   ```bash
   mkdir -p /opt/fantasy-football/data
   chown -R 1000:1000 /opt/fantasy-football/data
   ```

   Docker creates a missing bind-mount directory as root. The container runs as
   uid `1000`, so it then fails to open the database.

6. Build and start the stack:

   ```bash
   cd /opt/fantasy-football
   docker-compose up -d --build
   ```

If another machine already runs the stack, stop it there with
`docker-compose down` before you start the new copy. Two schedulers post every
report twice.

## Development tools

Both scripts in `dev/` run inside the container and read credentials from the
environment.

Check that the ESPN API is reachable and preview the available data:

```bash
docker-compose exec fantasy-bot python dev/api_healthcheck.py
```

The script exits non-zero on failure, so you can run it from a cron check.

Backfill a completed season into the dashboard database:

```bash
docker-compose exec fantasy-bot python dev/backfill_season.py 2025
```

The year is a command-line argument because `LEAGUE_YEAR` stays pinned to the
current season. Both scripts run in the container that is already up, so
neither needs a rebuild or a restart.

The Tuesday snapshot also fills in any week it finds missing for the current
season. A container that was down over a Tuesday repairs its own gap on the
next run, so `backfill_season.py` is only needed for prior seasons.

## Project layout

| Path | Contents |
| --- | --- |
| `gamedaybot/run.py` | Container entrypoint. |
| `gamedaybot/espn/` | ESPN API access, report text, and the scheduler. |
| `gamedaybot/discord_bot/` | Slash-command bot, webhook client, and embed formatting. |
| `gamedaybot/storage/db.py` | SQLite schema and queries. |
| `gamedaybot/web/app.py` | Shiny dashboard: layout and reactive wiring. |
| `gamedaybot/web/stats.py` | Season arithmetic — records, streaks, head-to-head, trophies. |
| `gamedaybot/web/charts.py` | Plotly figure builders and their shared styling. |
| `gamedaybot/web/theme.py` | Team palette and the stat-tile sparkline. |
| `gamedaybot/web/www/dashboard.css` | Dashboard styling. |
| `tests/` | Tests for `web/stats.py`. Run with `pytest`. |
| `dev/` | Maintenance scripts. Copied into the image, so `docker-compose exec` can run them. |
| `data/` | SQLite database. Mounted from the host. |
| `cloudflared/config.yml` | Reference copy of the tunnel config. The `cloudflared` container reads `~/.cloudflared` on the host instead. |
| `config.env` | Secrets and runtime settings. Excluded by [.gitignore](.gitignore). |

Container logs go to Docker's `json-file` driver, capped at three 10 MB files.
Read them with `docker-compose logs fantasy-bot`.

## Troubleshooting

**The bot posts nothing.** Check that the current date falls between
`START_DATE` and `END_DATE`. Outside that window the scheduler registers jobs
but never fires them.

**Slash commands don't appear.** Confirm `DISCORD_BOT_TOKEN` is set, then check
the logs for a "Synced slash commands" line. The bot syncs on connect, so
restart it after inviting it to a new server.

**`/waiver-report` reports a private-league error.** Set `ESPN_S2` and `SWID`
in `config.env`, then restart the container.

**The dashboard is empty.** No snapshot has run yet for the selected season.
Use `dev/backfill_season.py` to load a completed season. The dashboard picks
the new season up within 30 seconds, with no restart.

**The bot's replies and the dashboard disagree on the season.** Slash commands
and scheduled posts read the ESPN API live, using `LEAGUE_YEAR`. The dashboard
shows only the seasons collected into `data/fantasy.db`. Its season dropdown
falls back to the current calendar year until a snapshot exists. Run
`dev/backfill_season.py` for the year you want.

**The `fantasy-bot` container exits with `sqlite3.OperationalError: unable to
open database file`.** Docker created the `data/` bind mount as root, but the
container runs as uid `1000`. Run `chown -R 1000:1000 data` on the host, then
restart the container.

**Cloudflare Tunnel returns a 502.** Either `cloudflared` connected before
`fantasy-bot` finished starting, or `fantasy-bot` crashed. Check
`docker-compose logs fantasy-bot` for the SQLite permission error. The tunnel
retries once the app listens.

## Credits

Built on [gamedaybot](https://github.com/dtcarls/fantasy_football_chat_bot) by
Dean Carlson.
