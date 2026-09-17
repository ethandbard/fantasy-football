Job: Friday final injury designations for week {week}.

Teams publish final designations Friday afternoon. This run makes sure
nobody ruled Out starts for me on Sunday, and that a true game-time call is
handed to the pre-game check with a plan.

Do:

1. get_my_roster. Every player with a tag other than ACTIVE gets one search
   for his Friday designation and the beat reporting behind it. Also search
   any starter whose team's Friday report you have not seen, one query for
   the whole team is fine.
2. Bench anyone Out or Doubtful; start the best healthy option from the
   bench. If a bench player is Out and IR-eligible, move him to IR only if it
   frees a spot you will use. Use preview_lineup and execute_lineup.
3. If a starter is a real game-time decision, write the contingency: who
   replaces him and by when the decision must be made (his kickoff minus 60
   minutes). The pre-game check reads this from the state file, so update it
   with write_state (read_state first and preserve the other keys).
4. If the best replacement is a free agent rather than a bench player, add
   him now (preview_add_drop, execute_add_drop) within the tiers.
5. append_season_log with the designations and any moves.

Brief: designations for my players, lineup changes, contingencies for
Sunday, and the disagreements section. Keep it under 500 words.
