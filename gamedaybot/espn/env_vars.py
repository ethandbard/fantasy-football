"""
Reads every runtime setting from the environment into one dict. Docker Compose
supplies these from config.env; see the configuration table in README.md.
"""
import os

# Sentinels standing in for "no ESPN cookies configured", i.e. a public league.
# The rest of the codebase compares against these to decide whether
# private-league calls (waiver transactions) are available at all.
NO_SWID = '{1}'
NO_ESPN_S2 = '1'


def _str_to_bool(value):
    """
    Parses a boolean environment variable.

    Deliberately not defensive about non-str input. The previous version
    swallowed the resulting AttributeError in a bare except and returned
    False, which silently disabled TOP_HALF_SCORING when the value had
    already been parsed upstream. Failing loudly is the point.
    """
    return value.strip().lower() in ("yes", "true", "t", "1")


def get_env_vars():
    data = {
        'ff_start_date': os.environ.get("START_DATE", '2026-09-04'),
        'ff_end_date': os.environ.get("END_DATE", '2027-01-05'),
        'my_timezone': os.environ.get("TIMEZONE", 'America/New_York'),
        'daily_waiver': _str_to_bool(os.environ.get("DAILY_WAIVER", "False")),
        'monitor_report': _str_to_bool(os.environ.get("MONITOR_REPORT", "True")),
        'top_half_scoring': _str_to_bool(os.environ.get("TOP_HALF_SCORING", "False")),
        'league_id': os.environ["LEAGUE_ID"],
        'year': int(os.environ.get("LEAGUE_YEAR", 2026)),
        'espn_s2': os.environ.get("ESPN_S2", NO_ESPN_S2),
    }

    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    # Length rather than truthiness, so a blank-but-set variable is caught too.
    if len(discord_webhook_url) <= 1:
        raise Exception(
            "No DISCORD_WEBHOOK_URL provided. Scheduled reports have nowhere to post.")
    data['discord_webhook_url'] = discord_webhook_url

    # ESPN's SWID cookie is brace-wrapped; tolerate a value pasted without them.
    swid = os.environ.get("SWID", NO_SWID)
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    data['swid'] = swid

    init_msg = os.environ.get("INIT_MSG")
    if init_msg:
        data['init_msg'] = init_msg

    return data
