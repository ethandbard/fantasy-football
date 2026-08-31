"""Tests for draft-board filtering, sorting, and stat formatting."""
import pandas as pd
import pytest

import gamedaybot.web.draft as draft


def _frame():
    return pd.DataFrame([
        {
            "name": "Ja'Marr Chase", "position": "WR", "pro_team": "CIN",
            "draft_rank": 1, "adp": 1.2, "projected_points": 312.4,
            "bye_week": 10, "percent_owned": 99.8,
            "projected_stats": {"receivingYards": 1400, "receivingReceptions": 110},
        },
        {
            "name": "Bijan Robinson", "position": "RB", "pro_team": "ATL",
            "draft_rank": 2, "adp": 2.1, "projected_points": 298.0,
            "bye_week": 12, "percent_owned": 99.5,
            "projected_stats": {"rushingYards": 1300, "receivingReceptions": 55},
        },
        {
            "name": "Josh Allen", "position": "QB", "pro_team": "BUF",
            "draft_rank": 28, "adp": 32.4, "projected_points": 355.1,
            "bye_week": 7, "percent_owned": 97.2,
            "projected_stats": {"passingYards": 4200, "passingTouchdowns": 32},
        },
    ])


def test_filter_by_position():
    out = draft.filter_players(_frame(), "QB")
    assert list(out["name"]) == ["Josh Allen"]


def test_search_matches_name_or_team():
    out = draft.filter_players(_frame(), "ALL", "atl")
    assert list(out["name"]) == ["Bijan Robinson"]
    out = draft.filter_players(_frame(), "ALL", "chase")
    assert list(out["name"]) == ["Ja'Marr Chase"]


def test_sort_rank_defaults_ascending():
    out = draft.sort_players(_frame(), "draft_rank")
    assert list(out["draft_rank"]) == [1, 2, 28]


def test_sort_fpts_defaults_descending():
    out = draft.sort_players(_frame(), "projected_points")
    assert list(out["name"]) == ["Josh Allen", "Ja'Marr Chase", "Bijan Robinson"]


def test_flatten_exposes_counting_stats_for_sort():
    flat = draft.flatten_stats(_frame())
    out = draft.sort_players(flat, "passingYards")
    assert out.iloc[0]["name"] == "Josh Allen"


def test_qb_columns_include_pc_pa_py():
    labels = [label for _key, label in draft.columns_for("QB")]
    assert labels[:3] == ["Rk", "Player", "Pos"]
    assert "PC" in labels and "PA" in labels and "PY" in labels
    assert "FPTS" in labels


def test_all_view_omits_position_specific_stats():
    labels = [label for _key, label in draft.columns_for("ALL")]
    assert "PC" not in labels
    assert "FPTS" in labels


def test_format_stat_dashes_missing_and_drops_trailing_zero():
    assert draft.format_stat(None) == "—"
    assert draft.format_stat(32.0) == "32"
    assert draft.format_stat(32.14) == "32.1"
    assert draft.format_stat(99.8, "pct") == "99.8"
    assert draft.format_stat("DET", "text") == "DET"


def test_attach_picks_adds_club_and_pick_label():
    players = _frame().assign(player_id=[10, 20, 30])
    picks = pd.DataFrame([
        {"player_id": 10, "team_name": "John Foot Ball", "round_num": 1,
         "round_pick": 3, "overall_pick": 3},
        {"player_id": 20, "team_name": "NOT LAST! 🤓", "round_num": 1,
         "round_pick": 2, "overall_pick": 2},
    ])
    out = draft.attach_picks(players, picks)
    chase = out[out["name"] == "Ja'Marr Chase"].iloc[0]
    assert chase["draft_team"] == "John Foot Ball"
    assert chase["pick_label"] == "1.3"
    allen = out[out["name"] == "Josh Allen"].iloc[0]
    assert pd.isna(allen["draft_team"])


def test_sort_by_pick_uses_overall_pick_not_label_string():
    players = draft.attach_picks(
        _frame().assign(player_id=[10, 20, 30]),
        pd.DataFrame([
            {"player_id": 10, "team_name": "Aces", "round_num": 10,
             "round_pick": 1, "overall_pick": 111},
            {"player_id": 20, "team_name": "Aces", "round_num": 2,
             "round_pick": 5, "overall_pick": 17},
            {"player_id": 30, "team_name": "Aces", "round_num": 1,
             "round_pick": 3, "overall_pick": 3},
        ]),
    )
    out = draft.sort_players(players, "pick_label")
    assert list(out["pick_label"]) == ["1.3", "2.5", "10.1"]
    out = draft.sort_players(players, "pick_label", descending=True)
    assert list(out["pick_label"]) == ["10.1", "2.5", "1.3"]


