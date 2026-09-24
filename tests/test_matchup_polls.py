"""Tests for the Thursday matchup polls: payload shape, limits, and dispatch."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gamedaybot.discord_bot.formatting as fmt
from gamedaybot.discord_bot.webhook import Discord
from gamedaybot.espn.espn_bot import send_matchup_polls


def _team(name):
    return SimpleNamespace(team_name=name, team_abbrev=name[:4].upper(), wins=1, losses=0)


def _box(home, away):
    return SimpleNamespace(home_team=home, away_team=away)


def test_matchup_poll_is_a_two_answer_single_choice_poll():
    poll = fmt.matchup_poll("Bard Bunch", "Gridiron Goblins", 65, week=4)
    assert poll["question"]["text"] == "Week 4: Bard Bunch vs Gridiron Goblins"
    assert [a["poll_media"]["text"] for a in poll["answers"]] == ["Bard Bunch", "Gridiron Goblins"]
    assert poll["duration"] == 65
    assert poll["allow_multiselect"] is False
    assert poll["layout_type"] == 1
    json.dumps(poll)


def test_matchup_poll_clips_long_names_and_clamps_duration():
    long_name = "x" * 80
    poll = fmt.matchup_poll(long_name, "Short", 5000)
    assert len(poll["answers"][0]["poll_media"]["text"]) == fmt.POLL_ANSWER_MAX
    assert poll["answers"][0]["poll_media"]["text"].endswith("…")
    assert poll["duration"] == fmt.POLL_HOURS_MAX
    assert fmt.matchup_poll("A", "B", 0)["duration"] == fmt.POLL_HOURS_MIN


def test_send_poll_posts_the_poll_field_to_every_url():
    urls = ["https://discord.com/api/webhooks/1/aaa", "https://discord.com/api/webhooks/2/bbb"]
    ok = Mock(status_code=204)
    poll = fmt.matchup_poll("A", "B", 65)
    with patch("gamedaybot.discord_bot.webhook.requests.post", return_value=ok) as post:
        Discord(urls).send_poll(poll, content="Who wins?")

    assert post.call_count == 2
    body = json.loads(post.call_args_list[0].kwargs["data"])
    assert body["poll"] == poll
    assert body["content"] == "Who wins?"
    assert "embeds" not in body


def test_send_poll_retries_once_after_a_rate_limit():
    limited = Mock(status_code=429, headers={"Retry-After": "0.5"})
    ok = Mock(status_code=204)
    with patch("gamedaybot.discord_bot.webhook.requests.post", side_effect=[limited, ok]) as post, \
            patch("gamedaybot.discord_bot.webhook.time.sleep") as sleep:
        Discord("https://discord.com/api/webhooks/1/aaa").send_poll(fmt.matchup_poll("A", "B", 1))

    assert post.call_count == 2
    sleep.assert_called_once_with(0.5)


def test_send_matchup_polls_posts_one_poll_per_matchup_and_skips_byes():
    boxes = [
        _box(_team("Alpha"), _team("Bravo")),
        _box(_team("Charlie"), None),          # bye week in the playoffs
        _box(_team("Delta"), _team("Echo")),
    ]
    league = SimpleNamespace(current_week=7, box_scores=lambda week: boxes)
    discord_bot = Mock()

    assert send_matchup_polls(discord_bot, league, 65) == 2

    questions = [c.args[0]["question"]["text"] for c in discord_bot.send_poll.call_args_list]
    assert questions == ["Week 7: Alpha vs Bravo", "Week 7: Delta vs Echo"]
    assert all(c.args[0]["duration"] == 65 for c in discord_bot.send_poll.call_args_list)


def test_matchup_poll_settings_come_from_the_environment(monkeypatch):
    from gamedaybot.espn.env_vars import get_env_vars
    monkeypatch.setenv("LEAGUE_ID", "1")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/aaa")
    monkeypatch.delenv("MATCHUP_POLLS", raising=False)
    monkeypatch.delenv("MATCHUP_POLL_HOURS", raising=False)
    data = get_env_vars()
    assert data["matchup_polls"] is True
    assert data["matchup_poll_hours"] == 65

    monkeypatch.setenv("MATCHUP_POLLS", "false")
    monkeypatch.setenv("MATCHUP_POLL_HOURS", "24")
    data = get_env_vars()
    assert data["matchup_polls"] is False
    assert data["matchup_poll_hours"] == 24
