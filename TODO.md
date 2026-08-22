# Dashboard work plan

Fourteen requests, grouped into four phases. Phase 1 is a prerequisite for the
records, head-to-head and upcoming-matchup work, so it goes first.

---

## Findings: what is actually wrong with the playoff weeks

Queried `data/fantasy.db` directly. The 2025 season holds 16 weeks, and the last
two are broken in two separate ways.

| Week | Rows | League total | Reading |
|---|---|---|---|
| 1–14 | 8 | 865–1064 | Correct. One scoring period each. |
| 15 | 8 | 2145.1 | Two weeks of points stamped on one week. |
| 16 | 8 | 2145.1 | The same two weeks again, byte for byte. |
| 17–18 | — | — | Never collected. |

Per-team confirmation: `Seemed like the thing to do` shows 260.8 in week 15 and
260.8 in week 16, against the same opponent both times, on a season where its
typical week is ~125.

**Root cause 1 — the duplicate.** ESPN runs the playoffs as *matchup periods*
that span two *scoring periods*. Matchup period 15 covers scoring periods 15+16;
matchup period 16 covers 17+18. `league.box_scores(week=N)` takes a scoring
period, resolves it to its matchup period, and returns that matchup period's
`totalPoints`. Ask for 15 and ask for 16 and you get the same two-week total
back both times. [collector.py:76](gamedaybot/espn/collector.py:76) writes
whatever it gets, so the round total lands on both weeks.

**Root cause 2 — the missing weeks.**
[collector.py:61](gamedaybot/espn/collector.py:61) sets the loop bound with
`last_week = len(league.settings.matchup_periods)`. For 2025 that is 16 — the
number of *matchup periods*, not scoring periods. The loop therefore asks for
scoring periods 1 through 16 and never asks for 17 or 18. There are 18 scoring
periods in the season.

**Independent check that the regular season is 14 weeks.** ESPN's own
`points_for` in `standings_snapshot` matches each team's weeks 1–14 sum to the
cent (e.g. `First Down Syndrome` 1795.68 both ways) and diverges from the 1–16
sum. So weeks 1–14 are the regular season and 15–18 are two playoff rounds of
two weeks each. `stats.regular_season_weeks()` already derives 14 correctly from
wins+losses+ties, and keeps working once weeks 17–18 arrive.

**On averaging the pair.** Averaging weeks 15 and 16 returns 260.8 — the same
doubled number — because both rows already hold the round total. What you want
is the round total *split* back into its two weeks. The real per-week split is
recoverable from the box score lineups, so we take that when we can and fall
back to an even split when we cannot. Either way each week reads on the same
scale as a regular-season week, and nothing is counted twice.

---

## Phase 1 — Data correctness

### 1.1 Verify the ESPN side before writing anything

Extend `dev/api_healthcheck.py` to dump, for the configured league:

- the full `settings.matchup_periods` map (matchup period → scoring periods)
- for a playoff week: `box.home_score` next to the sum of the non-bench
  `home_lineup` player points
- whether `Team` exposes `logo_url`, and what the values look like

This is the one thing not checkable from the local DB, and three later steps
depend on the answers. Run it against 2025 (a completed season) with
`LEAGUE_YEAR=2025`.

### 1.2 Schema

`gamedaybot/storage/db.py` — additive only, through the existing
`_ADDED_COLUMNS` mechanism so existing databases migrate on open.

Add to `weekly_scores`:

| Column | Meaning |
|---|---|
| `matchup_period` | Which round this week belongs to. 1–14 map to themselves; 15 and 16 both carry 15; 17 and 18 both carry 16. |
| `matchup_score` | The round total that actually decided the win. Equals `score` for a one-week round. |

`score` becomes the true single-week score. Two new tables:

- `teams(year, team_id, team_name, abbrev, logo_url, owner)` — for item 6.
- `schedule(year, week, matchup_period, team_id, opponent_id, is_home)` — for
  the upcoming-matchup page, since the app reads only from SQLite.

### 1.3 Collector

`gamedaybot/espn/collector.py`:

- Loop bound becomes the last **scoring** period:
  `max(max(periods) for periods in league.settings.matchup_periods.values())`.
  For 2025 that is 18. Works for any league length without a setting.
- Build a scoring-period → matchup-period lookup from the same map and write it
  to the new column.
- Per-week score: sum the starters (slot not in `BE`/`IR`) from
  `box.home_lineup` / `box.away_lineup`. If lineups are unavailable, fall back
  to `matchup_score / len(periods_in_round)` — the even split. Log which path
  was used so a silent fallback is visible.
- Keep `box.home_score` in `matchup_score`.
- New `collect_teams()` and `collect_schedule()`, called from both
  `collect_weekly_snapshot()` and `collect_historical_season()`.

