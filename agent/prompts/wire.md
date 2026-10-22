Job: waiver wire watch, week {week}.

Other teams dropped players since the last look: {drops}

Each one sits on waivers for 24 hours from his drop, and ESPN processes
claims at 3:00 AM Eastern every day but Tuesday. A claim entered now beats
the managers who wait. The waiver order is inverse standings with no limit
on acquisitions, so a claim costs nothing but the player it drops.

This run is short. Spend at most two web searches, and only on a player
you are about to claim.

Do:

1. get_my_roster and read_strategy (the holes in my lineup and the target
   board). Name my worst bench player: the one least likely to start for
   me in the next three weeks.
2. For each dropped player, get_player: position, team, injury, season and
   recent points. Skip kickers and D/STs unless his matchup this week beats
   mine, and skip anyone hurt for the season.
3. Claim a dropped player when he would start for me, fills a hole the
   strategy names, is a target on the board, or simply has more upside
   than my worst bench player (a back one injury from the lead role, a
   receiver whose targets are rising). Before you claim, one search on his
   role and health. Use preview_add_drop with waiver=true and drop the
   worst bench player; read the preview and execute it. Claims for several
   players may share the same drop; ESPN processes them in order and skips
   one whose drop is gone, so list the best first.
4. If you claimed anyone, append_season_log with the claim and why, and
   leave a line with note_for_gm so the general manager knows the roster
   may change at 3:00 AM. A dropped player you passed on but who would
   interest a trade partner is worth a note too.

Brief: at most six lines plus the steps-skipped line. Each dropped player
with "claim" or "pass" and the reason in a few words, and any claim with its
drop and when it processes.
