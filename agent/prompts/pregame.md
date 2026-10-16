Job: pre-game check, 60 minutes before the {label}.

Players of mine in games at this kickoff: {players}.

This run is short. Its only purpose is to keep an inactive or downgraded
player out of my lineup before he locks, and to use the best unlocked
alternative.

Do:

1. get_my_roster. Look at the players listed above and any starter whose
   kickoff is at or after this one.
2. Search the inactives lists of the teams those players play for, two or
   three teams per query, until every team is covered. This is required even
   when every player is ACTIVE: it catches a QB ruled inactive or a teammate
   whose absence changes my starter's role. For each player tagged anything
   but ACTIVE, use that list or one search for his latest report. Also read
   the QB changes and contingencies the Friday designations run left in
   read_state.
3. If a starter in this window is Out, Doubtful, or reported inactive:
   swap in the best unlocked bench player eligible for the slot who still
   has a game to play, preferring the one with the later kickoff if
   projections are close. Use preview_lineup and execute_lineup. Also check
   read_state's "watch" and contingency notes for a plan written earlier.
4. If a later-kickoff starter has a fresh downgrade, handle him now too;
   there may not be another check before his lock.

Brief: at most five lines. Say "no change" if nothing changed. Include
sources only for a change.
