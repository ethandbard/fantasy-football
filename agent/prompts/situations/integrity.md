Situation: the roster integrity sentinel found something it cannot fix
alone, week {week} (scoring period {scoring_period}).

Findings: {findings}

This run is short. Its purpose is to put the roster back in a state ESPN
accepts moves on and the lineup can score from: a drop so a healthy player
can leave the IR slot, a free agent for a slot no bench player can fill, a
kicker or D/ST when the roster has none, a spare quarterback or kicker
dropped for a player who can start. Nothing else.

Do:

1. get_my_roster and get_rules. Confirm the findings against the roster as
   it is now; the sentinel read it within the hour.
2. For each finding, the smallest move that resolves it: get_free_agents
   for the slot's position when an add is needed (only status FREEAGENT can
   be added now), the worst bench player as the drop, preview_activate for
   a player leaving IR on a full roster. Preview, read, execute.
3. If a move needs a player who is locked or a claim that cannot process
   in time, say so and leave it for the next session.
4. append_season_log with what was found and what was done.

Brief: at most six lines plus the steps-skipped line. Each finding with
what was done or why it waits.
