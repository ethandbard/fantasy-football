"""
Pure pieces of the agent service: wakeup planning from a roster, Discord
chunking, prompt rendering, and the tool-name groups the analyst may not see.
"""
from datetime import datetime, timedelta, timezone

import gamedaybot.espn.roster as roster
from agent import discord_out, jobs
from agent.tools import state, tool_names


def _entry(name, kickoff, slot=2):
    return roster.RosterEntry(
        player_id=hash(name) % 10000, name=name, position="RB", pro_team="DET", slot_id=slot,
        slot=roster.slot_name(slot), eligible_slot_ids=[2], eligible_slots=["RB"], injury_status="ACTIVE",
        injured=False, lineup_locked=False, roster_locked=False, trade_locked=False, droppable=True,
        kickoff=kickoff.isoformat() if kickoff else None, bye=kickoff is None,
    )


def test_plan_wakeups_one_per_future_kickoff_minus_lead():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    thu = datetime(2026, 9, 18, 0, 15, tzinfo=timezone.utc)
    sun = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    gone = datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)
    entries = [_entry("Gibbs", thu), _entry("Irving", sun), _entry("Egbuka", sun), _entry("Old", gone), _entry("Bye", None)]
    planned = state.plan_wakeups(entries, now=now)
    assert [p[0] for p in planned] == [thu - timedelta(minutes=60), sun - timedelta(minutes=60)]
    assert sorted(planned[1][2]["players"]) == ["Egbuka", "Irving"]
    assert "kickoff" in planned[0][1]


def test_chunk_splits_on_paragraphs_under_limit():
    body = "\n\n".join(f"paragraph {i} " + ("x" * 500) for i in range(12))
    pieces = discord_out.chunk(body, limit=2000)
    assert len(pieces) >= 3
    assert all(len(p) <= 2000 for p in pieces)
    assert "".join(p.replace("\n", "") for p in pieces).count("paragraph") == 12
    assert discord_out.chunk("") == []


def test_render_leaves_unknown_placeholders_alone():
    out = jobs.render("week {week} and {{literal}} and {missing}", week=3)
    assert out == "week 3 and {literal} and {missing}"


def test_every_job_prompt_exists_and_persona_mentions_rules():
    for spec in jobs.JOBS.values():
        assert jobs.read_prompt(spec.prompt_file)
    assert "preview" in jobs.read_prompt("persona.md").lower()


def test_analyst_tools_exclude_private_and_writes():
    analyst = tool_names("analyst")
    assert "mcp__espn__get_team_roster" in analyst
    for private in ("read_research", "read_state", "read_season_log", "get_rules"):
        assert f"mcp__espn__{private}" not in analyst
    assert not any("execute" in n or "preview" in n for n in analyst)
    # League history is public: the prose jobs and /ask both get it.
    assert "mcp__espn__get_rivalry" in analyst and "mcp__espn__get_league_history" in analyst
    assert "mcp__espn__execute_lineup" in tool_names("write")


def test_agent_webhook_accepts_a_list_like_the_bot():
    from types import SimpleNamespace
    one = SimpleNamespace(webhook_url="https://discord.com/api/webhooks/1/a")
    two = SimpleNamespace(webhook_url="https://discord.com/api/webhooks/1/a, https://discord.com/api/webhooks/2/b")
    none = SimpleNamespace(webhook_url=None)
    assert discord_out.webhook_urls(one) == ["https://discord.com/api/webhooks/1/a"]
    assert discord_out.webhook_urls(two) == ["https://discord.com/api/webhooks/1/a", "https://discord.com/api/webhooks/2/b"]
    assert discord_out.webhook_urls(none) == []


def test_recap_job_is_league_facing():
    spec = jobs.get("recap")
    assert not spec.owner_job and not spec.writes and not spec.web and not spec.post_brief
    names = [n for g in spec.tool_groups for n in tool_names(g)]
    assert "mcp__espn__write_site_content" in names
    assert "mcp__espn__get_week_results" in names
    for private in ("read_research", "read_state", "read_season_log", "get_rules"):
        assert f"mcp__espn__{private}" not in names
    assert not any("execute" in n or "preview" in n for n in names)
    # The analyst that answers friends must not be able to publish.
    assert "mcp__espn__write_site_content" not in tool_names("analyst")


def test_recap_prompt_targets_the_week_just_played():
    from types import SimpleNamespace
    cfg = SimpleNamespace(team_id=11, heavy_search_cap=40, light_search_cap=8)
    ctx = SimpleNamespace(cfg=cfg, week=4, scoring_period=4, team_name=lambda _id: "Mine")
    spec = jobs.get("recap")
    assert "week to recap is week 3" in jobs.user_prompt(spec, ctx, {})
    assert "week to recap is week 2" in jobs.user_prompt(spec, ctx, {"week": 2})
    # Week 1 cannot recap week 0.
    ctx.week = 1
    assert "week to recap is week 1" in jobs.user_prompt(spec, ctx, {})


def test_preview_and_power_jobs_are_league_facing_like_the_recap():
    for name in ("preview", "power"):
        spec = jobs.get(name)
        assert not spec.owner_job and not spec.writes and not spec.web and not spec.post_brief
        names = [n for g in spec.tool_groups for n in tool_names(g)]
        assert "mcp__espn__write_site_content" in names
        assert not any("execute" in n or "preview_" in n for n in names)
        for private in ("read_research", "read_state", "read_season_log", "get_rules"):
            assert f"mcp__espn__{private}" not in names
