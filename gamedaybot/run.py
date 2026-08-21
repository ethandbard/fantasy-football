"""
Container entrypoint: runs the Shiny dashboard server in a background thread
and the existing bot scheduler in the foreground (the scheduler blocks
forever, so it owns the main thread).
"""
import logging
import os
import subprocess
import sys
import threading

sys.path.insert(1, os.path.abspath('.'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

from gamedaybot.espn.espn_bot import espn_bot
from gamedaybot.espn.scheduler import scheduler


def run_web():
    port = os.environ.get("DASHBOARD_PORT", "8000")
    app_path = os.path.join(os.path.dirname(__file__), "web", "app.py")
    # shiny.express apps are meant to be launched via the `shiny run` CLI,
    # which knows how to detect and wrap express-style modules.
    subprocess.run(
        [sys.executable, "-m", "shiny", "run", app_path,
         "--host", "0.0.0.0", "--port", port],
        check=False,
    )


def run_discord_bot():
    from gamedaybot.discord_bot.bot import run as run_bot
    token = os.environ["DISCORD_BOT_TOKEN"]
    port = os.environ.get("DASHBOARD_PORT", "8000")
    dashboard_url = os.environ.get("DASHBOARD_URL", f"http://localhost:{port}")
    run_bot(token, dashboard_url)


if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    if os.environ.get("DISCORD_BOT_TOKEN"):
        bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
        bot_thread.start()
    else:
        logger.info("DISCORD_BOT_TOKEN not set -- slash commands disabled, "
                     "only scheduled webhook messages will be sent")

    espn_bot("init")
    scheduler()
