Situation: the weekly session, Tuesday of week {week} (scoring period {scoring_period}).

ESPN rolled the week this morning. Waivers process tomorrow at 3:00 AM
Eastern, so the claims queued now land before anyone can add by hand. The
data analyst built this week's model just before you; the scorecard and the
dashboard prose are up. This is the one long session of the week: it
reviews, scouts, claims, sets the lineup, trades, and writes the memory
the other runs work from.

Inbox from the workers since your last run:
{inbox}

Do, in order:

1. Catch up. read_strategy, read_negotiations, read_scorecard, get_rules,
   get_my_roster, get_agent_activity, get_pending_transactions. From the
   scorecard, note what the model got right and wrong last week and what
   that changes about which forecasts to trust.
2. Review the week. get_week_results for week {played_week}: who beat or
   missed projection and why, per starter. Then read_analytics (summary
   and all) for this week's forecasts, flags, and divergences from ESPN.
3. The position. get_standings and playoff_odds with no changes: my odds,
   who I am chasing, and how many wins the fourth spot needs. Then
   market_map: every team's weak slots, surplus, byes ahead, and the thread
   with us. Name my two or three weakest slots and the gap at each.
4. Scout. Send scouts in parallel: one per NFL team that starts a player of
   mine (QB status, injuries beside my player, role changes, Wednesday's
   first report), one for the free agents at each position I am thin at
   (get_free_agents sorted by last_week and projected, cross-checked with
   the analytics waiver table), and one for each buy target you are about
   to price. Read their notes. Say in the brief which teams were checked.
5. Claims. Queue waiver claims in priority order with preview_add_drop
   (waiver=true for players on WAIVERS), fallbacks sharing the same drop.
   Score each add with playoff_odds when it is close. Free agents (status
   FREEAGENT) can be added now. Stream D/ST and K: claim the better one for
   this week's matchup when he projects about two points over mine. Any drop
   is yours to make, a starter included, when the case is clear.
6. Lineup. preview_lineup and execute_lineup: highest expected points at
   every slot, byes and Out or Doubtful players benched, an IR-eligible
   player into IR when it frees a spot. Explain any start that is not the
   highest projection.
7. Trades. From the holes in step 3 and the surplus in market_map, build
   the candidate list: for each of my weak slots, the players on other
   rosters who would start there, matched against what that manager lacks
   and what I can spare. evaluate_trade every candidate. Send the ones the
   numbers support (my odds up, his lineup better in his own view or a hole
   he feels filled), with preview_trade and execute_trade. Follow up on
   every open thread: withdraw and revise what has sat unanswered for days,
   answer a decline with a changed piece, close what is dead. Put no player
   in two open offers, and check that any combination of open offers being
   accepted leaves a legal lineup with a K and a D/ST.
8. schedule_pregame_checks for this week's kickoffs.
9. Write the memory:
   - write_research(week={week}, markdown): the review, the scouting notes
     with sources, the free-agent board, the trade candidates with their
     evaluations, and the streamers.
   - write_state(week={week}, json_text) with keys "my_players" (list of
     {{name, player_id, verdict, note, injury}}), "adds" (ordered list of
     {{name, player_id, position, status, drop_name, drop_player_id, why}}),
     "streamers" ({{"dst": [...], "k": [...]}}), "owners_needs"
     ({{team_id: note}}), and "watch" (what Friday and the pre-game checks
     must re-check). The workers read this file.
   - update_negotiation for every manager you approached, answered, or
     decided to leave alone, with the next action and when.
   - write_strategy with sections Outlook (record, odds, stance), Holes,
     Bets (every position you have taken and the number behind it), Watch,
     and Owner (the standing instructions from get_rules). Carry forward
     what still holds.
   - append_season_log with the claims, the lineup, the offers, and the
     stance, dated.

Brief: the moves first, then the position, the threads, and what Thursday
should do. Under 1500 words plus the disagreements and steps-skipped
sections.
