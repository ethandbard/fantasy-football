"""IR activation planning: moving a healthy player out of the IR slot.

Covers the week-5 failure where Jayden Daniels sat healthy in IR while the
roster was full: ESPN rejects a lone IR->active move (TRAN_ROSTER_LIMIT_EXCEEDED_ONE),
so the safe sequence is drop-first-then-move, which plan_activate plans.
"""
import gamedaybot.espn.roster as roster
from agent.tools.write import plan_activate

IR, BE, QB, WR = 21, 20, 0, 4
DANIELS, SHOUGH, BTJ = 4426348, 4360689, 4432773
COUNTS = {0: 1, 2: 2, 4: 2, 6: 1, 16: 1, 23: 1}


def _entry(name, pid, slot, position="QB", eligible=None, injury="ACTIVE", locked=False):
    return roster.RosterEntry(
        player_id=pid, name=name, position=position, pro_team="WSH", slot_id=slot,
        slot=roster.slot_name(slot), eligible_slot_ids=eligible or [0, 20, 21],
        eligible_slots=["QB", "BE", "IR"], injury_status=injury,
        injured=injury not in ("ACTIVE",),
        lineup_locked=False, roster_locked=locked, trade_locked=False, droppable=True,
    )


def _full_roster():
    """16 non-IR players (a full roster) plus Daniels healthy in IR."""
    entries = [_entry("Shough", SHOUGH, QB)]
    for i in range(15):
        entries.append(_entry(f"Bench{i}", 100 + i, BE, position="WR", eligible=[4, 20, 21]))
    entries.append(_entry("Daniels", DANIELS, IR, injury="ACTIVE"))
    return entries


def test_full_roster_plans_drop_then_move_with_extra_benching():
    # The Oct 9 scenario: Daniels IR->QB, Shough QB->BE, drop BTJ-equivalent first.
    entries = _full_roster()
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert problems == []
    assert plan["need_drop"] is True
    assert plan["drop_ids"] == [100]
    assert plan["to_slot_id"] == QB
    assert "Daniels" in plan["move_desc"] and "Shough" in plan["move_desc"]


def test_roster_with_room_needs_no_drop():
    entries = _full_roster()[:15]  # 15 non-IR players: one open spot
    entries.append(_entry("Daniels", DANIELS, IR, injury="ACTIVE"))
    plan, problems = plan_activate(entries, DANIELS, QB, [], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert problems == []
    assert plan["need_drop"] is False
    assert plan["drop_ids"] == []


def test_full_roster_without_drop_ids_is_a_problem():
    plan, problems = plan_activate(_full_roster(), DANIELS, QB, [], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("drop" in p for p in problems)


def test_occupied_target_slot_without_extra_move_is_a_problem():
    # Shough still holds QB: the move alone would double-fill the slot.
    plan, problems = plan_activate(_full_roster(), DANIELS, QB, [100], COUNTS)
    assert plan is None
    assert any("QB" in p for p in problems)


def test_player_not_in_ir_is_a_problem():
    plan, problems = plan_activate(_full_roster(), SHOUGH, BE, [], COUNTS)
    assert plan is None
    assert any("IR slot" in p for p in problems)


def test_unknown_player_is_a_problem():
    plan, problems = plan_activate(_full_roster(), 999999, QB, [], COUNTS)
    assert plan is None


def test_cannot_drop_the_activated_player():
    plan, problems = plan_activate(_full_roster(), DANIELS, QB, [DANIELS], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None


def test_locked_drop_candidate_is_a_problem():
    entries = _full_roster()
    entries[1] = _entry("Bench0", 100, BE, position="WR", eligible=[4, 20, 21], locked=True)
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("locked" in p for p in problems)


def test_ineligible_target_slot_is_a_problem():
    plan, problems = plan_activate(_full_roster(), DANIELS, 2, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert plan is None
    assert any("eligible" in p for p in problems)


def test_target_cannot_be_ir():
    plan, problems = plan_activate(_full_roster(), DANIELS, IR, [100], COUNTS)
    assert plan is None


def test_preexisting_ir_problem_does_not_block():
    # Another healthy player stuck in IR is a pre-existing problem, not ours.
    entries = _full_roster()
    entries.append(_entry("Other", 999, IR, injury="ACTIVE"))
    plan, problems = plan_activate(entries, DANIELS, QB, [100], COUNTS,
                                   extra_moves=[(SHOUGH, BE)])
    assert problems == []
    assert plan["need_drop"] is True
