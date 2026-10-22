"""
The trade evaluator: values by week and view, best lineups, the season
simulation, change application with forced drops, and the verdict line.
A small two-team league with hand-set forecasts, so every number can be
checked by hand.
"""
import pytest

from agent import evaluate
from agent.chart import Chart

SLOTS = {"QB": 1, "RB": 1, "WR": 1, "RB/WR/TE": 1}


def _p(pid, name, pos, team_id, by_week, eligible=None, espn_avg=None, pro_team="DET", **kw):
    return evaluate.Player(player_id=pid, name=name, position=pos, pro_team=pro_team,
                           eligible=eligible or ([pos, "RB/WR/TE"] if pos in ("RB", "WR", "TE") else [pos]),
                           team_id=team_id, modelled=bool(by_week), mean=(by_week or {}).get(8),
                           by_week=dict(by_week or {}), espn_avg=espn_avg, espn_proj=espn_avg, **kw)


def _state(chart=None):
    weeks = {8: 10.0, 9: 10.0, 10: 10.0}
    players = {
        1: _p(1, "QB One", "QB", 1, {8: 20, 9: 20, 10: 20}),
        2: _p(2, "Back One", "RB", 1, {8: 15, 9: 0, 10: 15}, espn_avg=12, pro_team="TB"),   # bye week 9
        3: _p(3, "Wide One", "WR", 1, {8: 12, 9: 12, 10: 12}, espn_avg=16),                 # market loves him
        4: _p(4, "Flex One", "WR", 1, {8: 8, 9: 8, 10: 8}, espn_avg=8),
        5: _p(5, "Bench One", "RB", 1, {8: 6, 9: 6, 10: 6}, espn_avg=6),
        11: _p(11, "QB Two", "QB", 2, {8: 18, 9: 18, 10: 18}),
        12: _p(12, "Back Two", "RB", 2, {8: 14, 9: 14, 10: 14}, espn_avg=9),                 # usage ahead of production
        13: _p(13, "Wide Two", "WR", 2, {8: 9, 9: 9, 10: 9}, espn_avg=9),
        14: _p(14, "Flex Two", "RB", 2, {8: 7, 9: 7, 10: 7}, espn_avg=7),
        15: _p(15, "Hurt Two", "WR", 2, {8: 11, 9: 11, 10: 11}, espn_avg=11, injury="OUT"),
        99: _p(99, "Free Agent", "WR", None, weeks, espn_avg=9),
    }
    teams = {
        1: evaluate.Team(1, "Mine", wins=3, losses=4, points_for=700, roster=[1, 2, 3, 4, 5]),
        2: evaluate.Team(2, "Theirs", wins=4, losses=3, points_for=720, roster=[11, 12, 13, 14, 15]),
    }
    return evaluate.State(week=8, reg_weeks=10, playoff_teams=1, slot_counts=SLOTS, teams=teams, players=players,
                          games=[(8, 1, 2), (9, 1, 2), (10, 1, 2)], history={1: [100, 110], 2: [120, 130]}, me=1,
                          cvs={"QB": 0.4, "RB": 0.5, "WR": 0.6}, team_byes={"TB": [9]}, chart=chart, data_through=7)


def test_value_by_week_view_bye_and_injury():
    st = _state()
    back = st.players[2]
    assert evaluate.value(st, back, 8) == 15 and evaluate.value(st, back, 9) == 0
    assert evaluate.value(st, back, 8, "espn") == 12 and evaluate.value(st, back, 9, "espn") == 0
    hurt = st.players[15]
    assert evaluate.value(st, hurt, 8) == 0 and evaluate.value(st, hurt, 9) == 5.5 and evaluate.value(st, hurt, 10) == 11
    unmodelled = evaluate.Player(50, "Kicker", "K", "DET", ["K"], 1, espn_proj=9, espn_avg=7)
    assert evaluate.value(st, unmodelled, 8) == 9 and evaluate.value(st, unmodelled, 9) == 7


def test_best_lineup_and_team_week_fill_the_flex_last():
    st = _state()
    mean, sd, lineup = evaluate.team_week(st, st.teams[1].roster, 8)
    picked = {slot: p.name for slot, p, _ in lineup}
    assert picked == {"QB": "QB One", "RB": "Back One", "WR": "Wide One", "RB/WR/TE": "Flex One"}
    assert mean == 55 and sd > 0
    # On the bye the bench back steps into the flex.
    _, _, lineup9 = evaluate.team_week(st, st.teams[1].roster, 9)
    assert {p.name for _, p, _ in lineup9} == {"QB One", "Wide One", "Flex One", "Bench One"}


