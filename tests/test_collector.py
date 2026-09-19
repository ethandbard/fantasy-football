"""
Tests for the collector's pure decision logic. The network and league halves
are exercised against the real ESPN API by dev/api_healthcheck.py; what lives
here is the logic a wrong answer would silently corrupt the database with.
"""
import gamedaybot.espn.collector as collector
import gamedaybot.web.theme as theme


def test_default_espn_logo_is_never_fetched():
    """ESPN's default_logos silhouette is 'no logo', not a logo."""
    url = "https://g.espncdn.com/lm-static/ffl/images/default_logos/19.svg"
    assert not collector.wants_logo_fetch(url, None)


def test_missing_url_is_never_fetched():
    assert not collector.wants_logo_fetch(None, None)
    assert not collector.wants_logo_fetch("", None)


def test_unchanged_url_is_not_refetched():
    url = "https://g.espncdn.com/lm-static/logo-packs/x.svg"
    assert not collector.wants_logo_fetch(url, url)


def test_new_or_changed_url_is_fetched():
    url = "https://mystique-api.fantasy.espn.com/apis/v1/domains/lm/images/abc"
    assert collector.wants_logo_fetch(url, None)
    assert collector.wants_logo_fetch(url, "https://x/old.png")


def test_monogram_initials_skip_emoji_decoration():
    assert theme.monogram_initials("💯 U MAD Bro? 💯") == "UM"
    assert theme.monogram_initials("First Down Syndrome") == "FD"
    assert theme.monogram_initials("Yikes(4)") == "Y"


def test_monogram_falls_back_to_the_first_character():
    assert theme.monogram_initials("💯💯") == "💯"


def test_monogram_data_uri_is_svg():
    uri = theme.monogram_data_uri("First Down Syndrome", "#6699DD")
    assert uri.startswith("data:image/svg+xml;base64,")


# ------------------------------------------------------------ unplayed weeks
#
# ESPN advances current_week in its nightly update early Tuesday, before the
# 6:00 AM snapshot fires. So the job always sees the *upcoming* week as
# current, and before kickoff it sees week 1 with every score at zero. The
# 2026 dashboard opened with a 0-0 week 1 and an all-zero "latest" week 2
# because both were stored as if played.

import importlib
from types import SimpleNamespace

import pytest

import gamedaybot.storage.db as db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    importlib.reload(db)
    db.init_db()
    yield db
    importlib.reload(db)


def _team(team_id):
    return SimpleNamespace(team_id=team_id, team_name=f"Team {team_id}")


def _box(home_id, away_id, home_score, away_score):
    return SimpleNamespace(
        home_team=_team(home_id), away_team=_team(away_id),
        home_score=home_score, away_score=away_score,
        home_projected=120.0, away_projected=118.0,
        home_lineup=[], away_lineup=[],
    )


def _league(current_week, played_weeks, monkeypatch):
    """A league whose box scores carry real points only for played_weeks;
    every other week comes back the way ESPN serves a future week -- the
    matchups are there, the scores are all zero."""
    def box_scores(week):
        if week in played_weeks:
            return [_box(1, 2, 100.0 + week, 90.0), _box(3, 4, 0.0, 80.0)]
        return [_box(1, 2, 0.0, 0.0), _box(3, 4, 0.0, 0.0)]

    def standings():
        return [SimpleNamespace(team_id=t, team_name=f"Team {t}", wins=1, losses=0,
                                ties=0, points_for=100.0, points_against=90.0)
                for t in (1, 2, 3, 4)]

    league = SimpleNamespace(
        year=2026, current_week=current_week,
        settings=SimpleNamespace(matchup_periods={str(w): [w] for w in range(1, 15)}),
        box_scores=box_scores, standings=standings, teams=[],
    )
    monkeypatch.setattr(collector, "collect_league_state", lambda lg: True)
    return league


def _weeks(fresh_db):
    with fresh_db.get_connection() as conn:
        return {r["week"]: r["total"] for r in conn.execute(
            "SELECT week, SUM(score) AS total FROM weekly_scores GROUP BY week")}


def _standings_weeks(fresh_db):
    with fresh_db.get_connection() as conn:
        return {r["week"] for r in conn.execute(
            "SELECT DISTINCT week FROM standings_snapshot")}


def test_all_zero_rows_mean_the_week_is_unplayed():
    assert collector.week_is_unplayed([
        {"score": 0.0, "matchup_score": 0.0}, {"score": 0, "matchup_score": 0},
    ])


def test_one_scoreless_team_does_not_make_a_week_unplayed():
    assert not collector.week_is_unplayed([
        {"score": 0.0, "matchup_score": 0.0}, {"score": 88.2, "matchup_score": 88.2},
    ])


