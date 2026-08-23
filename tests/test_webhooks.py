"""Tests for comma-separated Discord webhooks and fan-out posting."""
from unittest.mock import Mock, patch

import pytest

from gamedaybot.discord_bot.webhook import Discord, DiscordException, webhook_label
from gamedaybot.espn.env_vars import parse_webhook_urls


def test_parse_splits_commas_and_semicolons():
    urls = parse_webhook_urls(
        "https://discord.com/api/webhooks/1/aaa, https://discord.com/api/webhooks/2/bbb"
    )
    assert urls == [
        "https://discord.com/api/webhooks/1/aaa",
        "https://discord.com/api/webhooks/2/bbb",
    ]
    assert parse_webhook_urls(
        "https://a/webhook/1;https://a/webhook/2"
    ) == ["https://a/webhook/1", "https://a/webhook/2"]


def test_parse_drops_blanks_and_duplicates():
    assert parse_webhook_urls("  , https://a/w/1, https://a/w/1, ") == ["https://a/w/1"]
    assert parse_webhook_urls("") == []
    assert parse_webhook_urls("   ") == []


def test_webhook_label_is_the_id_prefix_not_the_token():
    url = "https://discord.com/api/webhooks/123456789012345678/super-secret-token"
    label = webhook_label(url)
    assert "super-secret-token" not in label
    assert label.startswith("12345678")
    assert "super-secret-token" not in repr(Discord(url))


def test_send_message_posts_to_every_url():
    urls = [
        "https://discord.com/api/webhooks/1/aaa",
        "https://discord.com/api/webhooks/2/bbb",
    ]
    ok = Mock(status_code=204)
    with patch("gamedaybot.discord_bot.webhook.requests.post", return_value=ok) as post:
        Discord(urls).send_message(text="hello")

    assert post.call_count == 2
    posted = [call.kwargs.get("url") or call.args[0] for call in post.call_args_list]
    assert posted == urls


def test_send_message_tries_the_second_url_when_the_first_fails():
    urls = [
        "https://discord.com/api/webhooks/1/aaa",
        "https://discord.com/api/webhooks/2/bbb",
    ]
    bad = Mock(status_code=500, content=b"nope")
    ok = Mock(status_code=204)

    with patch(
        "gamedaybot.discord_bot.webhook.requests.post", side_effect=[bad, ok]
    ) as post:
        with pytest.raises(DiscordException, match="failed"):
            Discord(urls).send_message(embed={"title": "hi"})

    assert post.call_count == 2
