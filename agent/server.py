"""
HTTP surface for the Discord bot. Internal to the compose network; no auth
because nothing outside the edge network can reach it.

Routes:
  GET  /health                      liveness plus auth and cookie flags
  GET  /status                      wakeups, next cron fires, recent runs, pending asks, canary
  POST /run/{job}                   {"params": {...}, "trigger": "..."} -> {"run_id"}
  GET  /runs/{id}                   one run row
  GET  /asks/pending                pending asks (for the bot to post and to resolve)
  POST /asks/{id}/posted            {"message_id": "..."}
  POST /asks/{id}/approve           {"by": "..."}
  POST /asks/{id}/reject            {"by": "..."}
  POST /ask                         {"question", "discord_user_id", "display_name"} -> {"run_id"} or 429
  GET  /users/{discord_id}          the team a Discord user claimed
  POST /users/{discord_id}          {"team_id": N, "display_name": "..."}
  GET  /teams                       team list for the claim command
  POST /canary                      run the auth canary now
"""
import asyncio
import json
import logging

from aiohttp import web

from agent import approvals, jobs, rules as rules_mod, store

logger = logging.getLogger(__name__)


def build_app(cfg, queue, clock):
    app = web.Application()
    routes = web.RouteTableDef()

    @routes.get("/health")
    async def health(request):
        return web.json_response({
            "ok": True, "claude_auth": cfg.has_claude_auth, "cookies": cfg.has_cookies,
            "dry_run": cfg.dry_run, "queue": queue.size, "current": queue.current,
        })

    @routes.get("/status")
    async def status(request):
        return web.json_response({
            "wakeups": store.pending_wakeups(),
            "next_fires": clock.next_fires(),
            "recent_runs": [_slim_run(r) for r in store.recent_runs(6)],
            "pending_asks": [_slim_ask(a) for a in store.pending_asks()],
            "canary": store.get_note("canary"),
            "queue": queue.size, "current": queue.current,
            "dry_run": cfg.dry_run, "claude_auth": cfg.has_claude_auth,
        })

    @routes.post("/run/{job}")
    async def run(request):
        job = request.match_info["job"]
        try:
            jobs.get(job)
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=404)
        body = await _json(request)
        if job == "ask":
            return web.json_response({"error": "use POST /ask"}, status=400)
        run_id = queue.submit(job, params=body.get("params") or {}, trigger=body.get("trigger") or "api")
        return web.json_response({"run_id": run_id, "queued": queue.size})

    @routes.get("/runs/{run_id}")
    async def get_run(request):
        row = store.get_run(request.match_info["run_id"])
        if not row:
            return web.json_response({"error": "no such run"}, status=404)
        return web.json_response(row)

    @routes.get("/asks/pending")
    async def asks_pending(request):
        return web.json_response(store.pending_asks())

    @routes.post("/asks/{ask_id}/posted")
    async def ask_posted(request):
        body = await _json(request)
        store.set_ask_message(request.match_info["ask_id"], body.get("message_id"))
        return web.json_response({"ok": True})

    @routes.post("/asks/{ask_id}/approve")
    async def ask_approve(request):
        ask = store.get_ask(request.match_info["ask_id"])
        if not ask:
            return web.json_response({"error": "no such ask"}, status=404)
        if ask["status"] != "pending":
            return web.json_response({"error": f"ask is {ask['status']}"}, status=409)
        body = await _json(request)
        store.resolve_ask(ask["id"], "approved", f"approved by {body.get('by')}")
        ok, message = await asyncio.to_thread(approvals.execute_ask, cfg, rules_mod.load(cfg.data_dir), ask)
        return web.json_response({"ok": ok, "message": message})

    @routes.post("/asks/{ask_id}/reject")
    async def ask_reject(request):
        ask = store.get_ask(request.match_info["ask_id"])
        if not ask:
            return web.json_response({"error": "no such ask"}, status=404)
        if ask["status"] != "pending":
            return web.json_response({"error": f"ask is {ask['status']}"}, status=409)
        body = await _json(request)
        store.resolve_ask(ask["id"], "rejected", f"rejected by {body.get('by')}")
        return web.json_response({"ok": True, "message": f"rejected ask {ask['id']}: {ask['description']}"})

    @routes.post("/ask")
    async def ask(request):
        body = await _json(request)
        question = (body.get("question") or "").strip()
        user_id = str(body.get("discord_user_id") or "")
        if not question or not user_id:
            return web.json_response({"error": "question and discord_user_id are required"}, status=400)
        user = store.team_for_user(user_id)
        if not user:
            return web.json_response({"error": "unknown user; claim a team first"}, status=403)
        # A limit of 0 means unlimited.
        if cfg.ask_daily_limit and store.asks_today(user_id) >= cfg.ask_daily_limit:
            return web.json_response({"error": f"daily limit of {cfg.ask_daily_limit} questions reached"}, status=429)
        if cfg.ask_league_daily_limit and store.asks_today() >= cfg.ask_league_daily_limit:
            return web.json_response({"error": "the league's daily question budget is used up"}, status=429)
        params = {"question": question[:1500], "asker_team_id": user["team_id"],
                  "team_name": body.get("team_name") or f"team {user['team_id']}",
                  "asker": body.get("display_name") or user.get("display_name"),
                  "history": render_history(body.get("history"))}
        run_id = queue.submit("ask", params=params, trigger="ask")
        store.log_ask(user_id, user["team_id"], question, run_id)
        return web.json_response({"run_id": run_id, "queued": queue.size})

    @routes.get("/users/{discord_id}")
    async def get_user(request):
        row = store.team_for_user(request.match_info["discord_id"])
        if not row:
            return web.json_response({"error": "not claimed"}, status=404)
        return web.json_response(row)

    @routes.post("/users/{discord_id}")
    async def set_user(request):
        body = await _json(request)
        try:
            team_id = int(body["team_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "team_id required"}, status=400)
        store.claim_team(request.match_info["discord_id"], team_id, body.get("display_name"))
        return web.json_response({"ok": True, "team_id": team_id})

    @routes.get("/teams")
    async def teams(request):
        from agent.espn_ctx import EspnContext
        ctx = EspnContext(cfg, rules_mod.load(cfg.data_dir), write_enabled=False)
        rows = await asyncio.to_thread(ctx.teams_summary)
        return web.json_response(rows)

    @routes.post("/canary")
    async def canary(request):
        ok = await clock.canary(force_post=True)
        return web.json_response({"ok": ok})

    app.add_routes(routes)
    return app


def render_history(history, limit=6000):
    """
    Earlier turns of a Discord thread as prompt text, or "" when there are
    none. Each entry is {"role": "user"|"analyst", "text": ...}. The text is
    league members' own words, so it is quoted as data, never as
    instructions; the analyst prompt says so.
    """
    if not history:
        return ""
    lines = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        role = "Them" if turn.get("role") == "user" else "You"
        text = str(turn.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > limit:
        body = body[-limit:]
    return "Earlier in this thread (oldest first):\n" + body + "\n\nNow they say:"


async def _json(request):
    try:
        return await request.json()
    except Exception:
        return {}


def _slim_run(r):
    return {k: r.get(k) for k in ("id", "job", "trigger", "status", "started_at", "finished_at", "num_turns",
                                  "cost_usd", "searches", "error")}


def _slim_ask(a):
    return {k: a.get(k) for k in ("id", "kind", "description", "reason", "created_at", "expires_at", "discord_message_id")}
