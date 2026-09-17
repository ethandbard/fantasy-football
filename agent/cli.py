"""
Run agent jobs from a shell, without the HTTP server or the clock.

    python -m agent.cli run pregame --param label="Sunday 1:00 PM" --param players="A, B"
    python -m agent.cli run ask --param question="who do I flex" --param asker_team_id=4 --param team_name=Yikes
    python -m agent.cli canary
    python -m agent.cli wakeups        # plan this week's pre-game checks and print them
    python -m agent.cli roster         # print my roster as the tools see it

AGENT_DRY_RUN=true keeps every write a preview. Source config.env first.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(1, os.path.abspath("."))

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _params(pairs):
    out = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        if key == "players":
            value = [p.strip() for p in value.split(",") if p.strip()]
        elif value.isdigit():
            value = int(value)
        out[key] = value
    return out


async def _run(args):
    from agent import config, runner, store
    cfg = config.from_env()
    store.init()
    outcome = await runner.run_job(cfg, args.job, params=_params(args.param), trigger="cli")
    print(json.dumps({k: v for k, v in outcome.items() if k != "brief"}, indent=2, default=str))
    print("\n" + (outcome.get("brief") or "(no brief)"))


async def _canary(args):
    from agent import config, store
    from agent.scheduler import Clock
    cfg = config.from_env()
    store.init()
    ok = await Clock(cfg, None).canary(force_post=True)
    print("canary ok" if ok else "canary FAILED")


def _wakeups(args):
    from agent import config, rules, store
    from agent.espn_ctx import EspnContext
    from agent.tools.state import schedule_week
    import gamedaybot.espn.roster as roster
    cfg = config.from_env()
    store.init()
    ctx = EspnContext(cfg, rules.load(cfg.data_dir), write_enabled=False)
    for run_at, label, params in schedule_week(ctx):
        print(run_at.astimezone(roster._eastern()).strftime("%a %b %d %I:%M %p ET"), "->", label, params["players"])


def _roster(args):
    from agent import config, rules
    from agent.espn_ctx import EspnContext
    import gamedaybot.espn.roster as roster
    cfg = config.from_env()
    ctx = EspnContext(cfg, rules.load(cfg.data_dir), write_enabled=False)
    print(roster.to_table(ctx.my_roster()))
    print("pending:", json.dumps(ctx.pending_transactions(), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("job")
    run.add_argument("--param", action="append", help="key=value, repeatable")
    sub.add_parser("canary")
    sub.add_parser("wakeups")
    sub.add_parser("roster")
    args = parser.parse_args()
    if args.cmd == "run":
        asyncio.run(_run(args))
    elif args.cmd == "canary":
        asyncio.run(_canary(args))
    elif args.cmd == "wakeups":
        _wakeups(args)
    elif args.cmd == "roster":
        _roster(args)


if __name__ == "__main__":
    main()
