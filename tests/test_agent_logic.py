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


def test_analyst_prompt_names_the_week_and_what_it_cannot_see():
    from agent import jobs
    out = jobs.render(jobs.read_prompt("analyst.md"), week=3, played_week=2, team_name="Yikes",
                      asker_team_id=6, owner_team_id=11, question="q", history="", search_cap=8, owner_note="")
    assert "NFL week 3" in out and "week 2 is the one just played" in out
    assert "declined, withdrawn, or expired" in out
    assert "tagged with the speaker's team" in out
    assert "{" not in out


def test_owner_asking_gets_the_private_record():
    from types import SimpleNamespace
    from agent.tools import groups_for
    cfg = SimpleNamespace(team_id=11)
    assert jobs.is_owner(cfg, {"asker_team_id": 11}) and jobs.is_owner(cfg, {"asker_team_id": "11"})
    assert not jobs.is_owner(cfg, {"asker_team_id": 6}) and not jobs.is_owner(cfg, {}) and not jobs.is_owner(cfg, None)
    spec = jobs.get("ask")
    assert groups_for("ask", spec, False) == list(spec.tool_groups)
    names = [n for g in groups_for("ask", spec, True) for n in tool_names(g)]
    assert "mcp__espn__read_briefs" in names and "mcp__espn__read_research" in names
    assert "mcp__espn__read_season_log" in names and "mcp__espn__get_rules" in names
    assert "mcp__espn__get_agent_activity" in names and "mcp__espn__get_agent_activity" in tool_names("read")
    assert "mcp__espn__get_agent_activity" not in tool_names("analyst")
    assert not any("execute" in n or "preview" in n or "write_" in n for n in names)
    # No other job changes with who triggered it, and the public analyst never sees the briefs.
    plan = jobs.get("plan")
    assert groups_for("plan", plan, True) == list(plan.tool_groups)
    assert "mcp__espn__read_briefs" not in tool_names("analyst")
    assert "read_briefs" in jobs.OWNER_NOTE.format(owner_team_id=11)


def test_pending_trade_phase_reads_team_actions():
    from agent.espn_ctx import describe_pending
    names = {1: "Seemed like the thing to do", 4: "Half In, Half Hurts"}
    accepted = {"type": "TRADE_ACCEPT", "status": "PENDING", "teamActions": {"4": "ACCEPTED", "1": "ACCEPTED"},
                "processDate": 1790199788167, "items": [{"fromTeamId": 4, "toTeamId": 1}]}
    d = describe_pending(accepted, names.get)
    assert d["accepted"] and d["accepted_by"] == [1, 4]
    assert "review window" in d["phase"] and d["process_at"].startswith("2026-09-23")
    offer = {"type": "TRADE_PROPOSAL", "status": "PENDING", "teamActions": {"4": "ACCEPTED"},
             "items": [{"fromTeamId": 4, "toTeamId": 1}]}
    d = describe_pending(offer, names.get)
    assert not d["accepted"] and d["phase"] == "offer awaiting a response from Seemed like the thing to do"
    claim = {"type": "WAIVER", "status": "PENDING", "processDate": 1790199788167, "items": [{"toTeamId": 1}]}
    assert describe_pending(claim, names.get)["phase"].startswith("claim waiting for waivers")