### 1.4 Backfill

Re-run `python dev/backfill_season.py 2025`. The upsert is keyed on
`(year, week, team_id)`, so weeks 15–16 are corrected in place and 17–18 are
added. Verify afterwards that all 18 weeks total ~1000 each.

**Worth doing at the same time:** the database holds only 2025. The all-time
records in Phase 4 will read as a copy of the 2025 season until earlier years
exist. Backfilling 2021–2024 makes that feature real — say the word and I will
run it.

### 1.5 Make the stats layer round-aware

`gamedaybot/web/stats.py`:

- `game_log()` gains a matchup-level view: one row per (team, matchup_period)
  using `matchup_score`, so a two-week playoff round counts as one win, not two.
- `derive_records()`, `head_to_head()` and the W/L half of `trophies()` switch to
  that view. Anything about *scoring* — `consistency()`, `vs_projection()`,
  ceiling/floor, the sparklines, the score charts — stays on per-week rows.
- `rank_by_week()` keeps per-week x-positions but only credits a win at the end
  of a round, so the race chart does not show a phantom win mid-round.
- Extend `tests/test_stats.py` with a fixture that has a two-week playoff round.

### 1.6 Display the playoff weeks honestly

- Week rail on **This week**: weeks 1–14 as today, then `R1 · 15–16` and
  `R2 · 17–18`.
- Picking a playoff round renders a round view: both weeks' scores per team plus
  the round total that decided it, rather than pretending it was one game.
- Scope segment: `Playoffs` becomes weeks 15–18, which falls out of
  `_scope_bounds()` automatically once 17–18 exist.

---

## Phase 2 — Bugs and small copy changes

### 2.1 The filter-toggle cycling bug

**Diagnosed.** The scope segment is rendered *inside* `screen_league()`, and
`screen_league()` re-runs whenever `scope` changes. So: click → `input.scope`
fires → `_on_scope_input` sets `scope` → `screen_league` re-renders → a brand
new radio group with the same id is inserted → the client re-binds it and sends
its value → `input.scope` fires again.

It does not settle, because Shiny's `reactive.Value._set` short-circuits on
**identity** (`self._value is value`), not equality — and each round trip
decodes a fresh `"reg"` string object off the wire. So the echo always looks
like a change, and the loop keeps going. Clicking fast queues several of these
at once, which is the visible thrash.

Fix, both halves:

1. Hoist the scope and sort segments out of the `@render.ui` trees and define
   them once at page level, then show and hide them per screen with a CSS rule —
   the same trick [app.py:718](gamedaybot/web/app.py:718) already uses for the
   race chart. The control is then never rebuilt by the value it drives.
2. Guard the effects with a value comparison (`if input.scope() != scope.get()`)
   so any echo is a no-op regardless.

This also retires the duplicate `scope_records` input id and its workaround,
since there is now one control instead of two.

### 2.2 Drop the "THE"

[app.py:349](gamedaybot/web/app.py:349) — `f"The {winner_name} survived …"` →
`f"{winner_name} survived …"`. The winner/loser branch either side of it is
inverted-and-back-again; straighten it while there.

### 2.3 Streak trophies

- Rename `Longest Active Streak` → **Longest Win Streak**, and change what it
  measures. It currently reports the *trailing* run from `_streak()`, so a team
  that won six then lost last week shows nothing. Compute the longest run of `W`
  anywhere in the scoped range instead, and report the weeks it spanned.
- Add **Longest Losing Streak** as its mirror.

### 2.4 Links out

Add to the top bar, right of the season picker: `ethandbard.com` →
`https://ethandbard.com` and `Docs` → `https://fantasy-docs.ethandbard.com/`,
both `target="_blank" rel="noopener"`. The top bar hides its nav under 900px, so
repeat both in the footnote for mobile.

---

## Phase 3 — Visualisations

### 3.1 Rebuild the race chart

Reading the current chart against `race chart example.png`, what makes the F1
version legible and ours not:

| F1 chart | Ours today | Change |
|---|---|---|
| Diagonal transitions between positions | `shape="hv"` — long flat runs that sit on top of each other on the same gridline | `shape="linear"`; every crossing becomes a visible diagonal |
| Labels on **both** edges — grid order left, current order right | Right edge only | Add left-edge labels, so a line is identified at both ends and never has to be traced |
| No per-lap markers | A marker every week | Drop the markers, or keep them tiny and only where a rank changed |
| Thick strokes, wide row spacing | 2.4px, cramped | ~3.2px, taller plot, more row pitch |
| Every car a distinct hue | Blue/cyan/green sit close together | Re-space the palette; also widen the dash cycle so wrapped hues separate |

