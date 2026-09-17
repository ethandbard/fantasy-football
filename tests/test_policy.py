"""
The permission tiers, one case per rule in fantasy-football-agents/RULES.md.
"""
from datetime import datetime, timezone

from agent import policy, rules as rules_mod


def _rules(**over):
    r = dict(rules_mod.DEFAULTS)
    r.update(over)
    r["core_players_lower"] = {n.lower() for n in r["core_players"]}
    return r


def _p(pid, name, pos, slot=20):
    return {"player_id": pid, "name": name, "position": pos, "slot_id": slot}


ROSTER = [
    _p(1, "Jaxson Dart", "QB", 0), _p(2, "Jahmyr Gibbs", "RB", 2), _p(3, "Bucky Irving", "RB", 2),
    _p(4, "Zay Flowers", "WR", 4), _p(5, "Emeka Egbuka", "WR", 4), _p(6, "Harold Fannin Jr.", "TE", 6),
    _p(7, "Rashee Rice", "WR", 23), _p(8, "Aaron Jones Sr.", "RB", 23), _p(9, "Chiefs D/ST", "D/ST", 16),
    _p(10, "Cameron Dicker", "K", 17), _p(11, "Jayden Daniels", "QB"), _p(12, "Tucker Kraft", "TE"),
    _p(13, "Brian Thomas Jr.", "WR"), _p(14, "Devaughn Vele", "WR"), _p(15, "Caleb Douglas", "WR"),
    _p(16, "Kaelon Black", "RB"),
]


def test_lineup_is_auto():
    assert policy.classify_lineup([], ROSTER, _rules()).tier == policy.AUTO


def test_bench_drop_for_a_free_agent_is_auto():
    d = policy.classify_add_drop([_p(99, "Jalen Coker", "WR")], [_p(15, "Caleb Douglas", "WR")], ROSTER, _rules())
    assert d.tier == policy.AUTO


def test_dropping_a_starter_asks():
    d = policy.classify_add_drop([_p(99, "Jalen Coker", "WR")], [_p(6, "Harold Fannin Jr.", "TE", 6)], ROSTER, _rules())
    assert d.tier == policy.ASK and "current starter" in d.reasons[0]


def test_dropping_a_core_player_is_never():
    d = policy.classify_add_drop([], [_p(2, "Jahmyr Gibbs", "RB", 2)], ROSTER, _rules())
    assert d.tier == policy.NEVER


def test_core_match_is_case_insensitive():
    d = policy.classify_add_drop([], [_p(7, "rashee rice", "WR", 23)], ROSTER, _rules())
    assert d.tier == policy.NEVER


def test_third_qb_is_never_and_second_kicker_is_never():
    d = policy.classify_add_drop([_p(99, "Josh Allen", "QB")], [_p(15, "Caleb Douglas", "WR")], ROSTER, _rules())
    assert d.tier == policy.NEVER and "third QB" in d.reasons[0]
    d = policy.classify_add_drop([_p(98, "Jake Bates", "K")], [_p(15, "Caleb Douglas", "WR")], ROSTER, _rules())
    assert d.tier == policy.NEVER and "kicker" in d.reasons[0]


def test_leaving_no_kicker_or_no_dst_asks():
    d = policy.classify_add_drop([_p(99, "Jalen Coker", "WR")], [_p(10, "Cameron Dicker", "K", 17)], ROSTER, _rules())
    assert d.tier == policy.ASK and any("no kicker" in r for r in d.reasons)
    d = policy.classify_add_drop([_p(99, "Jalen Coker", "WR")], [_p(9, "Chiefs D/ST", "D/ST", 16)], ROSTER, _rules())
    assert d.tier == policy.ASK and any("no D/ST" in r for r in d.reasons)


def test_streaming_dst_swap_is_auto_when_one_stays():
    roster = ROSTER + [_p(17, "Jaguars D/ST", "D/ST")]
    d = policy.classify_add_drop([_p(99, "Bills D/ST", "D/ST")], [_p(17, "Jaguars D/ST", "D/ST")], roster, _rules())
    assert d.tier == policy.AUTO


def test_trade_proposals_ask_unless_core_or_deadline():
    d = policy.classify_trade_propose([_p(13, "Brian Thomas Jr.", "WR")], [_p(50, "Breece Hall", "RB")], ROSTER, _rules())
    assert d.tier == policy.ASK
    d = policy.classify_trade_propose([_p(2, "Jahmyr Gibbs", "RB", 2)], [_p(50, "Breece Hall", "RB")], ROSTER, _rules())
    assert d.tier == policy.NEVER
    late = datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc)
    d = policy.classify_trade_propose([_p(13, "Brian Thomas Jr.", "WR")], [_p(50, "Breece Hall", "RB")], ROSTER, _rules(), now=late)
    assert d.tier == policy.NEVER and "deadline" in d.reasons[0]


def test_trade_response_decline_auto_accept_asks():
    d = policy.classify_trade_respond(False, [_p(13, "Brian Thomas Jr.", "WR")], [], ROSTER, _rules())
    assert d.tier == policy.AUTO
    d = policy.classify_trade_respond(True, [_p(13, "Brian Thomas Jr.", "WR")], [], ROSTER, _rules())
    assert d.tier == policy.ASK
    d = policy.classify_trade_respond(True, [_p(3, "Bucky Irving", "RB", 2)], [], ROSTER, _rules())
    assert d.tier == policy.NEVER


def test_withdraw_only_agent_sent_proposals():
    assert policy.classify_withdraw("abc", {"abc"}, _rules()).tier == policy.AUTO
    assert policy.classify_withdraw("xyz", {"abc"}, _rules()).tier == policy.NEVER


def test_rules_json_override(tmp_path):
    (tmp_path / "rules.json").write_text('{"core_players": ["Someone Else"], "max_qb": 1}', encoding="utf-8")
    r = rules_mod.load(tmp_path)
    assert r["core_players"] == ["Someone Else"] and r["max_qb"] == 1
    assert "someone else" in r["core_players_lower"]
    assert r["max_k"] == 1
