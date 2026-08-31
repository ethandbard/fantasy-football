"""
Tests for the trade pipeline's decision logic: flattening ESPN's paired
TRADE_SENT/TRADE_RECEIVED actions into player rows, only ever announcing a
trade once, and rendering one trade as one embed.
"""
import importlib
from types import SimpleNamespace

import pytest

import gamedaybot.discord_bot.formatting as fmt
import gamedaybot.espn.collector as collector
import gamedaybot.storage.db as db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    importlib.reload(db)
    db.init_db()
    yield db
    importlib.reload(db)


def _team(team_id, name):
    return SimpleNamespace(team_id=team_id, team_name=name)


def _player(player_id, name, position):
    return SimpleNamespace(playerId=player_id, name=name, position=position)


def _activity(date_ms, moves):
    """moves: list of (from_team, to_team, player)."""
    actions = []
    for from_team, to_team, player in moves:
        actions.append((from_team, "TRADE_SENT", player, 0))
        actions.append((to_team, "TRADE_RECEIVED", player, 0))
    return SimpleNamespace(date=date_ms, actions=actions)


def _two_player_trade(date_ms=1_700_000_000_000):
    a, b = _team(1, "Team A"), _team(2, "Team B")
    return _activity(date_ms, [
        (a, b, _player(10, "Saquon Barkley", "RB")),
        (b, a, _player(20, "CeeDee Lamb", "WR")),
    ])


def test_activity_pairs_sent_and_received_per_player():
    rows = collector._trade_rows_from_activity(_two_player_trade(), 2026)

    assert len(rows) == 2
    barkley = next(r for r in rows if r["player_id"] == 10)
    assert barkley["from_team_name"] == "Team A"
    assert barkley["to_team_name"] == "Team B"
    assert barkley["position"] == "RB"
    assert barkley["trade_date"] == 1_700_000_000_000


def test_unresolvable_player_id_still_produces_a_row():
    """espn_api hands back the raw target id when the player is unknown."""
    a, b = _team(1, "Team A"), _team(2, "Team B")
    rows = collector._trade_rows_from_activity(_activity(1, [(a, b, 12345)]), 2026)

    assert len(rows) == 1
    assert rows[0]["player_id"] == 12345
    assert rows[0]["player_name"] == "12345"
    assert rows[0]["position"] is None


def test_insert_new_trades_returns_only_unseen_rows(fresh_db):
    rows = collector._trade_rows_from_activity(_two_player_trade(), 2026)

    assert len(fresh_db.insert_new_trades(rows)) == 2
    # ESPN re-serves the same activity on every poll -- second pass is silent.
    assert fresh_db.insert_new_trades(rows) == []

    later = collector._trade_rows_from_activity(
        _two_player_trade(date_ms=1_700_000_999_000), 2026)
    assert len(fresh_db.insert_new_trades(rows + later)) == 2


def test_get_all_trades_returns_newest_trade_first(fresh_db):
    old = collector._trade_rows_from_activity(_two_player_trade(1_000), 2026)
    new = collector._trade_rows_from_activity(_two_player_trade(2_000), 2026)
    fresh_db.insert_new_trades(old + new)

    stored = fresh_db.get_all_trades()
    assert [r["trade_date"] for r in stored] == [2_000, 2_000, 1_000, 1_000]


def test_trade_embed_has_one_receives_field_per_team():
    rows = collector._trade_rows_from_activity(_two_player_trade(), 2026)
    embed = fmt.trade_embed(rows)

    names = {f["name"] for f in embed["fields"]}
    assert names == {"📥 Team A receives", "📥 Team B receives"}
    b_side = next(f for f in embed["fields"] if "Team B" in f["name"])
    assert b_side["value"] == "RB Saquon Barkley"
    assert "Team A" in embed["description"] and "Team B" in embed["description"]
