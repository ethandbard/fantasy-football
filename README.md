# Fantasy football Discord bot

A Discord bot that posts ESPN fantasy football updates on a schedule, answers
slash commands, and serves a web dashboard of league history. It runs as a
Docker container with league credentials supplied through `config.env`.

**Live**: [fantasy.ethandbard.com](https://fantasy.ethandbard.com), listed on [ethandbard.com](https://ethandbard.com). Docs: [ethandbard.github.io/fantasy-football](https://ethandbard.github.io/fantasy-football/).

## What the container runs

The entrypoint `gamedaybot/run.py` starts three parts in one process:

- A scheduler that posts recurring reports to a Discord webhook.
- A Discord gateway bot that answers slash commands, if `DISCORD_BOT_TOKEN` is
  set.
- A Shiny dashboard on port `8000`, backed by a SQLite database in `data/`.

An optional `cloudflared` sidecar, started with `--profile tunnel`, publishes
the dashboard at a hostname you own. On the VPS that sidecar stays off: the
shared connector in `/opt/cloudflared` routes `fantasy.ethandbard.com` instead.

## Requirements

- Docker Desktop on Windows or macOS, or Docker Engine on Linux.
- A `config.env` file in the project root. Copy [config.env.example](config.env.example)
  and fill it in. See [Configuration](#configuration).
- For a public dashboard off the VPS: a dedicated Cloudflare tunnel's
  `config.yml` and credentials JSON in `tunnel/`. See
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
This is the safest local mode, and the right default.

### Run the full stack

Use this only when you are changing the bot or the scheduler:

```bash
docker compose up -d --build
```

`cloudflared` is behind the `tunnel` profile, so a bare `up` does not publish
anything to the internet.

Follow the logs to confirm the bot connected:

```bash
docker compose logs -f fantasy-bot
```

[docker-setup-preconfig.sh](docker-setup-preconfig.sh) and
[docker-setup-preconfig.bat](docker-setup-preconfig.bat) wrap the same command
with a Docker check and a summary of what to run next.

The scheduler runs inside `fantasy-bot`. While a local copy runs alongside
another copy with the same `DISCORD_WEBHOOK_URL`, both fire every scheduled
post. Stop the extra stack as soon as you finish:

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
| `DISCORD_WEBHOOK_URL` | Yes | None | Destination for scheduled posts. Comma-separate two URLs to post to both channels. |
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
| `INIT_WEBHOOK_URL` | No | None | Webhook(s) that receive the startup message instead of every `DISCORD_WEBHOOK_URL`, so restarts stay out of the league channel. |
| `DASHBOARD_PORT` | No | `8000` | Port the dashboard binds inside the container. |
| `DB_PATH` | No | `/app/data/fantasy.db` | SQLite file backing the dashboard. |
| `DASHBOARD_URL` | No | `http://localhost:<port>` | Link the `/dashboard` slash command returns. |
| `AGENT_URL` | No | `http://fantasy-agent:8010` | Where the bot reaches the agent service. |
| `OWNER_DISCORD_ID` | Agents | None | Discord user id allowed to run agents and approve asks. |
| `AGENT_CHANNEL_ID` | Agents | None | Channel where the bot posts approval requests with reactions. |
| `AGENT_WEBHOOK_URL` | Agents | None | Webhook the agent service posts briefs to (a `#team-agent` channel). |
| `CLAUDE_CODE_OAUTH_TOKEN` | Agents | None | Long-lived token from `claude setup-token`. `ANTHROPIC_API_KEY` works instead. |
| `TEAM_ID` | No | `11` | The ESPN team the agents manage. |
| `AGENT_ESPN_S2` / `AGENT_SWID` | If the bot's cookies are not the team owner's | falls back to `ESPN_S2` / `SWID` | Cookies from the account that owns `TEAM_ID`. Any league member's cookies can read the league, but ESPN refuses writes from anyone but the owner. |
| `AGENT_DRY_RUN` | No | `False` | `True` previews every write and posts nothing to ESPN. |
| `AGENT_SCHEDULE` | No | `True` | `False` disables the fixed weekly jobs (wakeups and on-demand runs still work). |
| `AGENT_MODEL_HEAVY` / `AGENT_MODEL_LIGHT` | No | `opus` / `sonnet` | Models for the Tuesday and trade jobs, and for everything else. |
| `AGENT_ASK_DAILY_LIMIT` / `AGENT_ASK_LEAGUE_DAILY_LIMIT` | No | `3` / `20` | `/ask` questions per person and per league per day. `0` means no limit. |
| `AGENT_HEAVY_SEARCH_CAP` / `AGENT_LIGHT_SEARCH_CAP` | No | `40` / `8` | Web searches a run may spend. |
| `AGENT_HEAVY_MAX_TURNS` / `AGENT_LIGHT_MAX_TURNS` | No | `200` / `60` | Turn caps per run. |
| `ANTHROPIC_API_KEY` | Dashboard chat | None | Pays for the dashboard's data chat by the token. Without it the Chat page says it is not configured. |
| `CHAT_PASSPHRASE` | Dashboard chat | None | Shared league passphrase that opens the Chat page. Unset disables the chat. |
| `CHAT_MODEL` | No | `claude-haiku-4-5-20251001` | Model behind the data chat. |
| `CHAT_DAILY_LIMIT` / `CHAT_LEAGUE_DAILY_LIMIT` | No | `20` / `200` | Chat questions per browser and per league per day. |

Each running copy of the container is one league. Scheduled posts go to every
URL in `DISCORD_WEBHOOK_URL`. Slash commands follow the bot into every server
it has been invited to — one `DISCORD_BOT_TOKEN`, not two containers. Two
containers with the same bot token fight over the Discord gateway.

A friend running their own league needs their own `config.env`, their own
`DISCORD_BOT_TOKEN`, and their own webhook.

Credentials for the live league are kept outside this repository, in
`~/.fantasy-football-secrets/WEBHOOK_BACKUP.md`.

`ESPN_S2` and `SWID` come from your browser cookies on espn.com. Both are
required for waiver reports and trade alerts, because ESPN treats transaction
data as private.

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
| Every day | 7 minutes past each hour | Trade check |

When `DAILY_WAIVER` is `True`, the waiver report runs at 9:01 AM every day
instead of only on Wednesday.

The hourly trade check stores every trade it sees in `data/fantasy.db` and
posts a Trade Alert embed for each one it has never stored before, so a trade
is announced once, within the hour it clears. Trades older than three days are
stored without a post — that only matters on a first deploy mid-season, where
announcing the backlog would read as spam. Like the waiver report, it needs
`ESPN_S2` and `SWID`; ESPN also only serves transaction activity for the
active season, so past seasons' trades cannot be backfilled.

The Tuesday 6:00 AM snapshot writes the finished week's scores and standings to
`data/fantasy.db`. It runs after Monday night football so the week is final.
ESPN has already moved its current week on by then, so the job fetches every
week up to it and stores only the ones with points on the board. A week nobody
has played yet is never stored, and a stale pre-kickoff snapshot of one is
removed once the run sees it is unplayed.

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

## Team agents

A second container, `fantasy-agent`, runs Claude agents that manage one team
(`TEAM_ID`) and report to a `#team-agent` channel. The design, including why
it runs here and not in the cloud, is in
[fantasy-football-agents/AGENT-PLAN.md](fantasy-football-agents/AGENT-PLAN.md).
The rules the agents work under are
[fantasy-football-agents/RULES.md](fantasy-football-agents/RULES.md); a copy
in `data/agent/RULES.md` and a `data/agent/rules.json` override the shipped
versions without a redeploy.

Roster actions go through ESPN's own transaction endpoint with the same
`ESPN_S2` and `SWID` cookies the bot uses. No browser is involved. Every write
is a preview followed by an execute, and a permission tier decides what
happens: auto moves post at once, "ask" moves wait for the owner in Discord,
and "never" moves are refused.

### Schedule

Times are in `TIMEZONE`.

| When | Job | Model | Does |
| --- | --- | --- | --- |
| Tuesday 6:30 AM | League recap | light | Writes last week's recap for the dashboard's This week page into the `site_content` table. Public league data only — this season from ESPN, past seasons and each pairing's all-time history from the dashboard database — no web, no Discord post. |
| Tuesday 6:40 AM | Power rankings | light | Ranks the league 1–8 after last week, for the dashboard's League page. Same rules as the recap. |
| Wednesday 10:30 AM | Matchup preview | light | Previews this week's four matchups after waivers clear, for the dashboard's Next up page. Same rules as the recap. |
| Tuesday 7:00 AM | Research | heavy | Reviews last week, all rosters, free agents, trade market. Writes `data/agent/research/week-NN.md` and `state/week-NN.json`. |
| Tuesday 8:00 AM | Roster plan | heavy | Queues waiver claims with fallbacks, sets the lineup, proposes at most one trade, schedules the pre-game checks. |
| Tuesday 9:15 AM | Wakeup safety net | none | Plans the pre-game checks if the plan job did not. |
| Wednesday 9:30 AM | Post-waiver adjust | light | Reconciles claims, runs free-agent fallbacks, re-sets the lineup. |
| Friday 5:30 PM | Designations | light | Benches anyone ruled out, writes Sunday contingencies. |
| Kickoff minus 60 min | Pre-game check | light | One per distinct kickoff with a rostered player. Swaps inactives out. |
| Monday 9:00 PM | Week ahead | none | Posts the pending pre-game checks, in Eastern time. The scheduler calls this job `preview`; the dashboard's matchup preview is `preview_site`. |
| Daily 6:45 AM | Auth canary | none | Reads the league with the cookies; posts only on failure. |
| Every 15 min | Offer poll | none | A new incoming trade offer starts a trade review. |

Pre-game wakeups live in the `agent_wakeups` table, so a restart loses none.

### Agent slash commands

| Command | Who | Does |
| --- | --- | --- |
| `/agent status` | owner | What runs next, recent runs with cost, pending approvals, canary. Planned pre-game checks and the fixed jobs come as one list in Eastern time, soonest first; the every-minute housekeeping jobs (`tick`, `poll_offers`, `expire_asks`) are left out. |
| `/agent research`, `/agent plan`, `/agent lineup` | owner | Runs that job now. The brief posts to the agent channel. |
| `/agent recap [week]` | owner | Writes the league recap for the dashboard now, for the week just played unless a week is given. |
| `/agent preview` | owner | Writes this week's matchup preview for the dashboard now. |
| `/agent power [week]` | owner | Writes the power rankings for the dashboard now. |
| `/agent wakeups` | owner | Re-plans this week's pre-game checks from the current kickoff schedule. No model run; safe to repeat. Use it when the checks were never planned, for example after a mid-week deploy. |
| `/agent trade <text>` | owner | Asks the trade reviewer about an offer in your own words. |
| `/agent approve <id>`, `/agent reject <id>` | owner | Resolves a pending ask. Reacting ✅ or ❌ on the ask message does the same. |
| `/claim-team` | anyone | Maps your Discord account to your ESPN team. |
| `/ask <question>` | anyone | The league analyst answers for your team in a thread. Reply in that thread to follow up; the bot sends the thread so far along with your message, each line tagged with the speaker's team. The analyst knows the current week and says when something is not visible to it, such as a declined trade offer. Read-only tools, no access to the owner's research. Limits are configurable. |

### Running it

The service starts with the stack (`docker compose up -d --build`). It needs
`CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) in `config.env`, plus the
three Discord values in the configuration table. Without a token it starts,
serves `/agent status`, runs the canary, and fails every model run with a
clear error in the agent channel.

To try a job from a shell against the live league without posting anything:

```bash
docker compose run --rm --no-deps -e AGENT_DRY_RUN=true fantasy-agent python -m agent.cli run lineup
```

`python -m agent.cli` also has `canary`, `wakeups`, and `roster`. The
one-time write probe is
[fantasy-football-agents/tools/espn_write_probe.py](fantasy-football-agents/tools/espn_write_probe.py).

Agent state lives under `data/agent/`: the research and state files, the
season log the agents append to, per-run tool logs in `runs/`, and the
tables `agent_runs`, `agent_wakeups`, `agent_asks`, `agent_transactions`,
`agent_users` in `fantasy.db`.

## Dashboard

The dashboard reads `data/fantasy.db` and offers six destinations:

- **This week**: the front page. Results ordered closest-first, who moved in
  the standings since the prior week, the week's bests, and every team
  against its own average. Opens on the latest collected week; nothing to
  configure.
- **Next up**: the coming week's matchups, before they are played. Each card
  shows both teams' managers, records, season averages, and last-five form,
  ESPN's projected scores, a win probability derived from each team's scored
  weeks — labelled as the rough read it is — and the two managers' history
  across every season on file: the all-time series, one pip per past meeting
  grouped by season (ringed for a postseason meeting; hover for the score),
  the current streak, and the last meeting. Projections refresh with the daily player-pool job. Once a
  season is played out the page says so instead of inventing a matchup.
- **Draft**: ESPN's player pool for this league. Rank, ADP, bye, projected
  FPTS (in the league's scoring), last year's FPTS, and position counting
  stats (PC/PA/PY and the RB/WR/TE equivalents). After the draft, each row
  also shows pick and club. Filter by position or club, search by name or
  team, and sort any column. Opens first when the selected season has a
  player pool and no weekly scores yet — the preseason case.
- **League**: the standings board — record, streak, last-five form, points
  for/against, and a sparkline per row, sortable by seed, points, or recent
  form — plus the head-to-head grid, the season's trade ledger (each trade
  as a date and a "receives" column per team, fed by the hourly trade
  check), and "the race," rank by week for every team on one chart.
- **Teams**: one page per team, absorbing what used to be Spread and Head to
  head. A game log, a range-vs-the-league bar (floor, ceiling, and median
  next to the league's), and every head-to-head matchup for that team.
- **Records**: the record book. Twelve season awards, each a row with its
  scoreline and week, linking straight to the team it belongs to.

Team logos appear throughout — result rows, standings, the team pages, the
head-to-head grid, the Next up cards, and at each line's endpoint on the
charts. A team that never uploaded a logo to ESPN gets a colored monogram in
its chart color instead of ESPN's grey default silhouette. Logos are
downloaded once per URL by the daily collection job and stored in the
database; the dashboard serves them from `data/logos/`, which it creates
itself.

All-time views follow the manager, not the team. Most of the league renames
its team every year, and ESPN hands a departed manager's team id to whoever
joins next, so neither the name nor the id identifies a team across seasons.
The daily collect stores ESPN's owner GUID with each team, and the all-time
head-to-head grid, the Next up history, the data chat, and the agents' history
tools all join seasons on it. A manager appears under the team name they use
in the season on screen; a team page lists the names they used in other
seasons. Run `dev/set_managers.py` once to fill the GUID in for seasons
collected before it was stored and to give managers the names the league
knows them by (see [Development tools](#development-tools)).

League and Records carry a scope segment — Regular, Playoffs, or Full — that
sets which weeks are in play. The regular-season boundary is derived from the
data rather than configured: a team's wins plus losses plus ties is how many
games it has played, so a 13- or 15-week league needs no setting changed.

Selecting a team — a standings row, a matchup, a record-book row, or the team
picker on the Teams page — takes you to that team's own page rather than
filtering a shared chart. There is no reset button: each destination shows
either the whole league or one team, never a muted version of either.

A dropdown in the masthead selects the season. Only seasons present in the
database appear there. A season lands in the list once weekly scores or the
player pool have been collected, so the draft board can show the upcoming
year before week 1. With one season collected it renders as plain text rather
than a dropdown that cannot change anything.

The dashboard polls the database every 30 seconds. New snapshots reach an open
browser tab on their own, and a new season joins the dropdown without a
restart or a page refresh.

Reach the dashboard at `http://localhost:8000` locally, or at the tunnel
hostname if a tunnel is running.

## Publish the dashboard with Cloudflare Tunnel

The dashboard needs a public hostname. Discord webhooks are outbound and do
not. Pick the path that matches the machine.

**On the VPS.** Do not start the `tunnel` profile. [compose.vps.yml](compose.vps.yml)
joins the shared `edge` network. The connector at `/opt/cloudflared` already
routes `fantasy.ethandbard.com` to this container. That connector also serves
every other `*.ethandbard.com` project on the box. See `deploy-pipeline`.

**On another machine, using a hostname under ethandbard.com.** The friend does
not need a Cloudflare account. You create a **dedicated** named tunnel in your
account and hand them two files. Do not give them the VPS tunnel JSON. That
file authenticates as the shared edge, which can route every hostname on it.

1. On a machine logged into your Cloudflare account, create a tunnel and DNS
   record:

   ```bash
   cloudflared tunnel create ff-alex
   cloudflared tunnel route dns ff-alex alex-fantasy.ethandbard.com
   ```

2. Copy the new `<uuid>.json` and a `config.yml` into the friend's
   `tunnel/` directory. Start from [tunnel/config.yml.example](tunnel/config.yml.example).
   Ingress must target `http://fantasy-bot:8000`.

3. In their `config.env`, set `DASHBOARD_URL` to `https://alex-fantasy.ethandbard.com`.
   Fill in their `LEAGUE_ID`, `DISCORD_WEBHOOK_URL`, and `DISCORD_BOT_TOKEN`.

4. Start the stack with the sidecar:

   ```bash
   docker compose --profile tunnel up -d --build
   ```

One named tunnel cannot serve origins on two machines. Two connectors that
share a tunnel UUID load-balance, so visitors hit whichever answers first.
A friend's copy therefore gets its own tunnel, even when the hostname is
still under ethandbard.com.

To move one instance to a new host, copy that instance's `tunnel/` files and
its `config.env`. Stop the old copy first so two schedulers do not post.

To test without creating a hostname, run a quick tunnel. It prints a random
`trycloudflare.com` URL that changes on every restart:

```bash
cloudflared tunnel --url http://localhost:8000
```

## Deploy to the VPS

The VPS is one place this container can run. It serves the public dashboard
and runs the scheduler that posts to Discord. Local runs never touch it:
there is no shared state, and nothing syncs on its own.

On the VPS this project is an app on the shared `edge` network, not the owner
of the tunnel. `/opt/fantasy-football/.env` sets
`COMPOSE_FILE=docker-compose.yml:compose.vps.yml` so Compose joins `edge` and
publishes no host port. The connector in `/opt/cloudflared` routes
`fantasy.ethandbard.com` by container name.

Deployment is a copy of `HEAD` over SSH followed by a rebuild. The
repository is not cloned on the VPS, so `git push` deploys nothing.
`deploy-pipeline` documents the shared tunnel and `deploy.sh`. The public
hostname is not behind Cloudflare Access.

Connection details (SSH host, key, tunnel UUID) live in the private
`deploy-pipeline` skill. This README does not repeat them.

| Value | Where it lives |
| --- | --- |
| Project directory | `/opt/fantasy-football` on the VPS |
| Public URL | `fantasy.ethandbard.com` |
| Secrets | `config.env` in this repo, gitignored |
| Shared tunnel | `/root/.cloudflared` and `/opt/cloudflared` on the VPS |

Run one copy of a given `config.env` at a time. A second copy with the same
webhook posts every report twice.

### Update a running deployment

This is the common case: the VPS already runs, and you want your latest code
on it.

1. Open a shell on the VPS with the key named in `deploy-pipeline`
   (`~/.ssh/hetzner_vps`):

   ```bash
   ssh -i ~/.ssh/hetzner_vps root@<vps-host>
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

4. From your machine, send `HEAD`. Prefer
   `deploy-pipeline/skill/scripts/deploy.sh fantasy-football fantasy`.
   A manual copy looks like:

   ```bash
   git archive HEAD -- gamedaybot dev Dockerfile docker-compose.yml compose.vps.yml requirements.txt .dockerignore \
     | ssh -i ~/.ssh/hetzner_vps root@<vps-host> 'cd /opt/fantasy-football && tar xf -'
   ```

   `dev/` is required. The Dockerfile copies it, and the build fails without
   it. `data/` is not in the list, because the VPS database is the real one.

5. Optional: send `config.env` only when you have changed it. Compare first,
   and skip the copy when the checksums match:

   ```bash
   md5sum config.env
   ssh -i ~/.ssh/hetzner_vps root@<vps-host> 'md5sum /opt/fantasy-football/config.env'
   ```

6. Back on the VPS, rebuild and start. `COMPOSE_FILE` in `.env` already
   includes [compose.vps.yml](compose.vps.yml):

   ```bash
   docker compose up -d --build
   ```

Schema changes apply on start. `init_db()` adds any missing column, so no
migration command exists to run.

### Verify a deployment

Check each of these after a deploy:

1. Confirm `fantasy-football-bot` is up. The tunnel is the shared
   `cloudflared` container, not part of this stack:

   ```bash
   docker compose ps
   docker ps --filter name=cloudflared
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

5. Confirm the shared `edge` network and `/opt/cloudflared` stack are running.
   This project does not start its own connector on the VPS. See
   `deploy-pipeline`.
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

The scripts in `dev/` run inside the container and read credentials from the
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

Pull ESPN's player pool, team names, team logos, draft picks, and the season
schedule (with projected scores) into the dashboard database. The full
container does this on startup and every morning; run it yourself when the
dashboard is up alone:

```bash
docker compose exec fantasy-bot python dev/collect_players.py
```

Pass a year to collect a different season than `LEAGUE_YEAR`:

```bash
docker compose exec fantasy-bot python dev/collect_players.py 2025
```

Tie every season's teams to the managers who ran them:

```bash
docker compose exec fantasy-bot python dev/set_managers.py
```

The script re-collects the team rows of every season in the database, so each
carries ESPN's owner GUID, then reads `data/managers.txt` if it exists and
stores what the league calls each manager. Without the file, ESPN's first
names are used. The file is a season followed by `Team name - Person` lines:

```text
2025
First Down Syndrome - Josh
Yikes (3) - Ed
```

One line anywhere in a manager's history names them in every season. Team
names match loosely (case, spacing, and punctuation are ignored), and a season
with exactly one unmatched line and one unmatched team pairs the two and says
so. The file holds real first names, so it lives in `data/`, which git
ignores. The script is safe to run again, and `--no-refresh` skips the ESPN
calls when only the names changed.

## Project layout

| Path | Contents |
| --- | --- |
| `gamedaybot/run.py` | Container entrypoint. |
| `gamedaybot/espn/` | ESPN API access, report text, player pool, and the scheduler. |
| `gamedaybot/discord_bot/` | Slash-command bot, webhook client, and embed formatting. |
| `gamedaybot/storage/db.py` | SQLite schema and queries. |
| `gamedaybot/espn/writes.py` | ESPN transaction payloads and the write client (lineup, add/drop, waivers, trades). |
| `gamedaybot/espn/roster.py` | Roster reads with slot ids, lock flags, kickoffs, and the lineup legality check. |
| `gamedaybot/discord_bot/agent_commands.py` | `/agent`, `/ask`, `/claim-team`, and the reaction approval flow. |
| `agent/` | The agent service: config, storage, policy tiers, tools, jobs and prompts, runner, clock, HTTP API. |
| `Dockerfile.agent` | Image for the agent service. |
| `fantasy-football-agents/` | Design plan, rules, the season log seed, and the write probe. |
| `gamedaybot/web/app.py` | Shiny dashboard: layout and reactive wiring. |
| `gamedaybot/web/stats.py` | Season arithmetic — records, streaks, head-to-head, trophies, and manager identity across seasons. |
| `gamedaybot/espn/managers.py` | Parser for the league's list of who ran which team. |
| `agent/tools/history.py` | Agent tools for past seasons: `get_rivalry` and `get_league_history`. |
| `gamedaybot/web/draft.py` | Draft-board columns, filters, and sorting. |
| `gamedaybot/web/charts.py` | Plotly figure builders and their shared styling. |
| `gamedaybot/web/theme.py` | Team palette and the stat-tile sparkline. |
| `gamedaybot/web/www/dashboard.css` | Dashboard styling. |
| `tests/` | Tests for stats, storage, player parsing, and the draft board. |
| `dev/` | Maintenance scripts. Copied into the image, so `docker compose exec` can run them. |
| `data/` | SQLite database. Mounted from the host. |
| `compose.vps.yml` | VPS override: join `edge`, publish no host ports. |
| `tunnel/config.yml.example` | Template for an off-VPS dedicated tunnel. |
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
Use `dev/backfill_season.py` to load a completed season, or
`dev/collect_players.py` for the draft board before week 1. The dashboard
picks the new data up within 30 seconds, with no restart.

**The draft board is empty.** The player pool has not been collected. Restart
the full container (it pulls the pool on startup) or run
`dev/collect_players.py`. The daily 6:15 AM job also refreshes it, including
before `START_DATE`.

**The bot's replies and the dashboard disagree on the season.** Slash commands
and scheduled posts read the ESPN API live, using `LEAGUE_YEAR`. The dashboard
shows only the seasons collected into `data/fantasy.db`. Its season dropdown
falls back to the current calendar year until a snapshot exists. Run
`dev/backfill_season.py` for the year you want.

**The `fantasy-bot` container exits with `sqlite3.OperationalError: unable to
open database file`.** Docker created the `data/` bind mount as root, but the
container runs as uid `1000`. Run `chown -R 1000:1000 data` on the host, then
restart the container.

**Cloudflare Tunnel returns a 502.** On the VPS, the shared connector cannot
reach this container. Confirm `fantasy-football-bot` is on the `edge` network
and that `/root/.cloudflared/config.yml` names that container. Off the VPS,
check `docker compose --profile tunnel logs cloudflared`. The tunnel retries
once the app listens.

**A friend's dashboard hijacks `fantasy.ethandbard.com`.** Their `tunnel/`
files are the VPS shared-tunnel credentials. Stop their `tunnel` profile and
issue them a dedicated tunnel instead.

**`docker-compose: command not found` on the VPS.** Current installs ship
Compose as a Docker plugin. Run `docker compose` as two words.

**The VPS still serves old code after a deploy.** A file copy merges into the
existing tree, so a module you deleted upstream survives on the VPS and gets
built into the next image. Compare the two sides, then repeat the update and
move `gamedaybot` aside first:

```bash
md5sum gamedaybot/web/app.py
ssh -i ~/.ssh/hetzner_vps root@<vps-host> 'md5sum /opt/fantasy-football/gamedaybot/web/app.py'
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
