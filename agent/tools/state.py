"""
State tools: the research and state files, the season log, and the week's
pre-game wakeups. Only Ethan's own jobs get these.
"""
import json
from datetime import datetime, timedelta, timezone

from claude_agent_sdk import tool

import gamedaybot.espn.roster as roster
from agent import ledger, store
from agent.tools.common import err, text

NAMES = ["write_research", "write_state", "append_season_log", "schedule_pregame_checks", "list_wakeups",
         "read_strategy", "write_strategy", "note_for_gm", "read_scorecard"]

SCORECARD_FILE = "gm/scorecard.md"

# The season strategy: one living file, not one per week, so the target
# board and what each manager has said to past offers carry from week to
# week. The general manager job owns it; the other owner jobs read it and
# may update it.
STRATEGY_FILE = "strategy.md"

PREGAME_LEAD = timedelta(minutes=60)

# The data analyst's section of a week's research file. It may be written
# before or after the research job writes the rest, so each writer keeps the
# other's part.
ANALYTICS_START = "<!-- in-house-analytics:start -->"
ANALYTICS_END = "<!-- in-house-analytics:end -->"


def analytics_section(markdown):
    """The analytics block in a research file, markers included, or None."""
    if not markdown:
        return None
    a, b = markdown.find(ANALYTICS_START), markdown.find(ANALYTICS_END)
    if a == -1 or b == -1 or b < a:
        return None
    return markdown[a:b + len(ANALYTICS_END)]


def with_analytics(markdown, body):
    """markdown with its analytics block replaced by body (appended if it had none)."""
    block = f"{ANALYTICS_START}\n{body.strip()}\n{ANALYTICS_END}"
    old = analytics_section(markdown)
    if old:
        return markdown.replace(old, block)
    return (markdown.rstrip() + "\n\n" if markdown and markdown.strip() else "") + block + "\n"


def keep_analytics(new_markdown, old_markdown):
    """A rewrite of the research file keeps the analyst's block unless it carries one itself."""
    old = analytics_section(old_markdown)
    if not old or analytics_section(new_markdown):
        return new_markdown
    return new_markdown.rstrip() + "\n\n" + old + "\n"


def plan_wakeups(entries, now=None, lead=PREGAME_LEAD):
    """
    (run_at, label, params) for every distinct future kickoff on a roster.

    Pure so it can be tested: the caller stores the result.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for when, names in roster.distinct_kickoffs(entries):
        run_at = when - lead
        if run_at <= now:
            continue
        label = when.astimezone(roster._eastern()).strftime("%a %b %d %I:%M %p ET") + " kickoff"
        out.append((run_at, label, {"kickoff": when.isoformat(), "players": names}))
    return out


def schedule_week(ctx, replace=True):
    entries = ctx.my_roster(refresh=True)
    planned = plan_wakeups(entries)
    if replace:
        store.clear_pending_wakeups("pregame")
    for run_at, label, params in planned:
        params["week"] = ctx.week
        store.add_wakeup("pregame", run_at, params=params, label=label)
    store.set_note("wakeups_planned_for_week", ctx.week)
    return planned


def build(ctx, run):
    cfg = ctx.cfg

    @tool("write_research", "Save the week's research report (markdown). Overwrites the file for that week.",
          {"week": int, "markdown": str})
    async def write_research(args):
        week = int(args.get("week") or ctx.week)
        path = cfg.data_dir / "research" / f"week-{week:02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        body = keep_analytics(args["markdown"], old)
        path.write_text(body, encoding="utf-8")
        kept = " (kept the in-house analytics section)" if body != args["markdown"] else ""
        return text(f"wrote {path.name} ({len(body)} chars){kept}")

    @tool("write_state", "Save the week's structured state as JSON text: player notes, tiers, injury flags, owners' needs, plan.",
          {"week": int, "json_text": str})
    async def write_state(args):
        week = int(args.get("week") or ctx.week)
        try:
            parsed = json.loads(args["json_text"])
        except json.JSONDecodeError as e:
            return err(f"not valid JSON: {e}")
        path = cfg.data_dir / "state" / f"week-{week:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        return text(f"wrote {path.name}")

    @tool("append_season_log", "Append a dated entry (markdown) to the season log. Keep it factual: what was done and why.",
          {"markdown": str})
    async def append_season_log(args):
        ledger.season_log_append(cfg.data_dir, run.job, args["markdown"])
        return text("appended to season log")

    @tool("schedule_pregame_checks",
          "Compute this week's kickoffs for my roster and schedule a pre-game check 60 minutes before each. "
          "Replaces any pending pre-game checks.", {})
    async def schedule_pregame_checks(args):
        planned = schedule_week(ctx)
        if not planned:
            return text("no future kickoffs found for my roster; nothing scheduled")
        return text("scheduled:\n" + "\n".join(f"{r.astimezone(roster._eastern()).strftime('%a %I:%M %p ET')} -> {label} ({', '.join(p['players'])})"
                                                for r, label, p in planned))

    @tool("list_wakeups", "Pending scheduled wakeups.", {})
    async def list_wakeups(args):
        rows = store.pending_wakeups()
        return text("\n".join(f"{r['run_at']}  {r['job']:<10} {r['label'] or ''}" for r in rows) or "none pending")

    @tool("read_strategy", "The general manager's standing position: playoff outlook, the holes, the bets placed "
          "and why, what to watch, and the owner's standing instructions. Rewritten each weekly session; the "
          "per-manager threads live in read_negotiations.", {})
    async def read_strategy(args):
        path = cfg.data_dir / STRATEGY_FILE
        if not path.exists():
            return text("no strategy file yet; the general manager's weekly session writes the first one")
        return text(path.read_text(encoding="utf-8"))

    @tool("write_strategy", "Replace the strategy file (markdown). Read it first and carry forward what still "
          "holds. Sections: Outlook, Holes, Bets, Watch, Owner. Offer threads go in update_negotiation, "
          "not here.", {"markdown": str})
    async def write_strategy(args):
        body = (args.get("markdown") or "").strip()
        if not body:
            return err("the strategy is empty; nothing written")
        path = cfg.data_dir / STRATEGY_FILE
        path.write_text(body + "\n", encoding="utf-8")
        return text(f"wrote {path.name} ({len(body)} chars)")

    @tool("note_for_gm", "Leave a note the general manager reads at the start of its next run: a game-time "
          "call, a dropped player worth chasing, a claim that failed, anything a trade or an add should "
          "answer. One or two lines.", {"text": str})
    async def note_for_gm(args):
        body = (args.get("text") or "").strip()
        if not body:
            return err("the note is empty")
        store.add_inbox(run.job, body[:1000])
        return text("noted for the general manager")

    @tool("read_scorecard", "The weekly scorecard the service computes with no judgment in it: points left on "
          "the bench per week, claims won and lost, what the players in every trade proposed or declined "
          "scored afterward, and the model's edge against ESPN. Read it before deciding what to trust.", {})
    async def read_scorecard(args):
        path = cfg.data_dir / SCORECARD_FILE
        if not path.exists():
            return text("no scorecard yet; it is written Tuesday morning once a week has been played")
        return text(path.read_text(encoding="utf-8"))

    return [write_research, write_state, append_season_log, schedule_pregame_checks, list_wakeups,
            read_strategy, write_strategy, note_for_gm, read_scorecard]
