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
2. Lineup. Set the provisional lineup for this week with preview_lineup and
   execute_lineup: highest expected points at every slot, byes and Out or
   Doubtful players on the bench, IR-eligible players into IR if it frees a
   spot. Explain any start that is not the highest projection.
3. Trades. If the research priced trades off an older week's chart, fetch
   this week's chart before you send anything:
   https://www.fantasypros.com/<year>/<month>/fantasy-football-trade-value-chart-week-{week}-<year>/
   where <year>/<month> is the publish date. If it is still not up, say so
   and price with the newest chart. Go after the targets the research and
   the strategy name: build an offer for each with preview_trade and
   execute_trade, to different managers, so two to four offers are open
   when you finish (count the general manager's still-open ones). Build
   each one so the other manager has a reason to say yes: give him what he
   is short at, and a visible edge on the chart when my lineup gain is
   large. Two-for-one consolidations of my depth are welcome. Under full
   autonomy offers are sent at once, so send only offers you would be glad
   to see accepted, and write the case in the brief. Put no player in two
   open offers. Do not re-send an offer a manager already declined this
   week; an improved one is fine. If you send nothing, say per target why
   not.
4. Schedule this week's pre-game checks with schedule_pregame_checks.
5. append_season_log with what was queued, set, and proposed, and why.

Then update the strategy file's attempts log and target board with the
offers sent (read_strategy, then write_strategy with the rest kept).

Brief: what is queued for waivers (with fallbacks), the lineup, trades sent, the pre-game schedule, and the disagreements section.
