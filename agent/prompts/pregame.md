Job: pre-game check, 60 minutes before the {label}.

Players of mine in games at this kickoff: {players}.

This run is short. Its only purpose is to keep an inactive or downgraded
player out of my lineup before he locks, and to use the best unlocked
alternative.

Do:

1. get_my_roster, then read_state for week {week}. Look at the players
   listed above and any starter whose kickoff is at or after this one. From
   the state, take the "watch" entries, any contingency or QB change the
   Friday designations run left, and "owner_overrides".
2. Search the inactives lists of the teams those players play for, two or
   three teams per query, until every team is covered. Inactives post about
   90 minutes before kickoff, so they are out by now. This is required even
   when every player is ACTIVE: it catches a QB ruled inactive or a teammate
   whose absence changes my starter's role. For each player tagged anything
   but ACTIVE, use that list or one search for his latest report.
3. If a starter in this window is Out, Doubtful, or reported inactive:
   swap in the best unlocked bench player eligible for the slot who still
   has a game to play, preferring the one with the later kickoff if
   projections are close. Use preview_lineup and execute_lineup. Follow a
   contingency written earlier if it fits. Do not undo an owner override
   unless the condition its note names has happened.
4. If no unlocked bench player can fill the slot, or the best one projects
   far below a free agent, add a free agent instead. get_free_agents for
   the slot's position: only status FREEAGENT can be added before the lock
   (a waiver claim would come too late), and his game must kick off at or
   after this one. Drop the player least likely to help me after this
   week: usually the inactive player if he is out long-term, otherwise my
   worst bench player. Use preview_add_drop and execute_add_drop, then
   start him with preview_lineup and execute_lineup. A dead slot scores
   zero; any healthy starter beats it.
5. If a later-kickoff starter has a fresh downgrade, handle him now too;
   there may not be another check before his lock.

Brief: at most five lines plus the steps-skipped line. Say "no change" if
nothing changed. Name the teams whose inactives you checked. Include sources
only for a change.
