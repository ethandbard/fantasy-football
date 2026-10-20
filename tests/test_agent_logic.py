"""
Pure pieces of the agent service: wakeup planning from a roster, Discord
chunking, prompt rendering, and the tool-name groups the analyst may not see.
"""
import os
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


def test_record_opens_to_the_league_when_sharing_is_on():
    from types import SimpleNamespace
    from agent.tools import groups_for
    shared = SimpleNamespace(team_id=11, ask_share_record=True)
    closed = SimpleNamespace(team_id=11, ask_share_record=False)
    spec = jobs.get("ask")
    # Sharing on: a league member gets the record and the league note; the owner keeps the owner note.
    assert jobs.record_open(shared, {"asker_team_id": 6})
    note = jobs.record_note(shared, {"asker_team_id": 6})
    assert "opened the record" in note and "read_briefs" in note and "guesswork" in note
    assert jobs.record_note(shared, {"asker_team_id": 11}).startswith("The asker owns the agent")
    assert "mcp__espn__read_briefs" in [n for g in groups_for("ask", spec, jobs.record_open(shared, {"asker_team_id": 6}))
                                         for n in tool_names(g)]
    # Sharing off: only the owner.
    assert not jobs.record_open(closed, {"asker_team_id": 6}) and jobs.record_open(closed, {"asker_team_id": 11})
    assert jobs.record_note(closed, {"asker_team_id": 6}) == ""
    assert groups_for("ask", spec, jobs.record_open(closed, {"asker_team_id": 6})) == list(spec.tool_groups)
    # The default is open.
    from agent import config
    assert config._bool(None, True) is True and config._bool("false", True) is False


def test_claude_auth_defaults_to_the_subscription_and_hides_the_api_key(monkeypatch):
    from agent import config
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert config.select_claude_auth(None) == "subscription"
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "token"


def test_claude_auth_api_mode_hides_the_token(monkeypatch):
    from agent import config
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert config.select_claude_auth("API") == "api"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
    assert os.environ["ANTHROPIC_API_KEY"] == "key"


def test_claude_auth_falls_back_to_whichever_credential_exists(monkeypatch):
    from agent import config
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert config.select_claude_auth("subscription") == "api"
    assert os.environ["ANTHROPIC_API_KEY"] == "key"


def test_designations_checks_every_starters_team():
    from types import SimpleNamespace
    cfg = SimpleNamespace(team_id=11, heavy_search_cap=40, light_search_cap=16)
    ctx = SimpleNamespace(cfg=cfg, week=6, scoring_period=6, team_name=lambda _id: "Mine")
    out = jobs.user_prompt(jobs.get("designations"), ctx, {})
    assert "You have 16 web searches" in out
    assert "Team check, mandatory" in out and "required for every starter" in out
    assert "starting QB's status" in out
    assert "starter ahead of him" in out
    assert "{" not in out


