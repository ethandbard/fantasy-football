"""Tests for draft-board filtering, sorting, and stat formatting."""
import pandas as pd

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


def test_injury_tag_hides_active():
    assert draft.injury_tag("ACTIVE") is None
    assert draft.injury_tag("QUESTIONABLE") == "Q"
    assert draft.injury_tag("OUT") == "O"
