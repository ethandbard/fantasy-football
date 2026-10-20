"""
The waiver wire watch: which players other teams dropped since the last look.

A dropped player sits on waivers for 24 hours and ESPN processes claims at
3:00 AM Eastern every day but Tuesday, so a nightly look is enough to claim
anyone worth having before another team does. The check itself is a plain
ESPN read; the scheduler starts a model run only when there is a new drop.
"""


def new_drops(rows, my_team_id, since=None):
    """Drops by other teams after `since` (ISO time), oldest first.

    rows are EspnContext.recent_activity rows. Returns them unchanged, so
    the caller can describe each one.
    """
    out = []
    for r in rows:
        if "DROP" not in str(r.get("action", "")).upper():
            continue
        if r.get("team_id") is not None and int(r["team_id"]) == int(my_team_id):
            continue
        if since and (r.get("when") or "") <= since:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r.get("when") or "")


def describe(drops):
    return "; ".join(f"{r['player']} ({r.get('position') or '?'}, player_id {r.get('player_id')}) "
                     f"dropped by {r['team']} at {r['when']} UTC" for r in drops)
