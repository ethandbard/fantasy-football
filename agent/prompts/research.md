Job: week review and research for week {week} (scoring period {scoring_period}).

This is the one expensive run of the week. Its output feeds the roster plan
that follows, so be thorough and structured, not chatty.

Do, in order:

0. read_analytics for week {week}. The in-house data analyst ran just before
   you and built its own forecasts from usage data and Vegas lines. Treat its
   flags (role growing, buy-low, sell-high, model above or below ESPN) as
   leads: check the biggest ones against news in your searches and say in
   the report which you believe. Its section in the research file is kept
   when you write_research; do not copy its tables.
1. Review last week: get_week_results for the week just played. Note who
   beat or missed projection and why, per starter. Read the season log tail,
   read_strategy (the general manager's season plan and trade target
   board), and get_agent_activity for what was tried and what became of it.
2. League state: list_teams, get_standings, get_recent_activity (size 40),
   get_pending_transactions. Note the waiver order and who is chasing what.
3. My roster: get_my_roster. For each player, a one-line verdict: hold,
   start, sit, watch, drop candidate. Injury tags get a search each. Then
   check every starter's team, tagged or not, two or three teams per query:
   QB health, injuries ahead of or beside my player, depth chart and snap
   changes. Say which teams you checked.
4. Free agents: get_free_agents for RB, WR, TE, QB, D/ST, K (size 12 each,
   sort last_week then projected). Identify the real adds: players who would
   start for me, and upside stashes (a back one injury from the lead role, a
   receiver whose targets rose two weeks running) worth more than my worst
   bench player, with the drop that makes room. Cross-check the top
   candidates against usage and news. Aim for at least two adds; a week with
   none needs a line on why my worst bench player beats every one of them.
5. Trade market, run on our own numbers. First name my two or three
   weakest starting slots and how many points a week each trails the
   league's typical starter there. Then, for every other team
   (get_team_roster), one line on its lineup's weak slots and the positions
   it is deep at. From the analytics, list buy targets (players on other
   rosters, outside each team's two or three best, whom the model rates
   well above their chart value, especially at my holes) and my sell
   candidates (players the chart rates above what the model expects, and
   bench players who would start elsewhere but not for me). Check the story
   behind each: is the model's edge a real role change or a thin sample?
   Pair them into one to three offers that swap a sell candidate for a buy
   target at about an even chart price, each with a one-line thesis and a
   sentence on why the other manager gains. Follow the rules' trade
   section. Price from a public rest-of-season trade value chart. Fetch this week's
   FantasyPros chart directly first:
   https://www.fantasypros.com/<year>/<month>/fantasy-football-trade-value-chart-week-{week}-<year>/
   where <year>/<month> is the publish date (this month first; if that is a
   404 and the month just turned, last month). If the week {week} page is
   not up yet, use the newest chart one search finds, say which week it
   is, and add a "watch" entry so the plan reprices with this week's chart.
6. Streaming: the best D/ST and K available for this week's matchups,
   ranked against mine on the opponent's offense, the Vegas implied total,
   and (for kickers) dome or weather. Say plainly whether each beats mine
   by two projected points or more; the plan claims him if so.

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
