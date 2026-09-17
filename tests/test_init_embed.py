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


def test_startup_message_can_be_routed_to_its_own_webhook(monkeypatch):
    """INIT_WEBHOOK_URL keeps restart notices out of the league channel."""
    import gamedaybot.espn.espn_bot as espn_bot

    sent = []

    class FakeDiscord:
        def __init__(self, urls):
            self.urls = list(urls) if not isinstance(urls, str) else [urls]

        def send_message(self, text=None, embed=None):
            sent.append((tuple(self.urls), text))

    monkeypatch.setattr(espn_bot, "Discord", FakeDiscord)
    league_hook = FakeDiscord(["https://discord.com/api/webhooks/1/league", "https://discord.com/api/webhooks/2/test"])
    espn_bot._send_init(league_hook, {"init_msg": "hi"}, league=None)
    assert sent[-1][0] == ("https://discord.com/api/webhooks/1/league", "https://discord.com/api/webhooks/2/test")
    espn_bot._send_init(league_hook, {"init_msg": "hi", "init_webhook_urls": ["https://discord.com/api/webhooks/2/test"]}, league=None)
    assert sent[-1][0] == ("https://discord.com/api/webhooks/2/test",)


def test_init_webhook_env_is_parsed(monkeypatch):
    from gamedaybot.espn.env_vars import get_env_vars
    monkeypatch.setenv("LEAGUE_ID", "1")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/a")
    monkeypatch.delenv("INIT_WEBHOOK_URL", raising=False)
    assert get_env_vars()["init_webhook_urls"] is None
    monkeypatch.setenv("INIT_WEBHOOK_URL", "https://discord.com/api/webhooks/2/b")
    assert get_env_vars()["init_webhook_urls"] == ["https://discord.com/api/webhooks/2/b"]