def test_the_tuesday_before_kickoff_stores_nothing(fresh_db, monkeypatch):
    """Sept 8 2026 on the VPS: current_week 1, no game played, and the job
    wrote eight zero rows and a 0-0 standings snapshot."""
    league = _league(current_week=1, played_weeks=set(), monkeypatch=monkeypatch)

    collector.collect_weekly_snapshot(league)

    assert _weeks(fresh_db) == {}
    assert _standings_weeks(fresh_db) == set()


def test_the_tuesday_after_week_one_stores_week_one_only(fresh_db, monkeypatch):
    """Sept 15 2026: ESPN already says week 2. Week 1 is the snapshot;
    week 2 is a schedule, not a result, and standings are stamped with the
    week they describe."""
    league = _league(current_week=2, played_weeks={1}, monkeypatch=monkeypatch)

    collector.collect_weekly_snapshot(league)

    assert _weeks(fresh_db) == {1: pytest.approx(271.0)}
    assert _standings_weeks(fresh_db) == {1}


def test_a_pre_kickoff_snapshot_is_repaired_by_the_next_run(fresh_db, monkeypatch):
    """The live database's shape after the two bad runs: a zero week 1 that
    looks collected, a zero week 2 on top. One run under the fix leaves the
    real week 1 and nothing else."""
    zero = {"year": 2026, "team_name": "x", "projected_score": 100.0,
            "opponent_id": 2, "opponent_name": "y", "is_home": 1,
            "score": 0.0, "matchup_score": 0.0}
    fresh_db.upsert_weekly_scores([
        dict(zero, week=w, team_id=t, matchup_period=w) for w in (1, 2) for t in (1, 2, 3, 4)
    ])
    fresh_db.upsert_standings([{
        "year": 2026, "week": 2, "team_id": 1, "team_name": "x", "wins": 1,
        "losses": 0, "ties": 0, "points_for": 1.0, "points_against": 0.0, "rank": 1,
    }])
    league = _league(current_week=2, played_weeks={1}, monkeypatch=monkeypatch)

    collector.collect_weekly_snapshot(league)

    assert _weeks(fresh_db) == {1: pytest.approx(271.0)}
    assert _standings_weeks(fresh_db) == {1}


def test_the_newest_stored_week_is_recollected_for_corrections(fresh_db, monkeypatch):
    league = _league(current_week=3, played_weeks={1, 2}, monkeypatch=monkeypatch)
    collector.collect_weekly_snapshot(league)
    assert _weeks(fresh_db)[2] == pytest.approx(272.0)

    original = league.box_scores

    def corrected(week):
        return [_box(1, 2, 150.0, 90.0), _box(3, 4, 0.0, 80.0)] if week == 2 \
            else original(week)
    league.box_scores = corrected

    collector.collect_weekly_snapshot(league)

    assert _weeks(fresh_db)[2] == pytest.approx(320.0)
    assert _weeks(fresh_db)[1] == pytest.approx(271.0)


def test_last_scoring_period_counts_playoff_weeks():
    """16 matchup periods, 18 weeks: the season gate and the schedule loop
    have to use the larger number or the playoffs never get collected."""
    league = SimpleNamespace(settings=SimpleNamespace(matchup_periods={
        **{str(w): [w] for w in range(1, 15)}, "15": [16, 15], "16": [17, 18],
    }))
    assert collector.last_scoring_period(league) == 18


# ------------------------------------------------------------ lineups, settings, activity

def _box_player(pid, name, pos, slot, points, proj, eligible):
    return SimpleNamespace(playerId=pid, name=name, position=pos, slot_position=slot, points=points,
                           projected_points=proj, eligibleSlots=eligible, proTeam="DET", injuryStatus="ACTIVE")


def test_lineup_rows_cover_both_sides_bench_included():
    home = SimpleNamespace(team_id=1, team_name="A")
    away = SimpleNamespace(team_id=2, team_name="B")
    box = SimpleNamespace(
        home_team=home, away_team=away,
        home_lineup=[_box_player(10, "Gibbs", "RB", "RB", 20.5, 18.0, ["RB", "RB/WR/TE", "BE"]),
                     _box_player(11, "Bench Guy", "WR", "BE", 9.0, 8.0, ["WR", "BE"])],
        away_lineup=[_box_player(12, "QB1", "QB", "QB", 22.0, 19.0, ["QB", "BE"]),
                     SimpleNamespace(name="ghost")],  # no playerId: skipped
    )
    rows = collector.lineup_rows_from_box(box, 2026, 3)
    assert [(r["team_id"], r["player_id"], r["slot"]) for r in rows] == [(1, 10, "RB"), (1, 11, "BE"), (2, 12, "QB")]
    assert rows[0]["eligible_slots"] == ["RB", "RB/WR/TE", "BE"] and rows[0]["points"] == 20.5
    assert all(r["year"] == 2026 and r["week"] == 3 for r in rows)


