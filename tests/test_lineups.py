"""
Tests for the lineup-level derivations: optimal lineups, bench regret,
position contribution and the player leaderboard.

The fixtures are tiny hand-built rosters with a reduced slot set, so every
expected number can be added up in the margin.
"""
import pandas as pd
import pytest

import gamedaybot.web.stats as stats


# A cut-down lineup rule: one each of QB, RB, WR and a flex.
SLOTS = {"QB": 1, "RB": 1, "WR": 1, "RB/WR/TE": 1}

FULL_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "D/ST": 1, "K": 1}


def _player(name, position, slot, points, eligible=None, **extra):
    if eligible is None:
        eligible = {
            "QB": ["QB", "BE", "IR"],
            "RB": ["RB", "RB/WR/TE", "BE", "IR"],
            "WR": ["WR", "RB/WR/TE", "BE", "IR"],
            "TE": ["TE", "RB/WR/TE", "BE", "IR"],
            "D/ST": ["D/ST", "BE"],
            "K": ["K", "BE"],
        }[position]
    return {"player_name": name, "position": position, "slot": slot,
            "eligible_slots": eligible, "points": points, **extra}


def _lineup_frame(week_rosters):
    """{(year, week, team_id): [player dicts]} -> a lineup_scores frame."""
    rows = []
    for (year, week, team_id), players in week_rosters.items():
        for i, p in enumerate(players):
            rows.append({"year": year, "week": week, "team_id": team_id,
                         "player_id": p.get("player_id", hash(p["player_name"]) % 10000),
                         "pro_team": "", "projected": None, "injury_status": None,
                         "collected_at": None, **p})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ optimal lineup

def test_optimal_lineup_keeps_the_rb_slot_for_a_running_back():
    """A WR3 that outscores the RB2 cannot take the RB slot; it competes for
    the flex only, and the flex goes to whoever is best among the leftovers."""
    roster = [
        _player("QB_a", "QB", "QB", 20.0),
        _player("RB_a", "RB", "RB", 15.0),
        _player("RB_b", "RB", "BE", 10.0),
        _player("WR_a", "WR", "WR", 14.0),
        _player("WR_b", "WR", "BE", 13.0),
        _player("WR_c", "WR", "BE", 11.0),
        _player("RB_c", "RB", "BE", 9.0),
    ]
    total, chosen = stats.optimal_lineup(roster, FULL_SLOTS)

    picked = {slot: name for slot, name, _ in chosen}
    by_slot = {}
    for slot, name, _ in chosen:
        by_slot.setdefault(slot, []).append(name)

    assert by_slot["RB"] == ["RB_a", "RB_b"]      # RB_b (10) keeps its slot
    assert by_slot["WR"] == ["WR_a", "WR_b"]
    assert by_slot["RB/WR/TE"] == ["WR_c"]        # 11 beats RB_c's 9 for flex
    assert total == pytest.approx(20 + 15 + 10 + 14 + 13 + 11)
    assert "QB" in picked


def test_optimal_lineup_promotes_a_benched_rb_and_hands_the_flex_to_the_wr():
    """RBs of 15, 10 and 12 fill the two RB slots with 15 and 12; the 10 then
    loses the flex to the 11-point WR3. Starting the 12 in the flex instead
    (15 + 10 in RB, 12 flex) would score a point less."""
    roster = [
        _player("QB_a", "QB", "QB", 20.0),
        _player("RB_a", "RB", "RB", 15.0),
        _player("RB_b", "RB", "RB", 10.0),
        _player("RB_c", "RB", "BE", 12.0),
        _player("WR_a", "WR", "WR", 14.0),
        _player("WR_b", "WR", "WR", 13.0),
        _player("WR_c", "WR", "BE", 11.0),
    ]
    total, chosen = stats.optimal_lineup(roster, FULL_SLOTS)
    by_slot = {}
    for slot, name, _ in chosen:
        by_slot.setdefault(slot, []).append(name)

    assert by_slot["RB"] == ["RB_a", "RB_c"]
    assert by_slot["RB/WR/TE"] == ["WR_c"]
    assert total == pytest.approx(85.0)