Plus, beyond the reference: hovering or selecting a team dims every other line to
~20% opacity. That single change does more for "tracking a player line" than
everything above put together. Wire it to the existing `team` reactive value so
clicking a team pill highlights it here too.

Also fix the dead axis space — the x-axis runs to 16 when the data stops at 14.

### 3.2 Bring back the score line plot

New `charts.score_lines()`: points per week per team, one line each, with the
league average as a dashed reference. Lives on the **League** screen under a
`Race | Scores` toggle.

Shinywidgets fixes a widget's output slot where it is defined in the script, so
this needs its own always-present `ui.div(id="scores-wrap")` block and a
visibility style, exactly like `race-wrap`.

### 3.3 Team logos as marks

Depends on 1.2/1.3 storing `logo_url`.

- Download each logo once into `data/logos/{year}-{team_id}.png`, with the ESPN
  CDN URL as fallback and a coloured monogram when a team has no custom logo
  (ESPN hands out a generic silhouette by default, which is worth suppressing).
- Race chart and score plot: place them via Plotly `layout.images` at each
  line's right-hand endpoint — the F1 chart's right-hand roster, and a second
  reason the lines get easier to tell apart. Embed as base64 data URIs so there
  is no CORS or hotlink dependency at render time; eight logos is a trivial
  payload.
- Reuse the same asset in the team rail pills, scorebug rows, standings rows,
  the team profile header, and the head-to-head matrix headers.

---

## Phase 4 — New views

### 4.1 Head-to-head matrix

`stats.head_to_head()` already returns the records and average-margin frames;
nothing renders them as a grid. Build an HTML table rather than a Plotly
heatmap — the text stays crisp, it inherits the existing CSS system, and each
cell can route to a team page through the delegated `data-set` click handler
already in place.

- **League screen:** the full matrix, cells tinted by average margin (win green
  → loss red), diagonal blanked.
- **Team screen:** the same component with that team's row and column raised and
  everything else dimmed. It can replace the current `.h2h-row` bars, or sit
  above them — I would replace, since the table says the same thing in less
  space.
- Within one season most pairs meet once, so the grid is sparse and mostly a
  single result per cell. It gets genuinely interesting **all-time**, so build
  the component to take either frame and add a `Season | All-time` toggle.

### 4.2 Records: season trophies and all-time trophies

Split the Records screen into two groups.

**This season** — the existing awards, scoped by the year picker and the scope
segment, plus the two streak trophies from 2.3.

**All time** — a new `stats.all_time_trophies()` over every season in the
database, keyed on `(year, week)` so week numbers from different years never
collide. Each card names the season it happened in, and this block ignores both
the year picker and the scope segment.

Your two, plus the ones the stored columns already support for free:

- Smallest margin of victory, league-wide *(requested)*
- Largest margin of victory, league-wide *(requested)*
- Highest and lowest single-week score
- Highest-scoring matchup, by combined points
- Most points in a season
- Best and worst season record
- Longest win streak and longest losing streak
- Best and worst week against projection
- Most dominant rivalry — the best all-time record against one opponent

Championships would be the obvious other one, but it needs the playoff bracket
read out of the final round, so it waits on Phase 1 landing and on knowing
which round-2 matchup is the final rather than the consolation. I will scope it
once weeks 17–18 are in the database.

**Caveat, again:** with only 2025 collected, every all-time card will name 2025.
Backfilling earlier seasons (1.4) is what makes this section worth having.

### 4.3 Upcoming matchup page

A fifth nav item, `Next up`, reading the new `schedule` table.

Per matchup: both teams with logos and records, season average and range, last
five results, ESPN's projected scores, the pair's head-to-head history, and each
team's rank trend. A win-probability read is possible from each team's mean and
standard deviation — worth including, but labelled plainly as a rough model
rather than dressed up as a real number.

**Timing caveat you should know before I build it.** `config.env` pins
`LEAGUE_YEAR=2025` for testing and the 2025 season is finished, so there is no
upcoming matchup to show today. The screen will render its empty state and
nothing else until `LEAGUE_YEAR` moves to 2026 and the season starts. I will
build it with a real empty state and verify the layout against synthetic
fixtures, but it cannot be verified end-to-end against live data until then.

---

## Suggested order

| Phase | Why here |
|---|---|
| 1 | Everything downstream reads this data. Doing it later means redoing Phase 4. |
| 2 | Small, independent, and 2.1 is the one users hit constantly. |
| 3 | The race chart depends on 1.5 for correct playoff ranks and 1.3 for logos. |
| 4 | Depends on the round-aware stats layer and on the schedule and teams tables. |

Phase 2 could jump ahead of Phase 1 if you want the toggle bug gone today —
none of it touches the data layer.
