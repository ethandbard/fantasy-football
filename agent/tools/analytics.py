"""
The data analyst's tools: build the model's numbers and charts, look at one
player's weeks, and publish the PDF report with a section in the week's
research file. Only the analytics job gets these; the other jobs read the
result through read_analytics.
"""
import asyncio
import json

from claude_agent_sdk import tool

from agent.analytics import pipeline, report
from agent.tools.common import err, text
from agent.tools.state import with_analytics

NAMES = ["build_analytics", "get_player_trend", "publish_analytics"]

NARRATIVE_KEYS = [k for k, _ in report.SECTIONS] + ["caveats"]


def build(ctx, run):
    cfg = ctx.cfg
    state = {}

    async def analysis():
        if "analysis" not in state:
            state["analysis"] = await asyncio.to_thread(pipeline.build, ctx, cfg.data_dir)
        return state["analysis"]

    @tool("build_analytics",
          "Fetch the public usage data, fit the expected-points model, forecast every player in the league and "
          "the free-agent pool, simulate this week's matchup, and draw the charts. Returns every table as text. "
          "Call it first; later calls return the same build.", {})
    async def build_analytics(args):
        try:
            a = await analysis()
        except Exception as e:
            return err(f"analytics build failed: {type(e).__name__}: {e}")
        return text(pipeline.digest(a))

    @tool("get_player_trend",
          "Week-by-week usage for players whose name contains `name`: snaps, targets, carries, air yards, shares, "
          "expected points, actual points, and the forecast breakdown. For checking a story before you tell it.",
          {"name": str})
    async def get_player_trend(args):
        a = await analysis()
        out = pipeline.player_trend(a, args.get("name") or "")
        return text(out) if out else err(f"no player matching {args.get('name')!r} in the usage data")

    @tool("publish_analytics",
          "Render the PDF report and file it. Each argument is markdown for one section of the report: summary, "
          "matchup_notes, roster_notes, trend_notes, waiver_notes, trade_notes, divergence_notes, caveats. "
          "research_section is the markdown that goes into this week's research file for the research, plan, and "
          "trade jobs; the model-vs-ESPN tables are appended to it for you. The PDF posts to Discord with the brief.",
          {"summary": str, "matchup_notes": str, "roster_notes": str, "trend_notes": str, "waiver_notes": str,
           "trade_notes": str, "divergence_notes": str, "caveats": str, "research_section": str})
    async def publish_analytics(args):
        if "analysis" not in state:
            return err("call build_analytics first")
        a = state["analysis"]
        week = a.meta["target_week"]
        out_dir = pipeline.analytics_dir(cfg.data_dir, week)
        narrative = {k: args.get(k) or "" for k in NARRATIVE_KEYS}
        (out_dir / "narrative.json").write_text(json.dumps(narrative, indent=1), encoding="utf-8")

        section = (f"## In-house analytics (data through NFL week {a.meta['data_through']})\n\n"
                   f"{(args.get('research_section') or args.get('summary') or '').strip()}\n\n"
                   f"{pipeline.research_tables(a)}\n\n"
                   f"_Full report: analytics/week-{week:02d}/report.pdf. Numbers: read_analytics._")
        (out_dir / "summary.md").write_text(section, encoding="utf-8")
        research = cfg.data_dir / "research" / f"week-{week:02d}.md"
        research.parent.mkdir(parents=True, exist_ok=True)
        old = research.read_text(encoding="utf-8") if research.exists() else ""
        research.write_text(with_analytics(old, section), encoding="utf-8")

        pdf, problem = await asyncio.to_thread(report.render, a, narrative, out_dir)
        if pdf:
            run.attachments.append(str(pdf))
            return text(f"rendered {pdf.name}; it posts to Discord with your brief. Research file "
                        f"{research.name} now carries the analytics section.")
        run.attachments.extend(a.charts.values())
        return text(f"the PDF did not render ({problem}); the charts will post with the brief instead. "
                    f"Say so in the brief. Research file {research.name} carries the analytics section.")

    return [build_analytics, get_player_trend, publish_analytics]
