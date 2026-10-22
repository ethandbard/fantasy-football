Job: lineup check for week {week}.

Why this run: {reason} {findings}

Do:

1. get_my_roster and get_matchup. Identify the best starting nine by
   expected points for this week, with injury tags, byes, and locks
   respected. Read read_state for week {week}; the research may hold usage
   notes projections miss, and "owner_overrides" holds lineup calls the
   owner made, which stand unless the condition in their note has happened.
2. One search each for players with a non-ACTIVE tag. Then one search for
   this week's league-wide injury news, plus a search per two or three of my
   starters' teams it did not cover, for a QB who is Out or Doubtful or a
   teammate injury that changes a starter's role. An ACTIVE tag does not
   cover either. Skip this team check before Wednesday's first practice
   report, when there is nothing new to find.
3. If the current lineup differs from the best one, make the change with
   preview_lineup and execute_lineup and explain each swap in one line.
4. If a slot cannot be filled from the bench, or a starter's news changes
   what the roster needs (a long injury, a lost role), leave a line for the
   general manager with note_for_gm.

Brief: the lineup as it stands after this run, each change with a reason,
and the disagreements section. Under 400 words.
