Job: lineup check for week {week}, requested on demand.

Do:

1. get_my_roster and get_matchup. Identify the best starting nine by
   expected points for this week, with injury tags, byes, and locks
   respected. Read read_state for week {week} if it exists; the research may
   hold usage notes projections miss.
2. One search each only for players with a non-ACTIVE tag.
3. If the current lineup differs from the best one, make the change with
   preview_lineup and execute_lineup and explain each swap in one line.

Brief: the lineup as it stands after this run, each change with a reason,
and the disagreements section. Under 400 words.
