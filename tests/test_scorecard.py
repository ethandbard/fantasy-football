"""
The scorecard's pure parts: bench points left per week, what the players
in a proposal scored afterward, and the model-versus-ESPN forecast errors.
"""
import json

from agent import scorecard


def _row(week, pid, name, slot, points, eligible, position="WR"):
    return {"year": 2026, "week": week, "team_id": 11, "player_id": pid, "player_name": name, "slot": slot,
            "points": points, "eligible_slots": eligible, "position": position}


def test_bench_points_compares_the_lineup_with_the_best_one():
    rows = [
        _row(6, 1, "Starter", "WR", 5.0, ["WR", "RB/WR/TE", "BE"]),
        _row(6, 2, "Flex", "RB/WR/TE", 6.0, ["RB", "RB/WR/TE", "BE"], "RB"),
        _row(6, 3, "Benched", "BE", 17.8, ["WR", "RB/WR/TE", "BE"]),
        _row(6, 4, "IR guy", "IR", 0.0, ["WR", "BE", "IR"]),
    ]
    out = scorecard.bench_points(rows)
    assert out == [{"week": 6, "started": 11.0, "best": 23.8, "left": 12.8}]


def test_trade_outcomes_sum_points_after_the_decision():
    points = {10: {5: 20.0, 6: 30.0, 7: 10.0}, 20: {5: 9.0, 6: 2.0, 7: 3.0}}
    payload = json.dumps({"items": [{"playerId": 10, "type": "TRADE", "fromTeamId": 11, "toTeamId": 9},
                                    {"playerId": 20, "type": "TRADE", "fromTeamId": 9, "toTeamId": 11}]})
    tx = [{"kind": "trade_propose", "ok": 1, "dry_run": 0, "at": "2026-10-20T12:00:00", "payload": payload,
           "outcome": "declined", "description": "offer A for B"},
          {"kind": "trade_decline", "ok": 1, "dry_run": 0, "at": "2026-10-21T12:00:00", "payload": json.dumps({"items": []}),
           "outcome": None, "description": "decline offer from X"},
          {"kind": "waiver", "ok": 1, "dry_run": 0, "at": "2026-10-21T12:00:00", "payload": "{}"}]
    out = scorecard.trade_outcomes(tx, points, lambda at: 5, {10: "A", 20: "B"}, 11)
    assert out[0]["gave"] == 40.0 and out[0]["got"] == 5.0 and out[0]["give_names"] == ["A"]
    assert out[1]["gave"] is None and out[1]["description"] == "decline offer from X"
    assert len(out) == 2


def test_forecast_errors_score_model_and_espn_on_actuals():
    tables = {6: [{"player_id": 1, "modelled": True, "mean": 12.0, "espn_proj": 15.0},
                  {"player_id": 2, "modelled": True, "mean": 8.0, "espn_proj": 9.0},
                  {"player_id": 3, "modelled": False, "mean": None, "espn_proj": 7.0},
                  {"player_id": 4, "modelled": True, "mean": 5.0, "espn_proj": 6.0}]}
    points = {1: {6: 11.0}, 2: {6: 10.0}, 3: {6: 7.0}}
    out = scorecard.forecast_errors(tables, points)
    assert out == {6: {"n": 2, "model_mae": 1.5, "espn_mae": 2.5, "model_closer": 1}}


def test_render_has_every_section_and_handles_empty_inputs():
    text = scorecard.render(7, [], [], [], {}, "John Foot Ball")
    for heading in ("# Scorecard through week 7", "## Bench points left", "## Claims and adds", "## Trades", "## Forecasts"):
        assert heading in text
    text = scorecard.render(7, [{"week": 6, "started": 10, "best": 20, "left": 10}],
                            [{"at": "2026-10-20", "description": "add X", "landed": True}],
                            [{"at": "2026-10-20", "kind": "trade_propose", "outcome": "declined", "gave": 40.0, "got": 5.0,
                              "give_names": ["A"], "get_names": ["B"], "description": "x", "week": 5}],
                            {6: {"n": 2, "model_mae": 1.5, "espn_mae": 2.5, "model_closer": 1}}, "Me")
    assert "| 6 | 10 | 20 | 10 |" in text and "add X: landed" in text and "give A (40.0 pts since)" in text
    assert "| 6 | 2 | 1.5 | 2.5 | 1 of 2 |" in text
