Situation: after the games, Sunday night of week {week} (scoring period {scoring_period}).

The Sunday games are done and Monday night's is still to play, so ESPN
has not rolled the week: get_week_results for week {week} shows the
scores so far. This is the trade window. Every manager has just watched
his lineup, the one who lost can see his hole, and the one whose bench
player broke out has not reset his price. Next week's lineup cannot be
set until ESPN rolls the period on Tuesday; the weekly session does that.

Inbox from the workers since your last run:
{inbox}

Do, in order:

1. Catch up. read_strategy, read_negotiations, get_rules, get_my_roster,
   get_agent_activity, get_pending_transactions.
2. The week's results. get_week_results for week {week} for every team
   (list_teams for the ids): who won and lost, which starters boomed on a
   long touchdown, which busted, and which bench players broke out. Then
   market_map and playoff_odds with no changes.
3. Sell what the market just repriced. One of mine who scored big on usage
   the model does not expect to repeat is worth more tonight than it will
   be Tuesday. Buy what the market just marked down: a target who had a
   quiet week on healthy usage. One search per player you are about to
   put in an offer, for the injury news that would make the offer a
   vulture; skip anyone hurt today.
4. Approach the managers with a visible hole. For each, build the one
   offer that fills the slot he lost points at this week with a player he
   would start, in return for the target in his surplus. evaluate_trade
   it; send what passes with preview_trade and execute_trade, and record
   the thread with update_negotiation. Follow up on every open thread as
   the trade desk would.
5. The wire. Players dropped tonight sit on waivers until Tuesday's 3:00 AM
   run is skipped; claims process Wednesday. Queue a claim for any drop
   worth more than my worst bench player with preview_add_drop
   (waiver=true), and note for Tuesday the free agents whose breakout the
   league has not priced yet.
6. update_negotiation for every thread touched, write_strategy if the
   Bets or Watch sections changed, and append_season_log.

Brief: offers first with their numbers, then what the week's results
changed about the position, then what Tuesday should do. Under 900 words
plus the disagreements and steps-skipped sections.
