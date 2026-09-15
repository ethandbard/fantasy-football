# Dashboard work plan

Status of the original fourteen-request plan, updated 2026-08-30. The full
phase write-ups live in git history (`git log -- TODO.md`); this file now
tracks only what is left.

## Done

- **Phase 1 — data correctness.** Playoff weeks split back into real weekly
  scores (`matchup_period` / `matchup_score` columns), collector loops over
  scoring periods not matchup periods, weeks 17–18 collected, stats layer is
  round-aware, playoff rounds render as rounds.
- **Phase 2 — bugs and copy.** Scope-toggle feedback loop fixed, "The"
  dropped from the headline, streak trophies measure the longest run anywhere
  in range (plus the losing-streak mirror), outbound links added.
- **Phase 3.1 / 3.2 — charts.** Race chart rebuilt (linear transitions,
  hover dimming, both-edge labels), score line plot back under a
  `Race | Scores` toggle.
- **Phase 4.1 / 4.2 — head-to-head matrix and the two-part record book.**
  Season trophies and all-time trophies, with the all-time half keyed on
  (year, week).
- **Backfill.** 2024 is in the database alongside 2025. 2021–2023 do not
  exist — ESPN says the league ID starts in 2024, so two seasons is the whole
  history.
- **Phase 4.3 — Next up page.** Sixth nav item reading the `schedule` table,
  which now carries ESPN's projected scores (collected with the daily
  league-state job, so they refresh until kickoff). Cards show records,
  averages, last-five form, projections, all-time head-to-head, and a
  win-probability bar labelled as the rough read it is. Renders a real empty
  state for a played-out season. Not yet verified against live in-season
  data — the 2026 season starts 2026-09-04.
- **Phase 3.3 — team logos.** Downloaded once per URL into the `team_logos`
  table (ESPN's mystique uploads need the league cookies; cookies are only
  ever sent to ESPN hosts), served from `data/logos/` with content-hashed
  filenames, coloured monogram for teams on ESPN's default silhouette.
  Rendered in scorebugs, standings, team pills, the profile header, the
  head-to-head grid, the Next up cards, and at each line's right endpoint on
  both charts.

## Remaining

### Championships trophy

Weeks 17–18 are now in the database, so this is unblocked. Needs bracket
logic to tell the final from the consolation matchup in the last round before
a 🏆 all-time card can name champions.

### Deploy

Deployed 2026-09-15, along with the unplayed-week fix: the Tuesday snapshot
had been storing ESPN's *upcoming* week as zeros (a 0-0 week 1 on Sept 8, an
all-zero week 2 on Sept 15) because ESPN advances its current week before
the 6:00 AM job. The collector now stores only played weeks and purges a
stale pre-kickoff snapshot; the season gate counts scoring periods, not
matchup periods, so weeks 16–18 get collected this year.
