Job: post-waiver adjustment for week {week}.

Waivers processed this morning. Reconcile the result against Tuesday's plan.

Do:

1. get_recent_activity (size 40) and get_my_roster. Which of my claims
   landed, which failed, and who took the players I missed on.
2. read_state for week {week}. For every failed claim whose fallback is now
   a free agent (get_free_agents, status FREEAGENT), add him with
   preview_add_drop and execute_add_drop if the plan's drop still makes
   sense. If the fallback is gone, look at get_free_agents for the same
   need and for upside stashes the claims missed; add the best one if he
   beats my worst bench player.
3. Re-set the lineup if the roster changed: preview_lineup, execute_lineup.
4. Wednesday reports. One search per player with a new or worse injury
   tag. Then the team-level check for each starter's team, tagged or not,
   two or three teams per query: a QB who missed practice, a teammate
   injury that moves targets or carries, a depth chart change. Note what
   Friday must recheck with write_state (read_state first and keep the
   other keys), and say which teams you checked.
5. append_season_log with the waiver outcome and the moves made.

Brief: claims won and lost, adds made, lineup as it stands, what to watch
Friday, and the disagreements section.
