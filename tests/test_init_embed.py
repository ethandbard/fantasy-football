"""Startup embed must name the connected season."""
from types import SimpleNamespace

from gamedaybot.discord_bot.formatting import init_embed


def _data(**overrides):
    data = {
        "year": 2025,
        "ff_start_date": "2026-09-04",
        "ff_end_date": "2027-01-05",
        "my_timezone": "America/New_York",
        "daily_waiver": False,
        "monitor_report": True,
    }
    data.update(overrides)
    return data


def test_init_embed_uses_connected_league_year():
    league = SimpleNamespace(year=2026, settings=SimpleNamespace(name="Forehead fantasy football"))
    embed = init_embed(_data(year=2025), league=league)
    body = embed["description"]
    assert "2026 season" in body
    assert "Ready for the 2026 fantasy season!" in body
    assert "2025 season" not in body
    assert "Forehead fantasy football" in body


def test_init_embed_falls_back_to_env_year():
    body = init_embed(_data(year=2026))["description"]
    assert "2026 season" in body
