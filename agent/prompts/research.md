Job: week review and research for week {week} (scoring period {scoring_period}).

This is the one expensive run of the week. Its output feeds the roster plan
that follows, so be thorough and structured, not chatty.

Do, in order:

1. Review last week: get_week_results for the week just played. Note who
   beat or missed projection and why, per starter. Read the season log tail
   for context on what was tried.
2. League state: list_teams, get_standings, get_recent_activity (size 40),
   get_pending_transactions. Note the waiver order and who is chasing what.
3. My roster: get_my_roster. For each player, a one-line verdict: hold,
   start, sit, watch, drop candidate. Injury tags get a search each.
4. Free agents: get_free_agents for RB, WR, TE, QB, D/ST, K (size 12 each,
   sort last_week then projected). Identify the real adds: players who would
   start for me or are worth a bench spot, with the drop that makes room.
   Cross-check the top candidates against usage and news.
5. Trade market: for every other team (get_team_roster), one line on what
   they need and what they have in surplus. Name two or three realistic
   targets and what I could offer, priced from a public rest-of-season
   trade value chart. Fetch this week's FantasyPros chart directly first:
   https://www.fantasypros.com/<year>/<month>/fantasy-football-trade-value-chart-week-{week}-<year>/
   where <year>/<month> is the publish date (this month first; if that is a
   404 and the month just turned, last month). If the week {week} page is
   not up yet, use the newest chart one search finds and say which week it
   is.
6. Streaming: the best D/ST and K available for this week's matchups.

Then write two files and one brief:

- write_research(week={week}, markdown): the full report with sections for
  each step above and a Sources list of URLs.
- write_state(week={week}, json_text) with keys: "my_players" (list of
  {{name, player_id, verdict, note, injury}}), "adds" (ordered list of
  {{name, player_id, position, status, drop_name, drop_player_id, why}}),
  "trade_targets" (list of {{team_id, team, get, give, why}}), "streamers"
  ({{"dst": [...], "k": [...]}}), "owners_needs" ({{team_id: note}}), and
  "watch" (things to re-check Friday and before kickoff).
- append_season_log with a short dated summary.

The brief you return is a digest of the report, not the report. Make no
roster writes in this job.