def test_optimal_lineup_flex_takes_a_third_rb_when_it_is_the_best_leftover():
    roster = [
        _player("QB_a", "QB", "QB", 20.0),
        _player("RB_a", "RB", "RB", 15.0),
        _player("RB_b", "RB", "RB", 14.0),
        _player("RB_c", "RB", "BE", 12.0),
        _player("WR_a", "WR", "WR", 14.0),
        _player("WR_b", "WR", "WR", 13.0),
        _player("WR_c", "WR", "BE", 11.0),
    ]
    _, chosen = stats.optimal_lineup(roster, FULL_SLOTS)
    flex = [name for slot, name, _ in chosen if slot == "RB/WR/TE"]
    assert flex == ["RB_c"]


def test_optimal_lineup_ignores_ir_and_players_with_no_eligibility():
    roster = [
        _player("QB_a", "QB", "QB", 20.0),
        _player("QB_hurt", "QB", "IR", 40.0),
        _player("RB_a", "RB", "RB", 5.0),
        _player("RB_ghost", "RB", "BE", 50.0, eligible=[]),
        _player("WR_a", "WR", "WR", 7.0),
    ]
    total, chosen = stats.optimal_lineup(roster, SLOTS)
    names = {name for _, name, _ in chosen}

    assert "QB_hurt" not in names
    assert "RB_ghost" not in names
    assert total == pytest.approx(32.0)


def test_optimal_lineup_treats_dst_as_a_dedicated_slot_not_a_flex():
    """D/ST has a slash in its name and must not be filled from RB/WR/TE."""
    roster = [
        _player("Bears D", "D/ST", "D/ST", 8.0),
        _player("RB_a", "RB", "RB", 30.0),
    ]
    _, chosen = stats.optimal_lineup(roster, {"RB": 1, "D/ST": 1})
    assert ("D/ST", "Bears D", 8.0) in chosen


def test_optimal_lineup_reads_missing_points_as_zero():
    roster = [_player("QB_a", "QB", "QB", None), _player("QB_b", "QB", "BE", float("nan"))]
    total, chosen = stats.optimal_lineup(roster, {"QB": 1})
    assert total == 0.0
    assert len(chosen) == 1


def test_optimal_lineup_on_an_empty_roster():
    assert stats.optimal_lineup([], SLOTS) == (0.0, [])


# -------------------------------------------------------------- bench regret

# Team 1 starts QB 20, RB 10, WR 12, flex WR 8 (actual 50) and benches an RB
# who scored 15. Optimal is QB 20 + RB 15 + WR 12 + flex RB_a 10 = 57.
ROSTER = [
    _player("QB_a", "QB", "QB", 20.0, player_id=1),
    _player("RB_a", "RB", "RB", 10.0, player_id=2),
    _player("WR_a", "WR", "WR", 12.0, player_id=3),
    _player("WR_b", "WR", "RB/WR/TE", 8.0, player_id=4),
    _player("RB_b", "RB", "BE", 15.0, player_id=5),
]


@pytest.fixture
def lineups():
    return _lineup_frame({
        (2025, 1, 1): ROSTER,
        (2025, 2, 1): ROSTER,
        (2025, 3, 1): ROSTER,
    })


@pytest.fixture
def scores():
    # Week 1: lost by 5 (optimal 57 would have won). Week 2: lost by 10
    # (optimal still short). Week 3: won.
    rows = []
    for week, own, opp in ((1, 50.0, 55.0), (2, 50.0, 60.0), (3, 50.0, 45.0)):
        rows.append((2025, week, 1, "Ravens", own, 2, "Bears", 1))
        rows.append((2025, week, 2, "Bears", opp, 1, "Ravens", 0))
    return pd.DataFrame(rows, columns=[
        "year", "week", "team_id", "team_name", "score",
        "opponent_id", "opponent_name", "is_home",
    ])


def test_bench_regrets_flags_only_the_loss_the_bench_would_have_flipped(lineups, scores):
    regrets = stats.bench_regrets(lineups, SLOTS, scores).set_index("week")

    assert regrets.loc[1, "actual_points"] == pytest.approx(50.0)
    assert regrets.loc[1, "optimal_points"] == pytest.approx(57.0)
    assert regrets.loc[1, "regret"] == pytest.approx(7.0)
    assert regrets.loc[1, "team_name"] == "Ravens"
    assert regrets.loc[1, "opponent_score"] == 55.0

    assert regrets.loc[1, "result"] == "L" and bool(regrets.loc[1, "flipped"]) is True
    assert regrets.loc[2, "result"] == "L" and bool(regrets.loc[2, "flipped"]) is False
    assert regrets.loc[3, "result"] == "W" and bool(regrets.loc[3, "flipped"]) is False


