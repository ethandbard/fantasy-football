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
