"""
The clock: fixed weekly jobs, the minute tick that fires kickoff-relative
wakeups, the trade-offer poll, the auth canary, and the Monday preview.

Fixed jobs are cron entries in the league's timezone. Wakeups are rows in
agent_wakeups, planned by the Tuesday roster plan (or by the safety net
here if that run failed) and fired by the tick, so a container restart
loses nothing.
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import gamedaybot.espn.roster as roster
from gamedaybot.discord_bot.formatting import schedule_lines
from agent import discord_out, ledger, rules as rules_mod, store
from agent.espn_ctx import EspnContext
from agent.tools.state import schedule_week

logger = logging.getLogger(__name__)


class Clock:
    def __init__(self, cfg, queue):
        self.cfg = cfg
        self.queue = queue
        self.sched = AsyncIOScheduler(timezone=cfg.timezone)

    def start(self):
        cfg = self.cfg
        add = self.sched.add_job
        tz = cfg.timezone
        if cfg.enabled_schedule:
            # After ESPN rolls the week (before 6:00) and before the research
            # run, so the dashboard's recap is up first thing Tuesday.
            add(self._submit, CronTrigger(day_of_week="tue", hour=6, minute=30, timezone=tz), args=["recap"], id="recap")
            add(self._submit, CronTrigger(day_of_week="tue", hour=6, minute=40, timezone=tz), args=["power"], id="power")
            add(self._submit, CronTrigger(day_of_week="tue", hour=7, minute=0, timezone=tz), args=["research"], id="research")
            # After the post-waiver adjust, so the preview sees settled rosters.
            add(self._submit, CronTrigger(day_of_week="wed", hour=10, minute=30, timezone=tz), args=["preview"], id="preview_site")
            add(self._submit, CronTrigger(day_of_week="tue", hour=8, minute=0, timezone=tz), args=["plan"], id="plan")
            add(self.ensure_wakeups, CronTrigger(day_of_week="tue", hour=9, minute=15, timezone=tz), id="ensure_wakeups")
            add(self._submit, CronTrigger(day_of_week="wed", hour=9, minute=30, timezone=tz), args=["postwaiver"], id="postwaiver")
            add(self._submit, CronTrigger(day_of_week="fri", hour=17, minute=30, timezone=tz), args=["designations"], id="designations")
            add(self.preview, CronTrigger(day_of_week="mon", hour=21, minute=0, timezone=tz), id="preview")
            add(self.canary, CronTrigger(hour=6, minute=45, timezone=tz), id="canary")
            add(self.poll_offers, IntervalTrigger(minutes=15), id="poll_offers")
        add(self.tick, IntervalTrigger(seconds=60), id="tick")
        add(store.expire_asks, IntervalTrigger(minutes=30), id="expire_asks")
        add(self.remind_asks, IntervalTrigger(minutes=30), id="remind_asks")
        self.sched.start()
        logger.info("scheduler started (fixed jobs %s)", "on" if cfg.enabled_schedule else "off")

    def next_fires(self):
        out = []
        for job in self.sched.get_jobs():
            if job.next_run_time:
                out.append({"job": job.id, "next": job.next_run_time.isoformat(timespec="minutes")})
        return sorted(out, key=lambda r: r["next"])

    # ---------------------------------------------------------- helpers

    def _submit(self, job, params=None):
        return self.queue.submit(job, params=params or {}, trigger="schedule")

    def _ctx(self):
        return EspnContext(self.cfg, rules_mod.load(self.cfg.data_dir), write_enabled=False)

    # ------------------------------------------------------------- tick

    async def tick(self):
        for wake in store.due_wakeups():
            try:
                params = wake.get("params")
                params = __import__("json").loads(params) if isinstance(params, str) else (params or {})
                params["label"] = wake.get("label") or "kickoff"
                run_id = self.queue.submit(wake["job"], params=params, trigger="wakeup")
                store.set_wakeup_status(wake["id"], "fired", run_id)
                logger.info("wakeup %s fired -> %s", wake["id"], run_id)
            except Exception:
                logger.exception("wakeup %s failed to fire", wake["id"])
                store.set_wakeup_status(wake["id"], "failed")

    # -------------------------------------------------- wakeup planning

    async def ensure_wakeups(self, force=False):
        """
        Safety net: if the plan job did not schedule this week's checks, do
        it here. `force` re-plans even when the week is marked as done, for
        the /agent wakeups command; re-planning is safe because it replaces
        the pending pre-game checks and only future kickoffs count.

        Returns the planned (run_at, label, params) list, or None when the
        week was already planned and nothing was touched.
        """
        try:
            ctx = self._ctx()
            if not force and store.get_note("wakeups_planned_for_week") == ctx.week and store.pending_wakeups():
                return None
            planned = schedule_week(ctx)
            logger.info("ensure_wakeups planned %d checks", len(planned))
            if planned:
                discord_out.post(self.cfg, f"Pre-game checks · week {ctx.week}", _wakeup_lines(planned), kind="info")
            return planned
        except Exception:
            logger.exception("ensure_wakeups failed")
            raise

    async def preview(self):
        """Monday night: what is still pending this week and a reminder the plan comes Tuesday."""
        try:
            rows = store.pending_wakeups()
            body = "\n".join(schedule_lines(rows)) or "No pending wakeups."
            body += "\n\nTuesday: research at 7:00 AM, roster plan at 8:00 AM, pre-game checks re-planned after."
            discord_out.post(self.cfg, "Week ahead", body, kind="info")
        except Exception:
            logger.exception("preview failed")

    # ------------------------------------------------------------ canary

    async def canary(self, force_post=False):
        """
        Read something that needs the cookies, and confirm they own the team.
        Posts only on failure (or when forced).
        """
        try:
            ctx = self._ctx()
            ctx.pending_transactions()
            owns, owners = ctx.ownership()
            at = datetime.now(timezone.utc).isoformat(timespec="minutes")
            if not owns:
                msg = (f"The cookies read the league but do not own team {self.cfg.team_id} "
                       f"(owned by {', '.join(owners) or 'unknown'}). ESPN will refuse every write with "
                       "AUTH_UNAUTHORIZED_FOR_TEAM. Set AGENT_ESPN_S2 and AGENT_SWID from the owner's browser.")
                store.set_note("canary", {"ok": False, "at": at, "error": msg})
                discord_out.post(self.cfg, "Auth canary: wrong account", msg, kind="alert")
                return False
            store.set_note("canary", {"ok": True, "at": at, "owners": owners})
            if force_post:
                discord_out.line(self.cfg, f"Auth canary: ESPN cookies OK and own team {self.cfg.team_id}.")
            return True
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            store.set_note("canary", {"ok": False, "at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "error": msg})
            discord_out.post(self.cfg, "Auth canary failed",
                             f"ESPN rejected the league cookies or the read failed:\n`{msg[:800]}`\n\n"
                             "Every write and the bot's waiver and trade reports fail until ESPN_S2 and SWID are refreshed "
                             "from the browser and the containers restarted.", kind="alert")
            return False

    # ------------------------------------------------------ reminders

    async def remind_asks(self):
        try:
            for ask_id in ledger.remind_asks(self.cfg, rules_mod.load(self.cfg.data_dir)):
                logger.info("reminded owner about ask %s", ask_id)
        except Exception:
            logger.exception("remind_asks failed")

    # -------------------------------------------------------- offers

    async def poll_offers(self):
        try:
            ctx = self._ctx()
            for offer in ctx.incoming_offers():
                if store.offer_seen(offer["id"]):
                    continue
                store.mark_offer_seen(offer["id"])
                desc = (f"Incoming offer {offer['id']} from {offer['team']}: I give "
                        f"{', '.join(i['player'] or '?' for i in offer['items'] if i['from_team_id'] == self.cfg.team_id)}; "
                        f"I get {', '.join(i['player'] or '?' for i in offer['items'] if i['to_team_id'] == self.cfg.team_id)}. "
                        f"Expires {offer['expires']}.")
                self.queue.submit("trade_review", params={"trade": desc, "offer_id": offer["id"]}, trigger="offer")
                logger.info("queued trade review for offer %s", offer["id"])
            # Outgoing side: proposals the agent sent vanish from ESPN's pending
            # list when answered, with no record of how. Write one down.
            for tid, outcome in ledger.check_proposals(self.cfg, ctx):
                logger.info("proposal %s %s", tid, outcome)
        except Exception:
            logger.exception("poll_offers failed")


def _wakeup_lines(planned):
    return "\n".join(
        f"{run_at.astimezone(roster._eastern()).strftime('%a %b %d %I:%M %p ET')} -> {label} ({', '.join(p['players'])})"
        for run_at, label, p in planned)
