"""
The data analyst: the expected-points model, the forecast, the lineup and
matchup math, the report tables, and the research-file section that must
survive the research job rewriting the file.
"""
import math

import pandas as pd
import pytest

from agent import jobs
from agent.analytics import model, report
from agent.tools import state, tool_names


def _week(pid, name, pos, team, opp, week, targets=0, carries=0, air=0, attempts=0, pass_air=0, points=0.0,
          target_share=0.0, snap=0.8):
    return {"player_id": pid, "player_display_name": name, "position": pos, "season": 2026, "week": week,
            "season_type": "REG", "team": team, "opponent_team": opp, "targets": targets, "carries": carries,
            "receiving_air_yards": air, "attempts": attempts, "passing_air_yards": pass_air,
            "fantasy_points_ppr": points, "target_share": target_share, "receptions": targets * 0.65,
            "air_yards_share": 0.0, "wopr": 0.0}


def _stats():
    rows = []
    for w in (1, 2, 3):
        # A receiver whose targets climb every week while his points lag them.
        rows.append(_week("wr1", "Rising Receiver", "WR", "AAA", "BBB", w, targets=4 + 3 * w, air=40 + 20 * w,
                          points=6.0 + w, target_share=0.12 + 0.06 * w))
        # A steady back on the same team.
        rows.append(_week("rb1", "Steady Back", "RB", "AAA", "BBB", w, carries=18, targets=3, points=15.0,
                          target_share=0.08))
        rows.append(_week("wr2", "Other Receiver", "WR", "BBB", "AAA", w, targets=6, air=70, points=11.0,
                          target_share=0.2))
    return pd.DataFrame(rows)


def _games():
    return pd.DataFrame([
        {"season": 2026, "game_type": "REG", "week": 4, "away_team": "BBB", "home_team": "AAA",
         "spread_line": 3.0, "total_line": 47.0},
        {"season": 2026, "game_type": "REG", "week": 5, "away_team": "AAA", "home_team": "CCC",
         "spread_line": -1.0, "total_line": 44.0},
        # BBB has no week-5 game: a bye.
    ])


def test_prepare_weekly_computes_rush_share_and_opportunity_share():
    w = model.prepare_weekly(_stats())
    back = w[(w["player_id"] == "rb1") & (w["week"] == 1)].iloc[0]
    assert back["rush_share"] == pytest.approx(1.0)
    assert back["opp_share"] == pytest.approx(0.5 * 1.0 + 0.5 * 0.08)
    wr = w[(w["player_id"] == "wr1") & (w["week"] == 3)].iloc[0]
    assert wr["opp_share"] == pytest.approx(0.30)


def test_fit_falls_back_to_defaults_on_a_small_sample_and_xfp_is_never_negative():
    w = model.prepare_weekly(_stats())
    coefs = model.fit_xfp(w)
    assert coefs["WR"] == model.DEFAULT_COEFS["WR"]
    out = model.add_xfp(w, coefs)
    assert (out["xfp"] >= 0).all()
    assert out["fpoe"].equals(out["points"] - out["xfp"])


def test_fit_recovers_a_known_relationship():
    rows = [_week(f"p{i}", f"P{i}", "WR", "AAA", "BBB", 1, targets=t, air=a, points=0.5 + 1.5 * t + 0.02 * a)
            for i, (t, a) in enumerate((t, a) for t in range(1, 11) for a in (20, 60, 100, 140, 180))]
    w = model.prepare_weekly(pd.DataFrame(rows))
    c = model.fit_xfp(w, min_rows=20)["WR"]
    assert c["targets"] == pytest.approx(1.5, abs=1e-6) and c["receiving_air_yards"] == pytest.approx(0.02, abs=1e-6)
    assert c["r2"] == pytest.approx(1.0)


def test_summary_flags_the_growing_role_and_the_forecast_respects_byes():
    w = model.add_xfp(model.prepare_weekly(_stats()), dict(model.DEFAULT_COEFS))
    s = model.summarize(w)
    wr = s[s["player_id"] == "wr1"].iloc[0]
    assert wr["share_trend"] > 0.03 and "role growing" in model.signals(wr.to_dict())
    # Recency weighting puts the forecast base above the season average of xFP.
    assert wr["xfp_recent"] > wr["xfp_pg"]
    fc = model.forecast(s, w, _games(), target_week=4, end_week=5)
    by = fc.set_index("player_id")
    assert by.loc["wr1", "opponent"] == "BBB" and by.loc["wr1", "implied"] == 25.0
    # BBB is on bye in week 5, so its player has one game left; AAA has two.
    assert by.loc["wr2", "ros_games"] == 1 and by.loc["wr1", "ros_games"] == 2
    assert by.loc["wr1", "p10"] < by.loc["wr1", "mean"] < by.loc["wr1", "p90"]


