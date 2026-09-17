# Dashboard work plan

Status of the site plans, updated 2026-09-17. The original fourteen-request
plan is done; its phase write-ups live in git history (`git log -- TODO.md`).
This file tracks the expansion plan agreed on 2026-09-17, now that the agents
collect and write more than the Tuesday snapshot.

## Expansion plan (2026-09-17)

The biggest lever was one missing table: every previous chart was a score per
team per week. `lineup_scores` stores every rostered player in every played
week (slot, projection, points, eligibility), which is what bench regrets,
position contribution, and player leaderboards need. `league_settings` stores
the playoff team count, regular-season length, and starting slot counts that
the derivations used to guess at. `activity` keeps the add/drop/waiver feed.

### Data

- [x] `lineup_scores`, collected with every box score; weeks with scores but
      no lineups are re-fetched once, so a backfilled season repairs itself.
- [x] `league_settings` per season, from the daily league-state job.
- [x] `activity` ledger (adds, drops, waiver claims), daily, insert-only.
- [x] `players.total_points`: season-to-date actual points from the pool.
- [x] Backfill 2025 and 2024 lineups with `dev/backfill_season.py`.

### Visualizations

- [x] Luck: all-play record and expected wins, points for vs against
      quadrant on League.
- [x] Playoff odds: Monte Carlo over the remaining schedule, on League.
- [x] Projection accuracy per team and by week, on League.
- [x] Bench regrets: optimal lineup vs started, per team-week, with the
      losses that were left on the bench, on Teams.
- [x] Position contribution stacked bars, on Teams.
- [x] Draft return: pick number vs points scored, steals and busts, on Draft.

### More information

- [x] Players page: leaderboards by position, starts, boom and bust rates.
- [x] Agent prose: matchup preview on Next up (Wednesday), power rankings on
      League (Tuesday), alongside the recap on This week.
- [x] Manager's log on the agent's team page: executed moves with reasons.
- [x] Playoff bracket and the championships trophy (bracket logic unblocks
      the 🏆 all-time card).
- [x] Moves ledger beside trades on League.

### Chat

- [x] Data chat page: Shiny chat component, chatlas over the SQLite tables,
      Haiku by default, gated by a shared league passphrase with per-viewer
      and league-wide daily caps. Advice questions are pointed at `/ask`.
- [ ] Later: hand advice questions to the analyst job through the agent's
      ask route, with queue position shown.

## Done earlier

- Data correctness, playoff rounds, backfill of 2024 and 2025 (2021–2023 do
  not exist; the league id starts in 2024).
- Race and score charts, head-to-head matrix, two-part record book, Next up
  page with projections, team logos, draft board and draft-day analysis,
  trade ledger, league recap on This week, per-opponent head-to-head list and
  week-by-week bars on Teams.
- Deployed 2026-09-15 and 2026-09-17.
