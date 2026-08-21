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

## Run the app locally

Local runs are for testing changes before they reach the VPS. They use the
same image and the same `config.env`, so the dashboard renders the same way in
both places.

Two local modes exist. Pick by what you are changing.

### Run the dashboard alone

Use this for any change to `gamedaybot/web/`. It starts the Shiny server and
nothing else:

```bash
docker compose run --rm --service-ports --no-deps fantasy-bot python -m shiny run gamedaybot/web/app.py --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 to see it. Press Ctrl+C to stop.

The Discord bot and the scheduler stay down, so this posts nothing to Discord.
`--no-deps` keeps `cloudflared` down, so it publishes nothing to the internet.
This is the safest local mode, and the right default.

### Run the full stack

Use this only when you are changing the bot or the scheduler:

```bash
docker compose up -d --build fantasy-bot
```

Follow the logs to confirm the bot connected:

```bash
docker compose logs -f fantasy-bot
```

[docker-setup-preconfig.sh](docker-setup-preconfig.sh) and
[docker-setup-preconfig.bat](docker-setup-preconfig.bat) wrap the same command
with a Docker check and a summary of what to run next.

Naming `fantasy-bot` is required, not optional. A bare `docker compose up`
also starts `cloudflared`, which claims the same named tunnel the VPS runs.
Two connectors serving one hostname split traffic between them, so visitors
reach whichever answers first.

The scheduler runs inside `fantasy-bot`. While a local copy runs alongside the
VPS, both fire every scheduled post, and the Discord channel receives each
report twice. Stop the local stack as soon as you finish:

```bash
docker compose down
```

### Other local commands

Restart after editing `config.env`:

```bash
docker compose restart fantasy-bot
```

Show container status:

```bash
docker compose ps
```

Run the tests. The runtime image carries neither `pytest` nor `tests/`, so the
command mounts the directory and installs the runner first:

```bash
docker compose run --rm --no-deps --user root -v "./tests:/app/tests" fantasy-bot sh -c "pip install -q pytest && python -m pytest tests -q"
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

The dashboard reads `data/fantasy.db` and offers four destinations:

- **This week**: the front page. Results ordered closest-first, who moved in
  the standings since the prior week, the week's bests, and every team
  against its own average. Opens on the latest collected week; nothing to
  configure.
- **League**: the standings board — record, streak, last-five form, points
  for/against, and a sparkline per row, sortable by seed, points, or recent
  form — plus "the race," rank by week for every team on one chart.
