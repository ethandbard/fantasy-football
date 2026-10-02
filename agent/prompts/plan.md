Job: roster plan for week {week} (scoring period {scoring_period}).

The research job has already run this morning. Start with read_state and
read_research for week {week} (the research file carries the in-house
analytics section; read_analytics has the full numbers); only search again
for something the research flagged as open. Where the analytics forecast and
the consensus disagree, decide which to trust and say why. Then get_agent_activity: what became of last week's asks
and proposals, so nothing approved, declined, or expired is treated as
still open.

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
3. Trades. If the research or the in-house analytics names a target worth
   pursuing now, build the offer with preview_trade and execute_trade. Under
   full autonomy it is sent at once, so send only offers you would be glad
   to see accepted, and write the case in the brief for the owner. Do not
   re-send an offer a manager already declined this week.
4. Schedule this week's pre-game checks with schedule_pregame_checks.
5. append_season_log with what was queued, set, and proposed, and why.

Brief: what is queued for waivers (with fallbacks), the lineup, trades sent, the pre-game schedule, and the disagreements section.
