Job: roster plan for week {week} (scoring period {scoring_period}).

The research job has already run this morning. Start with read_state,
read_research for week {week}, and read_strategy (the general manager's
season plan, target board, and attempts log) (the research file carries the in-house
analytics section; read_analytics has the full numbers). Search again for
anything the research flagged as open. Before you claim, add, or start a
player whose news the research did not check this week, run one search for
his latest news and his team's (QB, injuries around him). Where the
analytics forecast and the consensus disagree, decide which to trust and
say why. Then get_agent_activity: what became of last week's asks and
proposals, so nothing approved, declined, or expired is treated as still
open.

Do:

1. Waiver claims. Queue them in priority order with preview_add_drop
   (waiver=true for players on WAIVERS). Fallback claims may share the same
   drop; ESPN processes them in order and skips ones whose drop is gone. Only
   free agents (status FREEAGENT) can be added immediately; do those only if
   they are clearly worth a spot today rather than after Wednesday's run.
   Any drop is yours to make, a starter included, if the case is clear.
   Stream D/ST and K every week. Compare mine with the research's
   streamers for this week's matchup (opponent's offense, Vegas implied
   total, weather for a kicker). Claim the better one whenever he projects
   about two points or more above mine, dropping mine; holding a D/ST or K
   out of habit costs points. Queue a fallback for each.
2. Lineup. Set the provisional lineup for this week with preview_lineup and
   execute_lineup: highest expected points at every slot, byes and Out or
   Doubtful players on the bench, IR-eligible players into IR if it frees a
   spot. Explain any start that is not the highest projection.
3. Trades. If the research priced trades off an older week's chart, fetch
   this week's chart before you send anything:
   https://www.fantasypros.com/<year>/<month>/fantasy-football-trade-value-chart-week-{week}-<year>/
   where <year>/<month> is the publish date. If it is still not up, say so
   and price with the newest chart. Trades follow the rules' trade section
   and the strategy's buy targets and sell candidates. Send an offer only
   when it swaps a sell candidate (or a bench player of mine who would
   start for him) for a buy target at about an even chart price, outside
   that team's two or three best players, with a one-line thesis from our
   model and one sentence on why the other manager gains. Keep one to three
   open in total, counting the general manager's, each to a different
   manager, and put no player in two open offers. Do not re-ask a manager
   for a player he declined in the last two weeks. Under full autonomy
   offers are sent at once, so write the thesis in the brief. Sending none
   is right when no offer passes; say why per target.
4. Schedule this week's pre-game checks with schedule_pregame_checks.
5. append_season_log with what was queued, set, and proposed, and why.

Then update the strategy file's attempts log and target board with the
offers sent (read_strategy, then write_strategy with the rest kept).

Brief: what is queued for waivers (with fallbacks), the lineup, trades sent, the pre-game schedule, and the disagreements section.