- **Teams**: one page per team, absorbing what used to be Spread and Head to
  head. A game log, a range-vs-the-league bar (floor, ceiling, and median
  next to the league's), and every head-to-head matchup for that team.
- **Records**: the record book. Twelve season awards, each a row with its
  scoreline and week, linking straight to the team it belongs to.

League and Records carry a scope segment — Regular, Playoffs, or Full — that
sets which weeks are in play. The regular-season boundary is derived from the
data rather than configured: a team's wins plus losses plus ties is how many
games it has played, so a 13- or 15-week league needs no setting changed.

Selecting a team — a standings row, a matchup, a record-book row, or the team
picker on the Teams page — takes you to that team's own page rather than
filtering a shared chart. There is no reset button: each destination shows
either the whole league or one team, never a muted version of either.

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
   docker compose up -d
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

## Deploy to the VPS

The VPS is the live deployment. It serves the public dashboard and runs the
scheduler that posts to Discord. Local runs never touch it: there is no shared
state, and nothing syncs on its own.

Deployment is a file copy over SSH followed by a rebuild. The repository is
not cloned on the VPS, so `git push` deploys nothing.

The steps use these values:

| Value | Where it lives |
| --- | --- |
| Host | `65.109.238.176` |
| SSH key | `~/.ssh/hetzner_fantasy` |
| Project directory | `/opt/fantasy-football` on the VPS |
| Secrets | `config.env` in this repo, gitignored |
| Tunnel credentials | `~/.cloudflared` on the VPS |

Run the VPS stack from one host at a time. If a local copy is up, stop it with
`docker compose down` first. Two schedulers post every report twice, and two
tunnel connectors split traffic for one hostname.

### Update a running deployment

This is the common case: the VPS already runs, and you want your latest code
on it.

1. Open a shell on the VPS:

   ```bash
   ssh -i ~/.ssh/hetzner_fantasy root@65.109.238.176
   ```

2. Back up the database, then stop the stack:

   ```bash
   cd /opt/fantasy-football
   cp data/fantasy.db "data/fantasy.db.bak-$(date +%Y%m%d-%H%M%S)"
   docker compose down
   ```

   Stop before you copy. `docker compose down` reads the compose file that is
   still on disk, so it removes the containers by the names that created them.
   Copying first can rename a service and strand the old container running.

3. Move the old code aside, so removed files do not survive:

   ```bash
   mv gamedaybot gamedaybot.bak-$(date +%Y%m%d-%H%M%S)
   ```

   A copy merges into whatever is already there. Files deleted upstream stay
   behind and get built into the next image.

4. From your machine, in a second terminal, send the new tree:

   ```bash
   tar czf - --exclude='__pycache__' --exclude='*.pyc' gamedaybot dev Dockerfile docker-compose.yml requirements.txt .dockerignore | ssh -i ~/.ssh/hetzner_fantasy root@65.109.238.176 'cd /opt/fantasy-football && tar xzf -'
   ```

   `dev/` is required. The Dockerfile copies it, and the build fails without
   it. `data/` is not in the list, because the VPS database is the real one.

5. Optional: send `config.env` only when you have changed it. Compare first,
   and skip the copy when the checksums match:

   ```bash
   md5sum config.env
   ssh -i ~/.ssh/hetzner_fantasy root@65.109.238.176 'md5sum /opt/fantasy-football/config.env'
   ```

6. Back on the VPS, rebuild and start:

   ```bash
   docker compose up -d --build
   ```

Schema changes apply on start. `init_db()` adds any missing column, so no
migration command exists to run.

### Verify a deployment

Check each of these after a deploy:

1. Confirm both containers are up and the bot reports healthy:

   ```bash
   docker compose ps
   ```

2. Check the log for failures:

   ```bash
   docker compose logs --tail=30 fantasy-bot
   ```

3. Confirm the public dashboard answers:

   ```bash
   curl -o /dev/null -w '%{http_code}\n' https://fantasy.ethandbard.com/
   ```

### Roll back

Step 2 and step 3 of the update leave timestamped copies. To undo a bad
deploy, restore them and rebuild:

```bash
cd /opt/fantasy-football
docker compose down
rm -rf gamedaybot && mv gamedaybot.bak-<timestamp> gamedaybot
cp data/fantasy.db.bak-<timestamp> data/fantasy.db
docker compose up -d --build
```

Delete old backups once a deploy proves out. They accumulate.

### Set up a new VPS

Follow these steps only for a host that has never run the stack.

1. Provision a Linux VPS and set up SSH key access.
2. Install Docker and the Compose plugin on the VPS:

   ```bash
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
     > /etc/apt/sources.list.d/docker.list
   apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
   ```

3. Create the project directory on the VPS:

   ```bash
   mkdir -p /opt/fantasy-football
   ```

4. Copy the project from your machine. Skip `data/`, which step 6 creates:

   ```bash
   scp -r gamedaybot dev requirements.txt Dockerfile docker-compose.yml \
       .dockerignore config.env root@<vps-ip>:/opt/fantasy-football/
   ```

   Include `dev/`. The Dockerfile copies it, and the build fails without it.
   This is the one time you copy `config.env`, since the new host has no
   credentials yet.

5. Set up the tunnel on the VPS, so `~/.cloudflared` exists there. See
   [Publish the dashboard](#publish-the-dashboard-with-cloudflare-tunnel).
6. Create `data/`, then give it to the container's non-root user:

   ```bash
   mkdir -p /opt/fantasy-football/data
   chown -R 1000:1000 /opt/fantasy-football/data
   ```

   Docker creates a missing bind-mount directory as root. The container runs as
   uid `1000`, so it then fails to open the database.

7. Build and start the stack:

   ```bash
   cd /opt/fantasy-football
   docker compose up -d --build
   ```

## Development tools

Both scripts in `dev/` run inside the container and read credentials from the
environment.

Check that the ESPN API is reachable and preview the available data:

```bash
docker compose exec fantasy-bot python dev/api_healthcheck.py
```

The script exits non-zero on failure, so you can run it from a cron check.

Backfill a completed season into the dashboard database:

```bash
docker compose exec fantasy-bot python dev/backfill_season.py 2025
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
| `dev/` | Maintenance scripts. Copied into the image, so `docker compose exec` can run them. |
| `data/` | SQLite database. Mounted from the host. |
| `cloudflared/config.yml` | Reference copy of the tunnel config. The `cloudflared` container reads `~/.cloudflared` on the host instead. |
| `docs/` | Overview, slide deck, and the Worker for https://fantasy-docs.ethandbard.com/. |
| `config.env` | Secrets and runtime settings. Excluded by [.gitignore](.gitignore). |

Container logs go to Docker's `json-file` driver, capped at three 10 MB files.
Read them with `docker compose logs fantasy-bot`.

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
`docker compose logs fantasy-bot` for the SQLite permission error. The tunnel
retries once the app listens.

**`docker-compose: command not found` on the VPS.** Current installs ship
Compose as a Docker plugin. Run `docker compose` as two words.

**The VPS still serves old code after a deploy.** A file copy merges into the
existing tree, so a module you deleted upstream survives on the VPS and gets
built into the next image. Compare the two sides, then repeat the update and
move `gamedaybot` aside first:

```bash
md5sum gamedaybot/web/app.py
ssh -i ~/.ssh/hetzner_fantasy root@65.109.238.176 'md5sum /opt/fantasy-football/gamedaybot/web/app.py'
```

**Every Discord report arrives twice.** Two copies of `fantasy-bot` are
running, each with its own scheduler. Stop the local one with
`docker compose down`. The VPS keeps the live schedule.

## Project site

An overview page and a slide deck live in [`docs/`](docs/). They use the same palette as the dashboard and render to static HTML.

Preview:

```bash
quarto preview docs
```

Render:

```bash
quarto render docs
```

Output lands in `docs/_site`. Two public URLs serve that folder:

| URL | How it is served |
| --- | --- |
| https://ethandbard.github.io/fantasy-football/ | GitHub Pages, from the `gh-pages` branch |
| https://fantasy-docs.ethandbard.com/ | A Cloudflare Worker that fetches the GitHub Pages copy |

Push to `main` to publish content. The workflow in [`.github/workflows/quarto-publish.yml`](.github/workflows/quarto-publish.yml) renders `docs/` and deploys `docs/_site` to `gh-pages`. GitHub Pages serves that branch. The Worker at the custom hostname fetches the same GitHub Pages site. Both URLs then show the new render.

The Worker script is [`docs/proxy-worker.js`](docs/proxy-worker.js). Redeploy it only when that file changes:

```bash
cd docs
npx wrangler deploy
```

A change to the Quarto pages does not need `wrangler deploy`. Push to `main` is enough.

## Credits

Built on [gamedaybot](https://github.com/dtcarls/fantasy_football_chat_bot) by
Dean Carlson.