def test_apply_changes_moves_players_and_forces_drops_over_the_limit():
    st = _state()
    rosters, forced = evaluate.apply_changes(st, [{"type": "trade", "other_team_id": 2, "give": [4], "get": [12]}])
    assert 12 in rosters[1] and 4 not in rosters[1] and 4 in rosters[2] and 12 not in rosters[2]
    assert forced == {}
    big = {pid: _p(pid, f"P{pid}", "WR", 1, {8: 5, 9: 5, 10: 5}) for pid in range(100, 117)}
    st.players.update(big)
    st.teams[1].roster = list(big)
    rosters, forced = evaluate.apply_changes(st, [{"type": "add", "player_id": 99, "drop": []}])
    assert len(rosters[1]) == evaluate.ROSTER_LIMIT and len(forced[1]) == 2


def test_simulation_is_deterministic_and_a_better_roster_raises_the_odds():
    st = _state()
    before = evaluate.simulate(st, sims=800, seed=1)
    again = evaluate.simulate(st, sims=800, seed=1)
    assert before == again
    assert abs(before[1]["playoff_odds"] + before[2]["playoff_odds"] - 1.0) < 1e-9
    b, a, forced = evaluate.playoff_odds(st, [{"type": "trade", "other_team_id": 2, "give": [4], "get": [12]}], sims=800)
    assert a[1]["playoff_odds"] > b[1]["playoff_odds"]
    assert a[2]["playoff_odds"] < b[2]["playoff_odds"]


def test_evaluate_trade_reports_both_views_the_chart_and_a_verdict():
    chart = Chart({"week": 8, "players": [{"name": "Flex One", "team": "DET", "position": "WR", "value": 10, "bye": 7},
                                          {"name": "Back Two", "team": "DET", "position": "RB", "value": 12, "bye": 7}]})
    st = _state(chart)
    ev = evaluate.evaluate_trade(st, 2, [4], [12], sims=800, ledger="[open] nothing yet")
    assert ev["give"] == ["Flex One"] and ev["get"] == ["Back Two"]
    assert ev["odds"]["mine_delta"] > 0
    # Model view: Back Two (14) replaces Flex One (8) in my flex, +6 a week.
    assert ev["mine"]["model"]["per_week"] == pytest.approx(6.0)
    # ESPN view for him: he loses a 9-avg back and gains an 8-avg receiver who starts at flex
    # (-1 in weeks 8 and 9), and in week 10 his returning receiver pushes the new one to the bench (-2).
    assert ev["theirs"]["espn"]["per_week"] == pytest.approx(-4 / 3, abs=0.01)
    assert ev["chart"]["i_give"]["total"] == 10 and ev["chart"]["i_get"]["total"] == 12
    assert "Back Two" in ev["fit"]["i_start"]
    assert ev["verdict"].startswith("good for me") and "his own view" in ev["verdict"]
    text = evaluate.render_trade(ev)
    assert "VERDICT" in text and "Chart (week 8)" in text and "Thread with this manager" in text
    assert "error" in evaluate.evaluate_trade(st, 2, [12], [4])
    assert "cannot evaluate" in evaluate.render_trade(evaluate.evaluate_trade(st, 2, [12], [4]))


def test_verdict_says_send_when_both_sides_gain_in_their_own_view():
    st = _state()
    # He gives the hurt receiver (ESPN 11, worth 0 to him now) for my bench back (ESPN 6): his lineup in
    # ESPN's view loses nothing this week, and the model says the receiver is worth 11 from week 10.
    ev = evaluate.evaluate_trade(st, 2, [5], [15], sims=800)
    assert ev["odds"]["mine_delta"] >= 0
    assert ev["verdict"].startswith(("send it", "the arithmetic says no", "good for me"))


def test_market_map_names_weak_slots_surplus_and_byes():
    st = _state()
    rows = evaluate.market_map(st, negotiations={2: "[open] asked for Back Two"})
    mine = next(r for r in rows if r["team_id"] == 1)
    theirs = next(r for r in rows if r["team_id"] == 2)
    assert mine["byes_ahead"] == {9: ["Back One"]}
    assert mine["open_spots"] == evaluate.ROSTER_LIMIT - 5
    assert theirs["thread"] == "[open] asked for Back Two"
    assert {s["slot"] for s in mine["weak_slots"]} <= set(SLOTS)
    text = evaluate.render_market(rows)
    assert "Mine (team 1)" in text and "thread: [open] asked for Back Two" in text


def test_render_odds_marks_my_team_and_shows_the_change():
    st = _state()
    before, after, forced = evaluate.playoff_odds(st, [{"type": "drop", "drop": [3]}], sims=400)
    text = evaluate.render_odds(st, before, after, forced)
    assert text.splitlines()[0].startswith("Playoff odds over weeks 8-10")
    assert "* Mine" in text and "->" in text
