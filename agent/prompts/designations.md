Job: Friday final injury designations for week {week}.

Teams publish final designations Friday afternoon. This run makes sure
nobody ruled Out starts for me on Sunday, that a starter whose role changed
because of a teammate's injury is judged on his new role, and that a true
game-time call is handed to the pre-game check with a plan.

A starter's ACTIVE tag says nothing about his quarterback or the teammates
around him. A starter can be fully healthy and still lose value because his
QB was ruled out, or gain it because the back ahead of him was. So the team
check in step 2 is required for every starter, tagged or not.

You have {search_cap} web searches (WebFetch counts as one). Spend them in
the order below and save one for the handcuff check in step 7.

Do:

1. get_my_roster. List my starters' NFL teams, plus the team of every
   handcuff or backup on my bench (a player whose value depends on the
   starter ahead of him getting hurt). That list is the team-check list.
2. Team check, mandatory. First run one search for this week's league-wide
   Friday final injury report roundup, which usually covers every team.
   Then, for each team on the list the roundup did not cover, search that
   team's Friday report; put two or three teams in one query where you can.
   Every team on the list must end this step with its Friday report seen.
   From each report, note:
   - the starting QB's status, and who starts if he is Out or Doubtful;
   - any Out or Doubtful player in my starter's position group or target
     share (the RB1, the WR1, the TE), because that changes my starter's
     role;
   - the status of the starter ahead of each of my handcuffs.
   Say in the brief which teams you checked and the source for each.
3. Every one of my players with a tag other than ACTIVE gets his Friday
   designation and the beat reporting behind it. Use the step 2 report when
   it already gives both; search only for what it left open.
4. Set the lineup. Bench anyone Out or Doubtful; start the best healthy
   option from the bench. Re-rank starters whose role changed in step 2: a
   pass catcher with a backup QB loses value, and a handcuff whose starter is
   Out becomes a starter candidate in this step, not a stash. If a bench
   player is IR-eligible and the IR slot is open, move him to IR; the slot
   costs nothing and the bench spot it frees is worth having. Use
   preview_lineup and execute_lineup.
5. If a starter is a real game-time decision, write the contingency: who
   replaces him and by when the decision must be made (his kickoff minus 60
   minutes). The pre-game check reads this from the state file, so update it
   with write_state (read_state first and preserve the other keys). Record
   any QB change from step 2 there too, so the pre-game check knows it.
6. If the best replacement is a free agent rather than a bench player, add
   him now (preview_add_drop, execute_add_drop).
7. If the roster has an open bench spot, fill it with the best stash in
   get_free_agents: a backup one injury from a starting role, or a player
   whose snaps or targets are climbing. Adding him before Sunday's games
   beats fighting for him on waivers after a breakout. Skip it only if no
   free agent is worth more than the empty spot. Before you add or keep a
   handcuff, check this week's status of the starter ahead of him, from step
   2 or with one search if his team was not on the list. If that starter is
   Out or Doubtful, the handcuff is a starter this week: go back to step 4
   and rank him for the lineup.
8. append_season_log with the designations, the team-check findings that
   changed a role, and any moves.

Brief: designations for my players, the teams checked and any QB or role
changes found, lineup changes, contingencies for Sunday, and the
disagreements section. Keep it under 500 words.
