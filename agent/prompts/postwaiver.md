Job: post-waiver adjustment for week {week}.

Waivers processed this morning. Reconcile the result against Tuesday's plan.

Do:

1. get_recent_activity (size 40) and get_my_roster. Which of my claims
   landed, which failed, and who took the players I missed on.
2. read_state for week {week}. For every failed claim whose fallback is now
   a free agent (get_free_agents, status FREEAGENT), add him with
   preview_add_drop and execute_add_drop if the plan's drop still makes
   sense. Do not chase a lesser player just to make a move.
3. Re-set the lineup if the roster changed: preview_lineup, execute_lineup.
4. Note anything for Friday: new injury tags, a role change from the
   Wednesday reports (one search per flagged player, no more).
5. append_season_log with the waiver outcome and the moves made.

Brief: claims won and lost, adds made, lineup as it stands, what to watch
Friday, and the disagreements section.
