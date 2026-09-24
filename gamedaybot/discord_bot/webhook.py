"""
Posts scheduled reports to every URL in DISCORD_WEBHOOK_URL. Separate from
gamedaybot.discord_bot.bot, which holds a live gateway connection to serve
slash commands; both render through gamedaybot.discord_bot.formatting.
"""
import json
import logging
import time

import requests

from gamedaybot.espn.env_vars import parse_webhook_urls

logger = logging.getLogger(__name__)


class DiscordException(Exception):
    pass


def _retry_after(response, default=2.0, cap=30.0):
    """Seconds to wait after a 429, from the Retry-After header."""
    try:
        return min(float(response.headers.get("Retry-After", default)), cap)
    except (TypeError, ValueError, AttributeError):
        return default


def webhook_label(url):
    """Identify a webhook in logs by id prefix, never the token."""
    marker = "/webhooks/"
    if marker not in url:
        return "webhook"
    webhook_id = url.split(marker, 1)[1].split("/", 1)[0]
    if not webhook_id:
        return "webhook"
    return webhook_id[:8] + "…"


class Discord(object):
    """
    Send a message to one or more Discord webhooks.

    Parameters
    ----------
    webhook_url : str or list of str
        One webhook URL, a comma-separated list, or a sequence of URLs.
        Each scheduled post is sent to every URL.
    """

    def __init__(self, webhook_url):
        if isinstance(webhook_url, str):
            urls = parse_webhook_urls(webhook_url)
        else:
            urls = list(webhook_url)
        if not urls:
            raise DiscordException("No Discord webhook URL provided")
        self.webhook_urls = urls
        self.webhook_url = urls[0]

    def __repr__(self):
        labels = ", ".join(webhook_label(url) for url in self.webhook_urls)
        return f"Discord({labels})"

    def send_message(self, text=None, embed=None):
        """
        Send a message to every configured webhook, either as plain
        code-block-wrapped text or as a rich embed (see
        gamedaybot.discord_bot.formatting for embed payload builders).

        Each URL is attempted even if an earlier one failed, so one dead
        channel does not strand the rest. Raises if any POST failed.

        Parameters
        ----------
        text : str, optional
            The message to send. Ignored if `embed` is given.
        embed : dict, optional
            A Discord embed object (plain JSON-serializable dict) to send
            instead of plain text.

        Returns
        -------
        r : requests.Response
            The response from the last successful POST, or None if there
            was nothing to send.

        Raises
        ------
        DiscordException
            If any POST is not a 204.
        """

        template = {}
        if embed is not None:
            template["embeds"] = [embed]
        elif text is not None:
            template["content"] = "```{0}```".format(text)
        else:
            return None
        return self._post(template)

    def send_poll(self, poll, content=None):
        """
        Send a native Discord poll to every configured webhook.

        Parameters
        ----------
        poll : dict
            A poll create request object (see
            gamedaybot.discord_bot.formatting.matchup_poll).
        content : str, optional
            Plain message text shown above the poll.

        Returns
        -------
        r : requests.Response
            The response from the last successful POST.

        Raises
        ------
        DiscordException
            If any POST is not a 204.
        """
        template = {"poll": poll}
        if content:
            template["content"] = content
        return self._post(template)

    def _post(self, template):
        """
        POST one message body to every webhook URL. Each URL is attempted
        even if an earlier one failed, so one dead channel does not strand
        the rest; a 429 is retried once after Discord's Retry-After.
        """
        headers = {"content-type": "application/json"}
        payload = json.dumps(template)
        last_ok = None
        failures = []
        for url in self.webhook_urls:
            r = requests.post(url, data=payload, headers=headers)
            if r.status_code == 429:
                # Several polls go out back to back; the webhook limit is
                # a handful of posts per few seconds.
                wait = _retry_after(r)
                logger.info("Webhook %s rate limited, retrying in %.1fs",
                            webhook_label(url), wait)
                time.sleep(wait)
                r = requests.post(url, data=payload, headers=headers)
            if r.status_code == 204:
                last_ok = r
                continue
            label = webhook_label(url)
            logger.error("Webhook %s returned HTTP %s", label, r.status_code)
            failures.append(label)

        if failures:
            raise DiscordException(
                "Webhook POST failed for " + ", ".join(failures)
            )
        return last_ok
