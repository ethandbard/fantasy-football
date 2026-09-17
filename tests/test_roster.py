"""
Roster parsing from ESPN's raw mRoster shape, kickoff derivation from the pro
schedule, and the lineup legality check the write tools run before a POST.
"""
from datetime import datetime, timezone

import gamedaybot.espn.roster as roster


def _entry(player_id, name, slot, pos_id, eligible, pro_team=8, injury="ACTIVE",
           locked=False, proj=12.5, actual=None):
    stats = [
        {"seasonId": 2026, "scoringPeriodId": 2, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": proj},
        {"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 0, "statSplitTypeId": 0,
         "appliedTotal": 40.0, "appliedAverage": 20.0},
    ]
    if actual is not None:
        stats.append({"seasonId": 2026, "scoringPeriodId": 2, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": actual})
    return {
        "playerId": player_id,
        "lineupSlotId": slot,
        "injuryStatus": "NORMAL",
        "playerPoolEntry": {
            "id": player_id,
            "lineupLocked": locked,
            "rosterLocked": False,
            "tradeLocked": False,
            "player": {
                "id": player_id, "fullName": name, "defaultPositionId": pos_id,
                "eligibleSlots": eligible, "proTeamId": pro_team,
                "injuryStatus": injury, "injured": injury not in ("ACTIVE", "QUESTIONABLE"),
                "droppable": True, "ownership": {"percentOwned": 99.5}, "stats": stats,
            },
        },
    }


# Detroit (8) hosts Buffalo (2) Thursday night; Kansas City (12) has a bye.
SCHEDULE = {
    8: {"2": [{"awayProTeamId": 2, "homeProTeamId": 8, "date": 1789689300000}]},
    2: {"2": [{"awayProTeamId": 2, "homeProTeamId": 8, "date": 1789689300000}]},
    12: {"3": [{"awayProTeamId": 12, "homeProTeamId": 1, "date": 1790000000000}]},
}


def test_kickoffs_for_period_maps_both_teams_and_skips_byes():
    k = roster.kickoffs_for_period(SCHEDULE, 2)
    assert set(k) == {8, 2}
    assert k[8][0] == 2 and k[2][0] == 8
    assert k[8][1] == datetime.fromtimestamp(1789689300, tz=timezone.utc)


def test_parse_entry_carries_slot_lock_projection_and_kickoff():
    kick = roster.kickoffs_for_period(SCHEDULE, 2)
    e = roster.parse_entry(_entry(1, "Jahmyr Gibbs", 2, 2, [2, 3, 23, 20, 21]), 2026, 2, kick)
    assert e.slot_id == 2 and e.slot == "RB" and e.is_starter
    assert e.position == "RB" and e.pro_team == "DET" and e.opponent == "BUF"
    assert e.projected == 12.5 and e.season_avg == 20.0 and e.percent_owned == 99.5
    assert e.kickoff and not e.bye and not e.lineup_locked
    assert "RB/WR/TE" in e.eligible_slots


def test_parse_entry_flags_bye_when_team_has_no_game():
    kick = roster.kickoffs_for_period(SCHEDULE, 2)
    e = roster.parse_entry(_entry(2, "Rashee Rice", 20, 3, [4, 23, 20], pro_team=12), 2026, 2, kick)
    assert e.bye and e.kickoff is None and not e.is_starter


def test_lineup_problems_catches_ineligible_and_overfilled_slots():
    kick = {}
    entries = [
        roster.parse_entry(_entry(1, "QB One", 0, 1, [0, 20]), 2026, 2, kick),
        roster.parse_entry(_entry(2, "RB One", 2, 2, [2, 23, 20]), 2026, 2, kick),
        roster.parse_entry(_entry(3, "WR in RB", 2, 3, [4, 23, 20]), 2026, 2, kick),
        roster.parse_entry(_entry(4, "RB Three", 2, 2, [2, 23, 20]), 2026, 2, kick),
        roster.parse_entry(_entry(5, "Healthy in IR", 21, 2, [2, 20, 21], injury="ACTIVE"), 2026, 2, kick),
    ]
    counts = {0: 1, 2: 2, 4: 2, 6: 1, 23: 2, 16: 1, 17: 1}
    problems = roster.lineup_problems(entries, counts)
    assert any("not eligible for RB" in p for p in problems)
    assert any("3 players in RB" in p for p in problems)
    assert any("IR without" in p for p in problems)


def test_lineup_problems_is_empty_for_a_legal_lineup():
    entries = [
        roster.parse_entry(_entry(1, "QB One", 0, 1, [0, 20]), 2026, 2, {}),
        roster.parse_entry(_entry(2, "RB One", 2, 2, [2, 23, 20]), 2026, 2, {}),
        roster.parse_entry(_entry(3, "WR Flex", 23, 3, [4, 23, 20]), 2026, 2, {}),
        roster.parse_entry(_entry(4, "Bench", 20, 2, [2, 23, 20]), 2026, 2, {}),
    ]
    assert roster.lineup_problems(entries, {0: 1, 2: 2, 23: 2}) == []


def test_distinct_kickoffs_groups_players_by_game_time():
    kick = roster.kickoffs_for_period(SCHEDULE, 2)
    entries = [
        roster.parse_entry(_entry(1, "Gibbs", 2, 2, [2], pro_team=8), 2026, 2, kick),
        roster.parse_entry(_entry(2, "Allen", 0, 1, [0], pro_team=2), 2026, 2, kick),
        roster.parse_entry(_entry(3, "Rice", 20, 3, [4], pro_team=12), 2026, 2, kick),
    ]
    groups = roster.distinct_kickoffs(entries)
    assert len(groups) == 1
    assert sorted(groups[0][1]) == ["Allen", "Gibbs"]


def test_to_table_lists_starters_first():
    kick = roster.kickoffs_for_period(SCHEDULE, 2)
    entries = [
        roster.parse_entry(_entry(3, "Rice", 20, 3, [4], pro_team=12), 2026, 2, kick),
        roster.parse_entry(_entry(1, "Gibbs", 2, 2, [2], pro_team=8, locked=True), 2026, 2, kick),
    ]
    table = roster.to_table(entries)
    lines = table.splitlines()
    assert lines[0].startswith("RB") and "Gibbs" in lines[0] and "LOCKED" in lines[0]
    assert lines[1].startswith("BE") and "BYE" in lines[1]