def test_light_search_cap_fits_a_team_by_team_check(monkeypatch):
    # About eight starter teams plus the tagged players did not fit in the old cap of 8.
    from agent import config
    monkeypatch.delenv("AGENT_LIGHT_SEARCH_CAP", raising=False)
    monkeypatch.setenv("LEAGUE_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    cfg = config.from_env()
    assert cfg.light_search_cap == 16


def test_persona_no_longer_skips_healthy_starters():
    persona = jobs.read_prompt("persona.md")
    assert "Do not search about healthy players" not in persona
    assert "ESPN injury tags lag the news" in persona
    assert "owner_overrides" in persona


def test_pregame_reads_state_and_checks_inactives_for_every_team():
    out = " ".join(jobs.read_prompt("pregame.md").split())
    assert "read_state" in out.split(" 2. ")[0]
    assert "required even when every player is ACTIVE" in out
    assert "owner override" in out


def test_owner_jobs_must_report_skipped_steps():
    from types import SimpleNamespace
    cfg = SimpleNamespace(team_id=11, dry_run=False)
    owner = jobs.system_prompt(jobs.get("designations"), cfg, "rules")
    assert "# Steps skipped" in owner and "# Rules" in owner
    assert 'Write "none"' in owner
    league = jobs.system_prompt(jobs.get("ask"), cfg, "rules")
    assert "Steps skipped" not in league


def test_audit_flags_the_week_6_designations_run():
    from agent import audit
    # What run 9bef151875a3 did: one search, about Allgeier, and no team check.
    calls = {"get_run_context": 1, "get_my_roster": 1, "preview_lineup": 1, "read_state": 1,
             "get_free_agents": 1, "execute_lineup": 1, "WebSearch": 1, "append_season_log": 1}
    problems = audit.check("designations", calls, "Lineup set.\n\nDecisions you might disagree with\n- x")
    assert problems == ["1 web searches, needs at least 2", 'brief has no "Steps skipped" section']


def test_audit_passes_a_complete_run_and_names_missing_calls():
    from agent import audit
    calls = {"get_my_roster": 1, "read_state": 1, "WebSearch": 2}
    assert audit.check("pregame", calls, "No change.\nSteps skipped: none") == []
    problems = audit.check("pregame", {"get_my_roster": 1}, "No change.\nSteps skipped: none")
    assert problems == ["never called read_state", "0 web searches, needs at least 1"]
    # WebFetch counts toward the web minimum.
    assert audit.check("trade_review", {"get_my_roster": 1, "get_team_roster": 1, "read_analytics": 1,
                                        "WebSearch": 1, "WebFetch": 1}, "## Steps skipped\nnone") == []
    # A job outside the table needs only the section; a league-facing job needs nothing.
    assert audit.check("analytics", {}, "report\nSteps skipped: none") == []
    assert audit.check("ask", {}, "answer", owner_job=False) == []


def test_audit_counts_tools_by_short_name():
    from agent import audit
    assert audit.short_name("mcp__espn__get_my_roster") == "get_my_roster"
    assert audit.short_name("WebSearch") == "WebSearch"
    line = audit.warning("Pre-game check", "abc123", ["never called read_state"])
    assert "Pre-game check" in line and "abc123" in line and "never called read_state" in line


def test_strategy_file_round_trips_and_refuses_empty(tmp_path):
    import asyncio
    from types import SimpleNamespace
    ctx = SimpleNamespace(cfg=SimpleNamespace(data_dir=tmp_path), week=7)
    run = SimpleNamespace(job="gm")
    handlers = {t.name: t.handler for t in state.build(ctx, run)}

    def call(name, args):
        out = asyncio.run(handlers[name](args))
        return out["content"][0]["text"], out.get("is_error", False)

    assert "no strategy file yet" in call("read_strategy", {})[0]
    assert call("write_strategy", {"markdown": "  "})[1]
    call("write_strategy", {"markdown": "## Target board\n- Hubbard"})
    assert call("read_strategy", {})[0].startswith("## Target board")
    assert (tmp_path / "strategy.md").exists()


def test_gm_job_is_an_owner_writer_that_hunts_trades():
    from types import SimpleNamespace
    spec = jobs.get("gm")
    assert spec.owner_job and spec.writes and spec.web and spec.model == "heavy"
    assert "mcp__espn__write_strategy" in tool_names("state")
    assert "mcp__espn__read_strategy" not in tool_names("analyst")
    cfg = SimpleNamespace(team_id=11, heavy_search_cap=40, light_search_cap=16)
    ctx = SimpleNamespace(cfg=cfg, week=7, scoring_period=7, team_name=lambda _id: "John Foot Ball")
    out = " ".join(jobs.user_prompt(spec, ctx, {}).split())
    assert "general manager of John Foot Ball" in out and "You have 40 web searches" in out
    assert "two to four offers open" in out and "withdraw_trade" in out
    assert "{" not in out.replace("{week}", "")


def test_audit_requires_the_gm_to_scout_and_keep_the_strategy():
    from agent import audit
    calls = {"read_strategy": 1, "get_standings": 1, "get_agent_activity": 1, "get_team_roster": 7,
             "get_free_agents": 4, "write_strategy": 1, "append_season_log": 1, "WebSearch": 3}
    assert audit.check("gm", calls, "Offers sent.\nSteps skipped: none") == []
    calls.pop("write_strategy")
    assert audit.check("gm", calls, "Steps skipped: none") == ["never called write_strategy"]


def test_prompts_no_longer_tell_the_agents_to_sit_still():
    persona = jobs.read_prompt("persona.md")
    assert "A roster is not improved by churn" not in persona
    assert "Doing nothing is a decision" in " ".join(persona.split())
    plan = " ".join(jobs.read_prompt("plan.md").split())
    assert "read_strategy" in plan and "If you send nothing, say per target why not" in plan
    review = " ".join(jobs.read_prompt("trade_review.md").split())
    assert "A bare decline with no counter needs a reason" in review
