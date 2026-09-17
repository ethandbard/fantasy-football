"""
Prove ESPN write access for this league without changing anything.

Posts a lineup transaction that moves one starter from its current slot to
the same slot. ESPN rejects that with TRAN_ROSTER_SAME_SLOT (HTTP 409), and
that rejection is the success signal: the cookies authenticated and the
write host accepted the request shape. A 401 means the cookies are dead.
Nothing on the roster changes either way.

Run from the project root with the league cookies in the environment:

    set -a; . ./config.env; set +a
    .venv-dashboard/Scripts/python fantasy-football-agents/tools/espn_write_probe.py

Needs LEAGUE_ID, LEAGUE_YEAR, ESPN_S2, SWID. TEAM_ID defaults to 11.
"""
import json
import os
import sys

import requests
from espn_api.football import League

WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"
BENCH_SLOT = 20
IR_SLOT = 21

HEADERS = {
    "Content-Type": "application/json",
    "X-Fantasy-Source": "kona",
    "X-Fantasy-Platform": "kona-PROD",
}


def main():
    league_id = int(os.environ["LEAGUE_ID"])
    year = int(os.environ.get("LEAGUE_YEAR", "2026"))
    team_id = int(os.environ.get("TEAM_ID", "11"))
    espn_s2 = os.environ["ESPN_S2"]
    swid = os.environ["SWID"]
    if not swid.startswith("{"):
        swid = "{" + swid + "}"

    league = League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)
    raw = league.espn_request.league_get(params={"view": "mRoster"})
    period = raw["scoringPeriodId"]
    team = next(t for t in raw["teams"] if t["id"] == team_id)
    starter = next(
        e for e in team["roster"]["entries"]
        if e["lineupSlotId"] not in (BENCH_SLOT, IR_SLOT)
    )
    name = starter["playerPoolEntry"]["player"]["fullName"]
    slot = starter["lineupSlotId"]

    payload = {
        "type": "ROSTER",
        "teamId": team_id,
        "memberId": swid,
        "executionType": "EXECUTE",
        "isLeagueManager": False,
        "isActingAsTeamOwner": False,
        "scoringPeriodId": period,
        "items": [{
            "playerId": starter["playerId"],
            "type": "LINEUP",
            "fromLineupSlotId": slot,
            "toLineupSlotId": slot,
        }],
    }
    url = f"{WRITE_HOST}/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}/transactions/"
    print(f"Probing with {name} (slot {slot} -> {slot}), scoring period {period}")
    r = requests.post(url, json=payload, headers=HEADERS,
                      cookies={"espn_s2": espn_s2, "SWID": swid}, timeout=20)
    try:
        body = r.json()
    except ValueError:
        body = r.text
    print(f"HTTP {r.status_code}")
    print(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else body)

    text = json.dumps(body)
    if "AUTH_UNAUTHORIZED_FOR_TEAM" in text:
        print(f"\nRESULT: the cookies authenticate, but not as the owner of team {team_id}. "
              "Use ESPN_S2 and SWID from the browser of the account that owns this team.")
        return 3
    if r.status_code == 401 or "AUTH_MISSING_CREDENTIALS" in text:
        print("\nRESULT: cookies rejected. Refresh ESPN_S2 and SWID from the browser.")
        return 2
    if "TRAN_ROSTER_SAME_SLOT" in text:
        print("\nRESULT: write access confirmed (ESPN parsed and rejected the no-op as expected).")
        return 0
    if "TRAN_LINEUP_LOCKED" in text:
        print("\nRESULT: write access confirmed; that player is locked right now. Pick another or rerun Tuesday.")
        return 0
    print("\nRESULT: unexpected response. Read the body above before trusting the endpoint.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
