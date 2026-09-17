"""
Site tools: prose the dashboard shows. League-facing jobs get these; they
write to the site_content table in the dashboard's database, which changes
the fingerprint the dashboard polls, so a new piece reaches open tabs on
its own.
"""
from claude_agent_sdk import tool

from agent.tools.common import err, text
from gamedaybot.storage import db

NAMES = ["write_site_content"]


def build(ctx, run):
    cfg = ctx.cfg

    @tool("write_site_content",
          "Publish a piece of prose to the league dashboard. kind is one of: "
          + ", ".join(db.SITE_CONTENT_KINDS)
          + ". Replaces the same kind and week if it was written before. "
          "Markdown only: headings, paragraphs, lists, bold. No raw HTML, no links to tools.",
          {"kind": str, "week": int, "title": str, "markdown": str})
    async def write_site_content(args):
        kind = (args.get("kind") or "").strip()
        body = (args.get("markdown") or "").strip()
        if not body:
            return err("markdown is empty; nothing written")
        try:
            week = int(args.get("week"))
        except (TypeError, ValueError):
            return err("week is required")
        try:
            db.upsert_site_content(kind, cfg.year, week, body, title=(args.get("title") or "").strip() or None,
                                   run_id=run.run_id)
        except ValueError as e:
            return err(str(e))
        run.log("site_content", kind=kind, week=week, chars=len(body))
        return text(f"published {kind} for week {week} ({len(body)} chars)")

    return [write_site_content]
