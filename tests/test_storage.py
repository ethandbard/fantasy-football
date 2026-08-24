"""
Tests for the storage layer's one piece of real logic: deciding which weeks
the collector is allowed to skip.

Everything else in db.py is a literal INSERT or SELECT, but this decision is
what kept a season's playoff weeks wrong on the live dashboard -- rows written
before matchup_period existed looked "collected", so the weekly job skipped
them and the two-week round totals sitting in the per-week score column were
never corrected.
"""
import importlib

import pytest

import gamedaybot.storage.db as db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A real SQLite file per test -- db.py talks to sqlite3 directly, so
    there is nothing meaningful to mock and a temp file is the honest double."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    importlib.reload(db)
    db.init_db()
    yield db
    importlib.reload(db)


def _row(week, team_id, **overrides):
    row = {
        "year": 2025, "week": week, "team_id": team_id,
        "team_name": f"Team {team_id}", "score": 100.0, "projected_score": 95.0,
        "opponent_id": 3 - team_id, "opponent_name": f"Team {3 - team_id}",
        "is_home": 1 if team_id == 1 else 0,
        "matchup_period": week, "matchup_score": 100.0,
    }
    row.update(overrides)
    return row


def test_a_fully_collected_week_is_skipped(fresh_db):
    fresh_db.upsert_weekly_scores([_row(1, 1), _row(1, 2)])

    assert fresh_db.get_collected_weeks(2025) == {1}


def test_a_week_missing_matchup_period_is_offered_for_recollection(fresh_db):
    """The production case: rows predating the column, holding a two-week
    round total in `score` with nothing to say so."""
    fresh_db.upsert_weekly_scores([
        _row(15, 1, matchup_period=None, matchup_score=None, score=250.4),
        _row(15, 2, matchup_period=None, matchup_score=None, score=238.0),
    ])

    assert fresh_db.get_collected_weeks(2025) == set()


def test_a_partially_backfilled_week_is_offered_for_recollection(fresh_db):
    """One repaired row does not make the week right -- the other side of the
    matchup is still a round total, and a half-fixed week reads worse than an
    untouched one."""
    fresh_db.upsert_weekly_scores([
        _row(16, 1),
        _row(16, 2, matchup_period=None, matchup_score=None),
    ])

    assert fresh_db.get_collected_weeks(2025) == set()


def test_only_the_requested_season_is_reported(fresh_db):
    fresh_db.upsert_weekly_scores([_row(1, 1), _row(1, 2)])
    fresh_db.upsert_weekly_scores([
        dict(_row(1, 1), year=2024), dict(_row(1, 2), year=2024),
    ])

    assert fresh_db.get_collected_weeks(2025) == {1}
    assert fresh_db.get_collected_weeks(2024) == {1}


def test_re_collecting_a_stale_week_makes_it_skippable(fresh_db):
    """The self-heal, end to end: a stale week is offered up, the collector
    overwrites it, and the next run leaves it alone."""
    fresh_db.upsert_weekly_scores([
        _row(15, 1, matchup_period=None, matchup_score=None, score=250.4),
        _row(15, 2, matchup_period=None, matchup_score=None, score=238.0),
    ])
    assert 15 not in fresh_db.get_collected_weeks(2025)

    fresh_db.upsert_weekly_scores([
        _row(15, 1, matchup_period=15, matchup_score=250.4, score=127.6),
        _row(15, 2, matchup_period=15, matchup_score=238.0, score=119.4),
    ])

    assert fresh_db.get_collected_weeks(2025) == {15}


def _player(player_id, **overrides):
    row = {
        "year": 2026, "player_id": player_id, "name": f"Player {player_id}",
        "position": "WR", "pro_team": "CIN", "bye_week": 10,
        "injury_status": "ACTIVE", "injured": 0, "percent_owned": 50.0,
        "percent_started": 20.0, "adp": 40.0, "auction_value": 5,
        "draft_rank": player_id, "pos_rank": player_id,
        "projected_points": 200.0, "projected_avg": 12.0,
        "last_year_points": 180.0, "projected_stats": "{}",
        "last_year_stats": "{}", "on_team_id": 0,
    }
    row.update(overrides)
    return row


def test_replace_players_swaps_the_season_pool(fresh_db):
    fresh_db.replace_players(2026, [_player(1), _player(2)])
    fresh_db.replace_players(2026, [_player(3, name="Kept")])

    rows = fresh_db.get_all_players()
    assert [r["player_id"] for r in rows] == [3]
    assert rows[0]["name"] == "Kept"
    assert isinstance(rows[0]["projected_stats"], dict)


def test_replace_draft_picks_swaps_the_season_board(fresh_db):
    fresh_db.replace_draft_picks(2026, [
        {"year": 2026, "overall_pick": 1, "round_num": 1, "round_pick": 1,
         "team_id": 1, "team_name": "Aces", "player_id": 10,
         "player_name": "Jahmyr Gibbs", "bid_amount": 0, "keeper": 0},
        {"year": 2026, "overall_pick": 2, "round_num": 1, "round_pick": 2,
         "team_id": 2, "team_name": "Bees", "player_id": 20,
         "player_name": "Bijan Robinson", "bid_amount": 0, "keeper": 0},
    ])
    fresh_db.replace_draft_picks(2026, [
        {"year": 2026, "overall_pick": 1, "round_num": 1, "round_pick": 1,
         "team_id": 1, "team_name": "Aces", "player_id": 10,
         "player_name": "Jahmyr Gibbs", "bid_amount": 0, "keeper": 0},
    ])
    rows = fresh_db.get_all_draft_picks()
    assert [r["player_id"] for r in rows] == [10]


def test_get_years_includes_player_only_seasons(fresh_db):
    fresh_db.upsert_weekly_scores([_row(1, 1), _row(1, 2)])
    fresh_db.replace_players(2026, [_player(1)])

    assert fresh_db.get_years() == [2026, 2025]
