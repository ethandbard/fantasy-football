"""
Runs one job: builds the tool server and prompts, drives the Agent SDK,
enforces the search cap through a hook, posts the brief, and records the run.

One run at a time. The queue in agent.queue serializes calls; this module
also holds a lock so a stray direct call cannot overlap.
"""
import asyncio
import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
                              TextBlock, query)

from agent import discord_out, jobs, rules as rules_mod, store, tools
from agent.espn_ctx import EspnContext

logger = logging.getLogger(__name__)

_LOCK = asyncio.Lock()


@dataclass
class RunState:
    run_id: str
    job: str
    params: Dict[str, Any]
    searches: int = 0
    search_cap: int = 0
    adds_executed: int = 0
    asks: List[str] = field(default_factory=list)
    transactions: List[dict] = field(default_factory=list)
    log_path: Any = None

    def log(self, event, **data):
        if not self.log_path:
            return
        rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        rec.update(data)
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass


def _hooks(run):
    async def pre_tool(hook_input, tool_use_id, context):
        name = hook_input.get("tool_name", "")
        args = hook_input.get("tool_input", {})
        if name in ("WebSearch", "WebFetch"):
            run.searches += 1
            if run.searches > run.search_cap:
                run.log("search_denied", tool=name, args=args)
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"search cap of {run.search_cap} reached for this run; decide with what you have",
                }}
        run.log("tool_call", tool=name, args=args)
        return {}

    async def post_tool(hook_input, tool_use_id, context):
        name = hook_input.get("tool_name", "")
        response = hook_input.get("tool_response")
        summary = json.dumps(response, default=str)[:600] if response is not None else None
        run.log("tool_result", tool=name, result=summary)
        return {}

    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool])],
    }


def _model_name(cfg, spec):
    return cfg.heavy_model if spec.model == "heavy" else cfg.light_model


async def run_job(cfg, name, params=None, trigger="schedule", run_id=None):
    """Execute a job end to end. Returns a dict with run_id, status, brief, and usage."""
    spec = jobs.get(name)
    params = params or {}
    async with _LOCK:
        if run_id is None:
            run_id = store.create_run(name, trigger=trigger, params=params)
        else:
            store.start_run(run_id)
        cfg.ensure_dirs()
        run = RunState(run_id=run_id, job=name, params=params,
                       search_cap=cfg.heavy_search_cap if spec.search_cap_key == "heavy" else cfg.light_search_cap,
                       log_path=cfg.data_dir / "runs" / f"{run_id}.jsonl")
        run.log("start", job=name, trigger=trigger, params=params)
        rules = rules_mod.load(cfg.data_dir)
        outcome = {"run_id": run_id, "job": name, "status": "failed", "brief": None}
        try:
            ctx = EspnContext(cfg, rules, write_enabled=spec.writes)
            ctx.league()
            server = tools.build_server(ctx, run)
            allowed = []
            for group in spec.tool_groups:
                allowed.extend(tools.tool_names(group))
            builtins = ["WebSearch", "WebFetch"] if spec.web else []
            allowed.extend(builtins)
            options = ClaudeAgentOptions(
                system_prompt=jobs.system_prompt(spec, cfg, rules_mod.prose(cfg.data_dir, cfg.repo_agents_dir)),
                tools=builtins,
                allowed_tools=allowed,
                mcp_servers={tools.SERVER_NAME: server},
                permission_mode="dontAsk",
                model=_model_name(cfg, spec),
                max_turns=cfg.heavy_max_turns if spec.max_turns_key == "heavy" else cfg.light_max_turns,
                effort=spec.effort,
                hooks=_hooks(run),
                cwd=str(cfg.data_dir / "runs"),
                include_partial_messages=False,
            )
            prompt = jobs.user_prompt(spec, ctx, params)
            run.log("prompt", text=prompt)
            texts = []
            result = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            texts.append(block.text)
                elif isinstance(message, ResultMessage):
                    result = message
            brief = (result.result if result and result.result else (texts[-1] if texts else "")).strip()
            if result is not None and result.is_error:
                errors = "; ".join(result.errors or []) or result.subtype
                raise RuntimeError(f"agent run ended in error: {errors}")
            usage = _usage(result)
            if spec.post_brief and not params.get("silent"):
                title = f"{spec.title} · week {ctx.week}"
                discord_out.post(cfg, title, brief or "(the run produced no brief)", kind=spec.kind)
            store.finish_run(run_id, "done", result=brief, model=_model_name(cfg, spec),
                             num_turns=result.num_turns if result else None, searches=run.searches, **usage)
            outcome.update(status="done", brief=brief, usage=usage,
                           num_turns=result.num_turns if result else None, asks=run.asks,
                           transactions=run.transactions)
            run.log("done", **usage)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.exception("run %s (%s) failed", run_id, name)
            run.log("error", error=err, trace=traceback.format_exc())
            store.finish_run(run_id, "failed", error=err, searches=run.searches)
            outcome.update(status="failed", error=err)
            if spec.post_brief:
                discord_out.post(cfg, f"{spec.title} failed", f"Run `{run_id}` failed: {err[:1500]}", kind="alert")
        return outcome


def _usage(result):
    if result is None:
        return {"cost_usd": None, "input_tokens": None, "output_tokens": None}
    usage = result.usage or {}
    return {
        "cost_usd": result.total_cost_usd,
        "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": usage.get("output_tokens"),
    }