def test_filter_by_club():
    players = draft.attach_picks(
        _frame().assign(player_id=[10, 20, 30]),
        pd.DataFrame([
            {"player_id": 10, "team_name": "Aces", "round_num": 1,
             "round_pick": 1, "overall_pick": 1},
        ]),
    )
    out = draft.filter_players(players, club="Aces")
    assert list(out["name"]) == ["Ja'Marr Chase"]


def _drafted_board():
    """Three players drafted by two clubs, plus one left on the board."""
    players = _frame().assign(player_id=[10, 20, 30])
    extra = pd.DataFrame([{
        "name": "Rome Odunze", "position": "WR", "pro_team": "CHI",
        "draft_rank": 40, "adp": 44.0, "projected_points": 200.0,
        "bye_week": 5, "percent_owned": 80.0, "projected_stats": {},
        "player_id": 40,
    }])
    players = pd.concat([players, extra], ignore_index=True)
    picks = pd.DataFrame([
        {"player_id": 10, "team_name": "Aces", "round_num": 1,
         "round_pick": 1, "overall_pick": 1},     # Chase, adp 1.2 -> -0.2
        {"player_id": 30, "team_name": "Aces", "round_num": 2,
         "round_pick": 2, "overall_pick": 4},     # Allen, adp 32.4 -> -28.4 reach
        {"player_id": 20, "team_name": "Bees", "round_num": 1,
         "round_pick": 2, "overall_pick": 2},     # Bijan, adp 2.1 -> -0.1
    ])
    return draft.attach_picks(players, picks)


def test_attach_picks_computes_adp_delta():
    board = _drafted_board()
    allen = board[board["name"] == "Josh Allen"].iloc[0]
    assert allen["adp_delta"] == pytest.approx(4 - 32.4)
    odunze = board[board["name"] == "Rome Odunze"].iloc[0]
    assert pd.isna(odunze["adp_delta"])


def test_filter_undrafted_pill():
    board = _drafted_board()
    out = draft.filter_players(board, club=draft.UNDRAFTED)
    assert list(out["name"]) == ["Rome Odunze"]


def test_steals_and_reaches_split_by_sign():
    board = _drafted_board()
    # Turn Bijan into a fallen player so the steal side has an entry.
    board.loc[board["name"] == "Bijan Robinson", "adp_delta"] = 22.9
    steals, reaches = draft.steals_and_reaches(board, n=3)
    assert list(steals["name"]) == ["Bijan Robinson"]
    # Allen (-28.4) is the worst reach; Chase (-0.2) trails him.
    assert list(reaches["name"]) == ["Josh Allen", "Ja'Marr Chase"]


def test_club_summaries_order_by_projected_total():
    cards = draft.club_summaries(_drafted_board())
    assert [c["club"] for c in cards] == ["Aces", "Bees"]  # 667.5 vs 298.0
    aces = cards[0]
    assert aces["projected"] == pytest.approx(312.4 + 355.1)
    assert aces["shape"] == "1 QB · 1 WR"
    assert aces["biggest_reach"]["name"] == "Josh Allen"
    assert aces["best_value"] is None  # no positive deltas on this club


def test_grid_data_orders_clubs_by_first_round_slot():
    clubs, rows = draft.grid_data(_drafted_board())
    assert clubs == ["Aces", "Bees"]
    assert [r for r, _cells in rows] == [1, 2]
    round1 = rows[0][1]
    assert round1[0]["name"] == "Ja'Marr Chase"
    assert round1[1]["name"] == "Bijan Robinson"
    round2 = rows[1][1]
    assert round2[0]["name"] == "Josh Allen"
    assert round2[1] is None  # Bees made no round-2 pick in the fixture


def test_grid_data_empty_without_picks():
    clubs, rows = draft.grid_data(draft.attach_picks(
        _frame().assign(player_id=[10, 20, 30]), pd.DataFrame()))
    assert clubs == [] and rows == []


def test_format_stat_signed():
    assert draft.format_stat(22.9, "signed") == "+23"
    assert draft.format_stat(-28.4, "signed") == "-28"
    assert draft.format_stat(-0.4, "signed") == "0"


def test_injury_tag_hides_active():
    assert draft.injury_tag("ACTIVE") is None
    assert draft.injury_tag("QUESTIONABLE") == "Q"
    assert draft.injury_tag("OUT") == "O"
