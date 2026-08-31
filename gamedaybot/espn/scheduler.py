from apscheduler.schedulers.blocking import BlockingScheduler
from gamedaybot.espn.espn_bot import espn_bot
from gamedaybot.espn.env_vars import get_env_vars


def scheduler():
    """
    This function is used to schedule jobs to send messages.

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    data = get_env_vars()
    game_timezone = 'America/New_York'
    sched = BlockingScheduler(job_defaults={'misfire_grace_time': 15 * 60})
    ff_start_date = data['ff_start_date']
    ff_end_date = data['ff_end_date']
    my_timezone = data['my_timezone']

    # Jobs marked `game_timezone` are pinned to Eastern because they follow the
    # NFL game clock; the rest use TIMEZONE. See the schedule table in README.md.

    sched.add_job(espn_bot, 'cron', ['get_close_scores'], id='close_scores',
                  day_of_week='mon', hour=18, minute=30, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=game_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_power_rankings'], id='power_rankings',
                  day_of_week='tue', hour=18, minute=30, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_final'], id='final',
                  day_of_week='tue', hour=9, minute=0, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_standings'], id='standings',
                  day_of_week='wed', hour=9, minute=0, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    # Waivers process Wednesday morning; DAILY_WAIVER widens the same report to
    # every day. This has to stay a single job -- two jobs sharing an id with
    # replace_existing=True means the second silently overwrites the first.
    waiver_days = '*' if data['daily_waiver'] else 'wed'
    sched.add_job(espn_bot, 'cron', ['get_waiver_report'], id='waiver_report',
                  day_of_week=waiver_days, hour=9, minute=1, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)

    # Trades clear at any hour of any day, so this polls rather than reports:
    # each run stores what it sees and announces only what it has never
    # stored before (see espn_bot.check_trades). minute=7 keeps it clear of
    # the 9:00/9:01 report jobs.
    sched.add_job(espn_bot, 'cron', ['check_trades'], id='check_trades',
                  hour='*', minute=7, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)

    sched.add_job(espn_bot, 'cron', ['get_matchups'], id='matchups',
                  day_of_week='thu', hour=19, minute=30, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=game_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_scoreboard_short'], id='scoreboard1',
                  day_of_week='fri,mon', hour=9, minute=0, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)

    if data['monitor_report']:
        sched.add_job(espn_bot, 'cron', ['get_monitor'], id='monitor',
                      day_of_week='sun', hour=9, minute=0, start_date=ff_start_date, end_date=ff_end_date,
                      timezone=my_timezone, replace_existing=True)

    sched.add_job(espn_bot, 'cron', ['get_scoreboard_short'], id='scoreboard2',
                  day_of_week='sun', hour='16,20', start_date=ff_start_date, end_date=ff_end_date,
                  timezone=game_timezone, replace_existing=True)

    # snapshot for the dashboard: after Monday night football wraps, so the
    # full week's scores are final before we persist them.
    sched.add_job(espn_bot, 'cron', ['collect_snapshot'], id='collect_snapshot',
                  day_of_week='tue', hour=6, minute=0, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=game_timezone, replace_existing=True)

    # Player pool for the draft board. No start/end date -- projections
    # move through the offseason and the Tuesday snapshot is gated on
    # START_DATE, so without this the board would stay empty until kickoff.
    sched.add_job(espn_bot, 'cron', ['collect_players'], id='collect_players',
                  hour=6, minute=15, timezone=my_timezone, replace_existing=True)

    print("Ready!")
    sched.start()
