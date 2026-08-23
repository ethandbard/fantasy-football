"""
Tests for ESPN player-pool parsing.

The fetch talks to ESPN; these cover the flatten that turns one payload
entry into a players-table row, plus bye-week extraction from a pro schedule.
"""
import json

from gamedaybot.espn import players


def _qb_entry():
    return {
        "id": 3139477,
        "onTeamId": 0,
        "player": {
            "id": 3139477,
            "fullName": "Patrick Mahomes",
            "eligibleSlots": [0, 7, 20, 21],
            "defaultPositionId": 1,
            "proTeamId": 12,
            "injured": False,
            "injuryStatus": "ACTIVE",
            "ownership": {
                "percentOwned": 99.87,
                "percentStarted": 88.2,
                "averageDraftPosition": 42.3,
            },
            "draftRanksByRankType": {
                "STANDARD": {"rank": 45, "auctionValue": 18},
                "PPR": {"rank": 48, "auctionValue": 15},
            },
            "stats": [
                {
                    "seasonId": 2026,
                    "scoringPeriodId": 0,
                    "statSourceId": 1,
                    "statSplitTypeId": 0,
                    "appliedTotal": 355.42,
                    "appliedAverage": 20.907,
                    "stats": {
                        "0": 580.2,
                        "1": 380.1,
                        "3": 4501.2,
                        "4": 32.1,
                        "20": 11.2,
                        "22": 264.8,
                    },
                },
                {
                    "seasonId": 2025,
                    "scoringPeriodId": 0,
                    "statSourceId": 0,
                    "statSplitTypeId": 0,
                    "appliedTotal": 312.8,
                    "appliedAverage": 18.4,
                    "stats": {"0": 550, "1": 360, "3": 4183, "4": 26},
                },
            ],
        },
    }


def test_parse_qb_projections_and_ranks():
    row = players.parse_player_entry(_qb_entry(), 2026, {12: 10}, "PPR")

    assert row["player_id"] == 3139477
    assert row["name"] == "Patrick Mahomes"
    assert row["position"] == "QB"
    assert row["pro_team"] == "KC"
    assert row["bye_week"] == 10
    assert row["draft_rank"] == 48
    assert row["auction_value"] == 15
    assert row["adp"] == 42.3
    assert row["percent_owned"] == 99.9
    assert row["projected_points"] == 355.4
    assert row["last_year_points"] == 312.8

    projected = json.loads(row["projected_stats"])
    assert projected["passingYards"] == 4501.2
    assert projected["passingYardsPerGame"] == 264.8
    assert projected["passingTouchdowns"] == 32.1
    assert projected["passingAttempts"] == 580.2
    assert projected["passingCompletions"] == 380.1


def test_standard_rank_list_is_used_when_asked():
    row = players.parse_player_entry(_qb_entry(), 2026, {12: 10}, "STANDARD")
    assert row["draft_rank"] == 45
    assert row["auction_value"] == 18


def test_negative_ownership_becomes_none():
    entry = _qb_entry()
    entry["player"]["ownership"]["percentOwned"] = -1
    entry["player"]["ownership"]["averageDraftPosition"] = -1
    row = players.parse_player_entry(entry, 2026, {}, "PPR")
    assert row["percent_owned"] is None
    assert row["adp"] is None


def test_missing_player_id_is_dropped():
    assert players.parse_player_entry({"player": {"fullName": "Nobody"}}, 2026, {}) is None


def test_pool_dedupes_by_player_id():
    rows = players.parse_player_pool([_qb_entry(), _qb_entry()], 2026, {12: 10}, "PPR")
    assert len(rows) == 1


def test_bye_week_is_the_missing_nfl_week():
    schedule = {
        12: {str(w): [{"id": 1}] for w in range(1, 19) if w != 10},
        1: {str(w): [{"id": 1}] for w in range(1, 19) if w != 5},
    }
    byes = players.bye_weeks_from_schedule(schedule)
    assert byes[12] == 10
    assert byes[1] == 5


def test_ppr_scoring_picks_the_ppr_rank_list():
    class _League:
        class settings:
            scoring_format = [{"id": 53, "points": 1.0}]

    assert players.scoring_rank_type(_League()) == "PPR"

    class _Std:
        class settings:
            scoring_format = [{"id": 4, "points": 4.0}]

    assert players.scoring_rank_type(_Std()) == "STANDARD"


def test_bye_weeks_empty_when_schedule_is_incomplete():
    schedule = {12: {"1": [{"id": 1}], "2": [{"id": 1}]}}
    assert players.bye_weeks_from_schedule(schedule) == {}