def test_environment_scales_with_the_implied_total_and_is_zero_on_bye():
    sched = model.team_weeks(_games())
    hi, opp, implied = model.environment("AAA", "WR", 4, sched, {}, 23.5)
    lo, _, _ = model.environment("BBB", "WR", 4, sched, {}, 23.5)
    assert opp == "BBB" and implied == 25.0 and hi > 1 > lo
    assert model.environment("BBB", "WR", 5, sched, {}, 22.0)[0] == 0.0


def test_lognormal_range_is_skewed_right_around_the_mean():
    p10, p90 = model.lognormal_range(15.0, 0.6)
    assert p10 < 15.0 < p90 and (p90 - 15.0) > (15.0 - p10)
    assert model.lognormal_range(0, 0.6) == (0.0, 0.0)


def test_win_probability():
    assert model.win_probability([(100, 20)], [(100, 20)]) == pytest.approx(0.5)
    p = model.win_probability([(110, 15)], [(100, 20)])
    assert p == pytest.approx(0.5 * (1 + math.erf(10 / math.sqrt(2 * 625))))
    assert model.win_probability([(10, 0)], [(5, 0)]) == 1.0


def test_best_lineup_spends_flex_last():
    players = [
        {"name": "RB A", "position": "RB", "eligible": ["RB", "RB/WR/TE"], "value": 20},
        {"name": "RB B", "position": "RB", "eligible": ["RB", "RB/WR/TE"], "value": 9},
        {"name": "WR A", "position": "WR", "eligible": ["WR", "RB/WR/TE"], "value": 15},
        {"name": "WR B", "position": "WR", "eligible": ["WR", "RB/WR/TE"], "value": 14},
        {"name": "TE A", "position": "TE", "eligible": ["TE", "RB/WR/TE"], "value": 8},
    ]
    lineup = model.best_lineup(players, {"RB": 1, "WR": 1, "RB/WR/TE": 2})
    slots = {name: slot for slot, name in ((s, p["name"]) for s, p in lineup)}
    assert slots["RB A"] == "RB" and slots["WR A"] == "WR"
    assert {n for n, s in slots.items() if s == "RB/WR/TE"} == {"WR B", "RB B"}


def test_report_table_formats_percent_signed_and_missing():
    df = pd.DataFrame([{"name": "A | B", "share": 0.314, "edge": 2.0, "mean": None}])
    out = report.table(df, ["name", "share", "edge", "mean"], ["Player", "Share", "Diff", "Model"],
                       pct=("share",), signed=("edge",))
    assert "| A / B | 31% | +2.0 | – |" in out
    assert report.table(pd.DataFrame(), ["x"]).startswith("_None")
    assert report.clean("💯 U MAD Bro? 💯") == "U MAD Bro?"


def test_analytics_section_survives_a_research_rewrite():
    first = state.with_analytics("", "model says buy")
    assert state.ANALYTICS_START in first and "model says buy" in first
    research = "# Week 5\n\nconsensus notes"
    rewritten = state.keep_analytics(research, first)
    assert rewritten.startswith("# Week 5") and "model says buy" in rewritten
    # Re-running the analyst replaces its block instead of stacking another.
    again = state.with_analytics(rewritten, "model says sell")
    assert "model says sell" in again and "model says buy" not in again
    assert again.count(state.ANALYTICS_START) == 1
    # A rewrite that carries the block itself is left alone.
    assert state.keep_analytics(again, first) == again


def test_analytics_job_has_its_tools_and_no_writes_or_web():
    spec = jobs.get("analytics")
    assert not spec.writes and not spec.web and spec.owner_job
    names = [n for g in spec.tool_groups for n in tool_names(g)]
    assert "mcp__espn__build_analytics" in names and "mcp__espn__publish_analytics" in names
    assert not any("execute" in n or "preview" in n for n in names)
    # Every owner job can read the result; the public analyst cannot.
    assert "mcp__espn__read_analytics" in tool_names("read")
    assert "mcp__espn__read_analytics" not in tool_names("analyst")
    assert "read_analytics" in jobs.read_prompt("research.md")
