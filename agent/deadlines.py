"""
When an ask stops being useful, which can be well before it expires.

An ask lives 24 hours by default. A waiver claim queued Tuesday morning
expires Wednesday morning, but ESPN runs waivers at 3:00 AM Eastern, so
approving it at 7:00 AM Wednesday misses this week's run. The deadline
here is the earlier of the two, with a note the Discord message can show,
and the reminder job pings once when it is close.
"""
from datetime import datetime, timedelta, timezone

import gamedaybot.espn.roster as roster

# ESPN rolls the scoring period on Tuesday and processes no claims that day.
NO_WAIVER_WEEKDAY = 1


def _aware(value):
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def waiver_run_after(moment, hour):
    """The next ESPN waiver run at `hour` Eastern strictly after `moment`, skipping Tuesday."""
    local = _aware(moment).astimezone(roster._eastern())
    run = local.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if run <= local:
        run += timedelta(days=1)
    while run.weekday() == NO_WAIVER_WEEKDAY:
        run += timedelta(days=1)
    return run


def describe(ask, rules):
    """{"deadline": ISO minute, "deadline_note": "Wed 03:00 AM ET, when ESPN runs waivers"}."""
    expires = _aware(ask["expires_at"])
    if ask.get("kind") == "waiver":
        run = waiver_run_after(ask["created_at"], rules.get("waiver_process_hour", 3))
        if run < expires:
            return {"deadline": run.isoformat(timespec="minutes"),
                    "deadline_note": f"{run.strftime('%a %I:%M %p ET')}, when ESPN runs waivers"}
    local = expires.astimezone(roster._eastern())
    return {"deadline": expires.isoformat(timespec="minutes"),
            "deadline_note": f"{local.strftime('%a %I:%M %p ET')}, when it expires"}


def due_for_reminder(ask, rules, now=None):
    """True once the ask is within `ask_reminder_hours` of its deadline."""
    now = _aware(now or datetime.now(timezone.utc))
    deadline = _aware(describe(ask, rules)["deadline"])
    return now >= deadline - timedelta(hours=float(rules.get("ask_reminder_hours", 3)))
