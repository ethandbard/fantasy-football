"""Print whether ESPN has post-draft teams/picks/rosters yet. No secrets."""
import os
import sys

from espn_api.football import League

year = int(os.environ.get("LEAGUE_YEAR", 2026))
league = League(
    league_id=int(os.environ["LEAGUE_ID"]),
    year=year,
    espn_s2=os.environ.get("ESPN_S2"),
    swid=os.environ.get("SWID"),
)

print(f"league={league.settings.name!r} year={year}")
print(f"teams={len(league.teams)} current_week={league.current_week} "
      f"scoringPeriodId={league.scoringPeriodId}")
print("TEAM_NAMES")
for t in league.teams:
    owners = [o.get("displayName", "?") for o in (t.owners or [])]
    roster = getattr(t, "roster", None) or []
    print(f"  id={t.team_id} name={t.team_name!r} abbrev={getattr(t,'team_abbrev',None)!r} "
          f"owners={owners} roster={len(roster)}")

draft = getattr(league, "draft", None) or []
print(f"DRAFT_PICKS={len(draft)}")
for p in draft[:12]:
    team = getattr(p, "team", None)
    team_name = getattr(team, "team_name", None) if team is not None else None
    print(f"  r{getattr(p,'round_num',None)}.{getattr(p,'round_pick',None)} "
          f"{getattr(p,'playerName',None)!r} -> {team_name!r} "
          f"keeper={getattr(p,'keeper_status',None)}")
if len(draft) > 12:
    print(f"  ... {len(draft) - 12} more")

on_team = 0
if league.teams:
    sample = league.teams[0]
    roster = getattr(sample, "roster", None) or []
    print(f"SAMPLE_ROSTER team={sample.team_name!r} n={len(roster)}")
    for pl in roster[:8]:
        print(f"  {getattr(pl,'position',None)} {getattr(pl,'name',None)}")
        on_team += 1
sys.exit(0)
