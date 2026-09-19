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


def test_schedule_round_trips_projected_score(fresh_db):
    fresh_db.upsert_schedule([
        {"year": 2026, "week": 1, "matchup_period": 1, "team_id": 1,
         "opponent_id": 2, "is_home": 1, "projected_score": 112.4},
        {"year": 2026, "week": 1, "matchup_period": 1, "team_id": 2,
         "opponent_id": 1, "is_home": 0, "projected_score": 98.1},
    ])
    rows = fresh_db.get_all_schedule()
    assert {r["team_id"]: r["projected_score"] for r in rows} == {1: 112.4, 2: 98.1}


def test_schedule_rows_without_projection_still_write(fresh_db):
    """Rows written by code predating the column carry no projected_score key;
    the upsert must not choke on them."""
    fresh_db.upsert_schedule([
        {"year": 2025, "week": 1, "matchup_period": 1, "team_id": 1,
         "opponent_id": 2, "is_home": 1},
    ])
    rows = fresh_db.get_all_schedule()
    assert rows[0]["projected_score"] is None


def test_team_logo_round_trips_bytes(fresh_db):
    fresh_db.upsert_team_logo(2026, 1, "https://x/logo.png", b"\x89PNG...", "image/png")

    rows = fresh_db.get_all_team_logos()
    assert len(rows) == 1
    assert rows[0]["content"] == b"\x89PNG..."
    assert rows[0]["content_type"] == "image/png"
    assert fresh_db.get_logo_urls() == {(2026, 1): "https://x/logo.png"}


def test_team_logo_replaces_on_the_same_key(fresh_db):
    fresh_db.upsert_team_logo(2026, 1, "https://x/old.png", b"old", "image/png")
    fresh_db.upsert_team_logo(2026, 1, "https://x/new.svg", b"new", "image/svg+xml")

    rows = fresh_db.get_all_team_logos()
    assert len(rows) == 1
    assert rows[0]["url"] == "https://x/new.svg"
    assert rows[0]["content"] == b"new"


def test_get_years_includes_player_only_seasons(fresh_db):
    fresh_db.upsert_weekly_scores([_row(1, 1), _row(1, 2)])
    fresh_db.replace_players(2026, [_player(1)])

    assert fresh_db.get_years() == [2026, 2025]


def test_a_week_with_no_points_is_offered_for_recollection(fresh_db):
    """A snapshot taken before kickoff: every column present, every score
    zero. It has to be fetched again once the games are played."""
    fresh_db.upsert_weekly_scores([
        _row(1, 1, score=0.0, matchup_score=0.0),
        _row(1, 2, score=0.0, matchup_score=0.0),
    ])

    assert fresh_db.get_collected_weeks(2025) == set()


def test_delete_week_removes_scores_and_standings_and_reports_it(fresh_db):
    fresh_db.upsert_weekly_scores([_row(1, 1), _row(1, 2), _row(2, 1), _row(2, 2)])
    fresh_db.upsert_standings([{
        "year": 2025, "week": 2, "team_id": 1, "team_name": "Team 1", "wins": 1,
        "losses": 0, "ties": 0, "points_for": 1.0, "points_against": 0.0, "rank": 1,
    }])

    assert fresh_db.delete_week(2025, 2) == 3
    assert fresh_db.delete_week(2025, 2) == 0
    assert fresh_db.get_collected_weeks(2025) == {1}
    assert fresh_db.get_all_latest_standings() == []


def test_site_content_round_trips_and_replaces_on_the_same_key(fresh_db):
    before = fresh_db.fingerprint()
    fresh_db.upsert_site_content("recap", 2025, 3, "# Week 3\n\nA close one.", title="Photo finish", run_id="r1")
    rows = fresh_db.get_all_site_content()
    assert len(rows) == 1
    assert rows[0]["title"] == "Photo finish" and rows[0]["body"].startswith("# Week 3")
    assert rows[0]["run_id"] == "r1" and rows[0]["written_at"]
    assert fresh_db.fingerprint() != before

    fresh_db.upsert_site_content("recap", 2025, 3, "Rewritten.", title="Second pass")
    rows = fresh_db.get_all_site_content()
    assert len(rows) == 1 and rows[0]["body"] == "Rewritten." and rows[0]["title"] == "Second pass"


def test_site_content_is_newest_first_and_refuses_unknown_kinds(fresh_db):
    fresh_db.upsert_site_content("recap", 2025, 2, "two")
    fresh_db.upsert_site_content("recap", 2025, 5, "five")
    fresh_db.upsert_site_content("recap", 2024, 9, "old")
    assert [(r["year"], r["week"]) for r in fresh_db.get_all_site_content()] == [(2025, 5), (2025, 2), (2024, 9)]
    with pytest.raises(ValueError):
        fresh_db.upsert_site_content("manifesto", 2025, 1, "no")


def test_teams_keep_the_owner_guid_and_managers_round_trip(fresh_db):
    fresh_db.upsert_teams([
        {"year": 2025, "team_id": 4, "team_name": "New Name", "abbrev": "NEW", "logo_url": None,
         "owner": "ESPNfan5", "owner_id": "{G-FELIX}", "owner_name": "Felipe"},
        # Rows from callers that predate the owner columns still write.
        {"year": 2025, "team_id": 5, "team_name": "Plain", "abbrev": "PLN", "logo_url": None,
         "owner": None},
    ])
    rows = {t["team_id"]: t for t in fresh_db.get_all_teams()}
    assert rows[4]["owner_id"] == "{G-FELIX}" and rows[4]["owner_name"] == "Felipe"
    assert rows[5]["owner_id"] is None

    before = fresh_db.fingerprint()
    fresh_db.upsert_managers({"{G-FELIX}": "Felipe"})
    fresh_db.upsert_managers({"{G-FELIX}": "Felipe R"})
    assert fresh_db.get_managers() == {"{G-FELIX}": "Felipe R"}
    assert fresh_db.fingerprint() != before


def test_owner_columns_are_added_to_a_database_that_predates_them(fresh_db):
    with fresh_db.get_connection() as conn:
        conn.execute("DROP TABLE teams")
        conn.execute("CREATE TABLE teams (year INTEGER NOT NULL, team_id INTEGER NOT NULL, "
                     "team_name TEXT NOT NULL, abbrev TEXT, logo_url TEXT, owner TEXT, "
                     "PRIMARY KEY (year, team_id))")
    fresh_db.init_db()
    with fresh_db.get_connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(teams)")}
    assert {"owner_id", "owner_name"} <= columns