def test_lineup_rows_round_trip_through_the_database(fresh_db):
    home = SimpleNamespace(team_id=1, team_name="A")
    away = SimpleNamespace(team_id=2, team_name="B")
    box = SimpleNamespace(home_team=home, away_team=away,
                          home_lineup=[_box_player(10, "Gibbs", "RB", "RB", 20.5, 18.0, ["RB", "BE"])],
                          away_lineup=[_box_player(12, "QB1", "QB", "QB", 22.0, 19.0, ["QB", "BE"])])
    fresh_db.replace_lineup_week(2026, 3, collector.lineup_rows_from_box(box, 2026, 3))
    assert fresh_db.get_lineup_weeks(2026) == {3}
    rows = fresh_db.get_all_lineup_scores()
    assert len(rows) == 2 and rows[0]["eligible_slots"] == ["RB", "BE"]
    # Replacing the week drops a player who is no longer on the roster.
    box.home_lineup = []
    fresh_db.replace_lineup_week(2026, 3, collector.lineup_rows_from_box(box, 2026, 3))
    assert [r["player_id"] for r in fresh_db.get_all_lineup_scores()] == [12]


def test_snapshot_refetches_a_week_that_has_scores_but_no_lineups(fresh_db, monkeypatch):
    """Score rows written before lineup_scores existed are re-fetched once."""
    def row(week, team_id):
        return {"year": 2025, "week": week, "team_id": team_id, "team_name": f"Team {team_id}",
                "score": 100.0, "projected_score": 95.0, "opponent_id": 3 - team_id,
                "opponent_name": f"Team {3 - team_id}", "is_home": 1 if team_id == 1 else 0,
                "matchup_period": week, "matchup_score": 100.0}
    for w in (1, 2):
        fresh_db.upsert_weekly_scores([row(w, 1), row(w, 2)])
    fresh_db.replace_lineup_week(2025, 2, [{"year": 2025, "week": 2, "team_id": 1, "player_id": 5, "player_name": "x",
                                          "position": "RB", "pro_team": "DET", "slot": "RB", "eligible_slots": ["RB"],
                                          "projected": 1, "points": 2, "injury_status": "ACTIVE"}])
    asked = []
    monkeypatch.setattr(collector, "_collect_scores", lambda league, year, week: asked.append(week) or True)
    monkeypatch.setattr(collector, "collect_league_state", lambda league: None)
    monkeypatch.setattr(collector, "_collect_standings", lambda league, year, week: None)
    league = SimpleNamespace(year=2025, current_week=3)
    collector.collect_weekly_snapshot(league)
    # Week 1 has no lineups (re-fetched), week 2 is the newest stored week
    # (always re-fetched), week 3 is new.
    assert asked == [1, 2, 3]


def test_league_settings_and_activity_rows(fresh_db):
    settings = SimpleNamespace(position_slot_counts={"QB": 1, "RB": 2, "BE": 7, "IR": 1, "RB/WR/TE": 1},
                               reg_season_count=14, playoff_team_count=4)
    league = SimpleNamespace(year=2026, settings=settings, teams=[1] * 8)
    collector.collect_league_settings(league)
    stored = fresh_db.get_all_league_settings()[2026]
    assert stored["playoff_team_count"] == 4 and stored["reg_season_count"] == 14 and stored["team_count"] == 8
    assert stored["slot_counts"] == {"QB": 1, "RB": 2, "RB/WR/TE": 1}

    team = SimpleNamespace(team_id=3, team_name="Space Cadets")
    player = SimpleNamespace(playerId=77, name="Some RB", position="RB")
    activity = SimpleNamespace(date=1758000000000, actions=[
        (team, "WAIVER ADDED", player, 0),
        (team, "DROPPED", SimpleNamespace(playerId=78, name="Old TE", position="TE"), 0),
        (team, "TRADE_SENT", player, 0),
    ])
    rows = collector._activity_rows(activity, 2026)
    assert [r["action"] for r in rows] == ["WAIVER ADDED", "DROPPED"]
    assert fresh_db.insert_new_activity(rows) and not fresh_db.insert_new_activity(rows)
    assert [r["player_name"] for r in fresh_db.get_all_activity()] == ["Old TE", "Some RB"]


def test_collect_teams_stores_the_owner_guid_and_first_name_only(fresh_db):
    team = SimpleNamespace(
        team_id=4, team_name="Half In, Half Hurts", team_abbrev="HALF", logo_url=None,
        owners=[{"id": "{3013-abc}", "displayName": "ESPNfan79", "firstName": "Felipe ", "lastName": "Surname"}],
    )
    ownerless = SimpleNamespace(team_id=5, team_name="Ghost", team_abbrev="GHO", logo_url=None, owners=None)
    collector.collect_teams(SimpleNamespace(year=2026, teams=[team, ownerless]))

    rows = {t["team_id"]: t for t in fresh_db.get_all_teams()}
    assert rows[4]["owner_id"] == "{3013-ABC}"
    assert rows[4]["owner_name"] == "Felipe"
    assert "Surname" not in str(rows[4])
    assert rows[5]["owner_id"] is None and rows[5]["owner_name"] is None
