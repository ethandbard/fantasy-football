"""
Tests for the agent's schedule as Discord prints it.

The status command used to print planned wakeups in UTC followed by the fixed
jobs in a mix of UTC and Eastern, each sorted as text -- so a Tuesday job in
Eastern sorted ahead of a Sunday wakeup in UTC, and nobody could read either.
"""
from gamedaybot.discord_bot.formatting import schedule_lines

WAKEUPS = [
    {"run_at": "2026-09-21T23:15:00+00:00", "job": "pregame", "label": "Mon Sep 21 08:15 PM ET kickoff"},
    {"run_at": "2026-09-20T16:00:00+00:00", "job": "pregame", "label": "Sun Sep 20 01:00 PM ET kickoff"},
]
FIRES = [
    {"next": "2026-09-19T04:54+00:00", "job": "tick"},
    {"next": "2026-09-19T04:55+00:00", "job": "poll_offers"},
    {"next": "2026-09-19T05:10+00:00", "job": "expire_asks"},
    {"next": "2026-09-19T05:12+00:00", "job": "remind_asks"},
    {"next": "2026-09-22T06:30-04:00", "job": "recap"},
    {"next": "2026-09-21T21:00-04:00", "job": "preview"},
]


def test_one_list_in_eastern_time_in_true_order():
    assert schedule_lines(WAKEUPS, FIRES) == [
        "• Sun Sep 20, 12:00 PM ET · pre-game lineup check (1:00 PM ET kickoff)",
        "• Mon Sep 21, 7:15 PM ET · pre-game lineup check (8:15 PM ET kickoff)",
        "• Mon Sep 21, 9:00 PM ET · week-ahead note to Discord",
        "• Tue Sep 22, 6:30 AM ET · league recap for the dashboard",
    ]


def test_housekeeping_never_crowds_out_real_jobs():
    lines = schedule_lines([], FIRES, limit=2)
    assert len(lines) == 2 and not any("tick" in ln or "poll" in ln for ln in lines)


def test_a_timestamp_without_an_offset_is_utc_and_unknown_jobs_keep_their_id():
    lines = schedule_lines([{"run_at": "2026-11-08T18:00:00", "job": "lineup", "label": None}])
    # November is standard time: 18:00 UTC is 1:00 PM, not 2:00 PM.
    assert lines == ["• Sun Nov 08, 1:00 PM ET · lineup"]


def test_nothing_scheduled_is_an_empty_list():
    assert schedule_lines(None, None) == []
