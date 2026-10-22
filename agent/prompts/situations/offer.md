Situation: an offer to answer, week {week} (scoring period {scoring_period}).

The offer: {trade}

This manager wants to deal, which is the best opening the week gives.
Answer it on the numbers, and when it is close, counter with the smallest
change that makes them work for both sides rather than declining flat.

Inbox from the workers since your last run:
{inbox}

Do, in order:

1. read_strategy, read_negotiations (the thread with this manager: what
   he has asked for before, what he declined, what we have offered),
   get_rules, get_my_roster, get_team_roster for his team, and
   get_agent_activity. If the offer is from the owner's own words rather
   than a pending ESPN offer, treat it as a question: evaluate and answer,
   and send nothing unless the words say to.
2. evaluate_trade with his players as get_player_ids and mine as
   give_player_ids. Read every line: my odds, both lineups per week in
   both views, the chart, the fit, the byes, the forced drops.
3. Research every player coming to me before you value him: one search
   each for his current role, health, and his team's quarterback (two
   players in one query when they share a team), even when he is tagged
   ACTIVE. A counter is not sent for a player you have not researched.
4. Decide. Accept when my odds rise and the fit holds. Decline when they
   fall. When they are close, build the counter: swap one piece for one
   from his roster at a similar price, or drop a piece, and evaluate_trade
   the counter. Send it only when it raises my odds and his lineup still
   gains in his own view, with one sentence in the brief on why he would
   take it. There is no list of players you may not ask for: his best
   player is on the table when the numbers work for him, and a counter
   that loses for him in his own view is not sent whoever is in it.
5. Act. preview_trade_response with accept true or false and execute it;
   then preview_trade and execute_trade for a counter. Read each preview's
   permission line. If accepting overflows the roster, pass the drops.
6. update_negotiation for this manager: the event, what the offer says
   about his valuations, the next action and when. append_season_log.

Brief: the verdict in the first line (accept, decline, counter, or
answer), the numbers as a short table, his angle, and the disagreements
section. Under 600 words plus steps skipped.
