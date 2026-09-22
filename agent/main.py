"""
Container entrypoint for the agent service: storage, seed files, the job
queue, the clock, and the HTTP server, all on one asyncio loop.
"""
import asyncio
import logging
import os
import shutil
import sys

sys.path.insert(1, os.path.abspath("."))

from aiohttp import web

from agent import config, server, store
from agent.queue import JobQueue
from agent.scheduler import Clock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent")


def seed_files(cfg):
    """First start: copy the season log and rules into the data directory."""
    cfg.ensure_dirs()
    log_src = cfg.repo_agents_dir / "fantasy-season-log.md"
    log_dst = cfg.data_dir / "season-log.md"
    if not log_dst.exists() and log_src.exists():
        shutil.copyfile(log_src, log_dst)
        logger.info("seeded season log from %s", log_src)
    rules_src = cfg.repo_agents_dir / "RULES.md"
    rules_dst = cfg.data_dir / "RULES.md"
    if not rules_dst.exists() and rules_src.exists():
        shutil.copyfile(rules_src, rules_dst)
        logger.info("seeded RULES.md into %s", cfg.data_dir)
    elif rules_src.exists() and rules_dst.exists():
        # The data copy wins and is never overwritten, so a shipped change is
        # invisible until someone copies it over. Say so rather than stay quiet.
        shipped = rules_src.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        live = rules_dst.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        if shipped != live:
            logger.warning("%s differs from the shipped RULES.md; the agents read the data copy. "
                           "Copy the shipped file over it to pick up the new rules, or keep your edits.", rules_dst)


async def main():
    cfg = config.from_env()
    store.init()
    store.mark_stale_runs()
    seed_files(cfg)
    if not cfg.has_claude_auth:
        logger.warning("No CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY set: every model run will fail until one is.")
    if not cfg.has_cookies:
        logger.warning("ESPN_S2 and SWID are not set: the league cannot be read.")
    if cfg.dry_run:
        logger.warning("AGENT_DRY_RUN is on: writes are previewed and logged, never posted.")
    if not cfg.webhook_url:
        logger.warning("AGENT_WEBHOOK_URL is not set: briefs go to the log only.")

    queue = JobQueue(cfg)
    queue.start()
    clock = Clock(cfg, queue)
    clock.start()
    # Say at startup whether writes can work at all; the daily canary repeats it.
    asyncio.create_task(clock.canary())

    app = server.build_app(cfg, queue, clock)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.port)
    await site.start()
    logger.info("agent service listening on %s (team %s, %s)", cfg.port, cfg.team_id,
                "dry run" if cfg.dry_run else "live writes")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