def test_bench_regret_summary_totals_per_team(lineups, scores):
    summary = stats.bench_regret_summary(stats.bench_regrets(lineups, SLOTS, scores))
    row = summary.set_index("team_id").loc[1]

    assert row["team_name"] == "Ravens"
    assert row["weeks"] == 3
    assert row["total_regret"] == pytest.approx(21.0)
    assert row["avg_regret"] == pytest.approx(7.0)
    assert row["flipped_losses"] == 1


def test_bench_regrets_without_scores_still_reports_the_regret(lineups):
    regrets = stats.bench_regrets(lineups, SLOTS, pd.DataFrame())
    assert len(regrets) == 3
    assert (regrets["result"] == "").all()
    assert not regrets["flipped"].any()
    assert regrets["team_name"].iloc[0] == "Team 1"


def test_bench_regrets_empty_inputs():
    assert stats.bench_regrets(pd.DataFrame(), SLOTS, pd.DataFrame()).empty
    assert stats.bench_regret_summary(pd.DataFrame()).empty
    assert "flipped" in stats.bench_regrets(pd.DataFrame(), SLOTS, None).columns


# ------------------------------------------------------ position contribution

def test_position_contribution_counts_the_flex_under_the_players_position(lineups):
    contrib = stats.position_contribution(lineups[lineups["week"] == 1],
                                          names={1: "Ravens"})
    row = contrib.set_index("team_id").loc[1]

    assert row["QB"] == 20.0
    assert row["RB"] == 10.0          # the benched RB_b does not count
    assert row["WR"] == 20.0          # WR_a in WR plus WR_b in the flex
    assert row["total"] == 50.0
    assert row["team_name"] == "Ravens"

    shares = [row[f"share_{p}"] for p in stats.POSITIONS]
    assert sum(shares) == pytest.approx(1.0)
    assert row["share_WR"] == pytest.approx(0.4)


def test_position_contribution_long_form_matches_wide(lineups):
    contrib = stats.position_contribution(lineups)
    long = stats.position_contribution_long(contrib)

    assert len(long) == len(contrib) * len(stats.POSITIONS)
    assert long.groupby("team_id")["share"].sum().iloc[0] == pytest.approx(1.0)


def test_position_contribution_empty():
    out = stats.position_contribution(pd.DataFrame())
    assert out.empty
    assert "share_QB" in out.columns and "D/ST" in out.columns


# ---------------------------------------------------------- player leaderboard

def test_player_leaderboard_boom_and_bust_are_relative_to_the_players_own_average():
    # Starts of 4, 10, 10, 40 average 16: 40 is a boom (>= 24), 4 a bust
    # (<= 8). A 50-point week on the bench counts toward the total and the
    # best week but not toward the starting figures.
    weeks = {
        (2025, 1, 1): [_player("Star", "WR", "WR", 4.0, player_id=9)],
        (2025, 2, 1): [_player("Star", "WR", "WR", 10.0, player_id=9)],
        (2025, 3, 1): [_player("Star", "WR", "WR", 10.0, player_id=9)],
        (2025, 4, 1): [_player("Star", "WR", "WR", 40.0, player_id=9)],
        (2025, 5, 2): [_player("Star", "WR", "BE", 50.0, player_id=9)],
    }
    board = stats.player_leaderboard(_lineup_frame(weeks)).set_index("player_id")
    star = board.loc[9]

    assert star["weeks_rostered"] == 5
    assert star["starts"] == 4
    assert star["points_total"] == pytest.approx(114.0)
    assert star["points_as_starter"] == pytest.approx(64.0)
    assert star["avg_as_starter"] == pytest.approx(16.0)
    assert star["boom_rate"] == pytest.approx(0.25)
    assert star["bust_rate"] == pytest.approx(0.25)
    assert star["best_week"] == 5 and star["best_points"] == 50.0
    assert star["team_id"] == 2   # most recent roster


def test_player_leaderboard_orders_by_starter_points_and_honours_min_weeks(lineups):
    board = stats.player_leaderboard(lineups)
    assert board["player_name"].iloc[0] == "QB_a"      # 60 as a starter
    # RB_b never started: 45 total, 0 as a starter, so it sorts last.
    assert board["player_name"].iloc[-1] == "RB_b"
    assert board.set_index("player_id").loc[5, "points_as_starter"] == 0.0

    assert stats.player_leaderboard(lineups, min_weeks=4).empty
    assert stats.player_leaderboard(pd.DataFrame()).empty
