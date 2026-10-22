Situation: the trade desk, week {week} (scoring period {scoring_period}).
{reason}

Tuesday's offers have had time for an answer and the first practice
reports are in. This session moves the threads: it follows up, revises,
closes, and opens, and it takes any free agent whose role grew since
Tuesday.

Inbox from the workers since your last run:
{inbox}

Do, in order:

1. Catch up. read_strategy, read_negotiations, get_rules, get_my_roster,
   get_agent_activity, get_pending_transactions. Which offers were
   answered, which are still open and how old, and what each answer says
   about how that manager values players.
2. The market. market_map and playoff_odds with no changes. What changed
   since Tuesday: an injury that opened a hole on another roster, a
   manager who lost a starter, a player whose role grew.
3. Follow up every open thread. An offer open more than two days with no
   answer: withdraw_trade it, and send a revised version or close the
   thread with the reason. A decline: change the piece he objected to and
   evaluate_trade the new version; send it when the numbers hold for both
   sides, otherwise record what you learned and the condition to reopen.
4. Open what the week calls for. One or two targeted searches per target
   for health, role, and quarterback, or a scout per team when there are
   several. evaluate_trade each candidate; send what passes with
   preview_trade and execute_trade. Put no player in two open offers.
5. The wire. get_free_agents at the positions where a free agent beats my
   worst bench player on upside: a back one injury from the lead role, a
   receiver whose targets rose two weeks running, this week's better D/ST or
   K stream. Add him with preview_add_drop and execute_add_drop.
6. If the roster changed, re-set the lineup with preview_lineup and
   execute_lineup.
7. update_negotiation for every thread you touched, read_strategy then
   write_strategy if the Bets or Watch sections changed, and
   append_season_log.

Brief: the threads first (sent, revised, withdrawn, closed, each with its
odds delta and his side's gain in his own view), then the adds, then what
Sunday night should look at. Under 900 words plus the disagreements and
steps-skipped sections.
