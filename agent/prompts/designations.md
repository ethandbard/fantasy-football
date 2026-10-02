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
   bench. If a bench player is IR-eligible and the IR slot is open, move him
   to IR; the slot costs nothing and the bench spot it frees is worth having.
   Use preview_lineup and execute_lineup.
3. If a starter is a real game-time decision, write the contingency: who
   replaces him and by when the decision must be made (his kickoff minus 60
   minutes). The pre-game check reads this from the state file, so update it
   with write_state (read_state first and preserve the other keys).
4. If the best replacement is a free agent rather than a bench player, add
   him now (preview_add_drop, execute_add_drop).
5. If the roster has an open bench spot, fill it with the best stash in
   get_free_agents: a backup one injury from a starting role, or a player
   whose snaps or targets are climbing. Adding him before Sunday's games
   beats fighting for him on waivers after a breakout. Skip it only if no
   free agent is worth more than the empty spot.
6. append_season_log with the designations and any moves.

Brief: designations for my players, lineup changes, contingencies for
Sunday, and the disagreements section. Keep it under 500 words.
