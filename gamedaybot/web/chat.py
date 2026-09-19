"""
Backend for the dashboard's data chat: a chatlas client over the SQLite
snapshots, with tools that read the same tables the pages do.

The UI (shiny.ui.Chat in web/app.py) owns the widget; this module owns the
configuration, the gate (passphrase and daily caps), the tools the model can
call, and the streaming answer. Nothing here imports Shiny, so the whole
thing is testable with a fake client and a temp database.

Configuration is read from the environment at call time rather than import
time, so a container can be started before the secrets are mounted and the
tests can flip settings with monkeypatch.

    ANTHROPIC_API_KEY       required for the chat to work
    CHAT_MODEL              default claude-haiku-4-5-20251001
    CHAT_PASSPHRASE         the league's shared passphrase; unset disables chat
    CHAT_DAILY_LIMIT        questions per viewer per UTC day (default 20)
    CHAT_LEAGUE_DAILY_LIMIT questions for everyone per UTC day (default 200)
"""
import functools
import hmac
import inspect
import os
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd

import gamedaybot.storage.db as db
import gamedaybot.web.stats as stats

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_DAILY_LIMIT = 20
DEFAULT_LEAGUE_DAILY_LIMIT = 200

# Tool output cap. Haiku reads fast, but a full lineup dump is thousands of
# rows and the model only ever needs the top of a list.
MAX_TOOL_CHARS = 4000

# Lineup slots that mean "did not start". Bench points never count toward a
# team's score, which is the whole reason the leaderboard excludes them.
NON_STARTER_SLOTS = {"BE", "IR"}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _env_int(name, default):
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def model_name():
    return _env("CHAT_MODEL", DEFAULT_MODEL)


def enabled():
    """(True, "") when the chat can run, else (False, reason for the UI)."""
    if not _env("ANTHROPIC_API_KEY"):
        return False, "Chat is not configured: no ANTHROPIC_API_KEY is set."
    if not _env("CHAT_PASSPHRASE"):
        return False, "Chat is off: no league passphrase is set."
    return True, ""


def check_passphrase(text):
    """Constant-time compare of the viewer's entry against CHAT_PASSPHRASE.
    Whitespace either side is ignored; an unset passphrase never matches."""
    expected = _env("CHAT_PASSPHRASE")
    if not expected or text is None:
        return False
    return hmac.compare_digest(str(text).strip().encode(), expected.encode())


def allow(viewer):
    """
    Whether this viewer may ask another question today: (True, "") or
    (False, reason). Both caps count rows in chat_log since UTC midnight,
    so a restart does not reset them.
    """
    viewer_cap = _env_int("CHAT_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)
    league_cap = _env_int("CHAT_LEAGUE_DAILY_LIMIT", DEFAULT_LEAGUE_DAILY_LIMIT)
    if db.chats_today() >= league_cap:
        return False, (f"The league has used all {league_cap} questions for today. "
                       "Try again tomorrow.")
    if db.chats_today(viewer) >= viewer_cap:
        return False, f"You have used all {viewer_cap} of your questions for today."
    return True, ""


# --------------------------------------------------------------------------
# Shared helpers for the tools
# --------------------------------------------------------------------------

def _safe(func):
    """
    Tools must never raise into the model: chatlas would turn the exception
    into a tool error, and Haiku tends to apologise and stop instead of
    trying another tool. A one-line error string keeps the conversation going.
    functools.wraps keeps the signature and docstring chatlas reads the
    schema from.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return _cap(func(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - the whole point is to swallow it
            return f"Error in {func.__name__}: {type(exc).__name__}: {exc}"
    return wrapper


def _cap(text, limit=MAX_TOOL_CHARS):
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0]
    return cut + "\n... (truncated)"


def _table(headers, rows):
    """A markdown table. Cells are stringified; None shows as an empty cell."""
    def cell(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.1f}"
        return str(v).replace("|", "/")
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(cell(v) for v in r) + " |")
    return "\n".join(out)


def _years():
    return [int(y) for y in db.get_years()]


def _resolve_year(year):
    """(year, None) or (None, error message). A missing year means the
    newest season in the database."""
    years = _years()
    if not years:
        return None, "No seasons are in the database yet."
    if year in (None, "", 0, "0"):
        return years[0], None
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None, f"'{year}' is not a season. Seasons on file: {', '.join(map(str, years))}."
    if y not in years:
        return None, f"No data for {y}. Seasons on file: {', '.join(map(str, years))}."
    return y, None


def _scores(year=None):
    df = pd.DataFrame(db.get_all_weekly_scores())
    if df.empty:
        return df
    if year is not None:
        df = df[df["year"] == int(year)]
    return df.reset_index(drop=True)


def _teams(year):
    """Team metadata for a season, plus any name only seen in scores."""
    rows = [t for t in db.get_all_teams() if int(t["year"]) == int(year)]
    seen = {int(t["team_id"]) for t in rows}
    for s in db.get_all_weekly_scores():
        if int(s["year"]) == int(year) and int(s["team_id"]) not in seen:
            rows.append({"year": year, "team_id": s["team_id"], "team_name": s["team_name"],
                         "abbrev": None, "owner": None, "logo_url": None})
            seen.add(int(s["team_id"]))
    return rows


def _manager_of(team_row):
    """What the league calls the team's manager, ESPN's first name failing
    that, or '' when neither is on file."""
    return (db.get_managers().get(stats.manager_key(team_row))
            or str(team_row.get("owner_name") or "").strip())


def _team_names(year):
    return {int(t["team_id"]): t["team_name"] for t in _teams(year)}


def _match_team(year, query):
    """
    (team row, None) or (None, message). Case-insensitive; an exact name,
    abbreviation, owner or manager's first name wins, otherwise a unique
    substring of any of them.
    """
    teams = _teams(year)
    if not teams:
        return None, f"No teams on file for {year}."
    if query is None or not str(query).strip():
        return None, "Which team? " + ", ".join(t["team_name"] for t in teams)
    q = str(query).strip().lower()

    def fields(t):
        return ([str(t.get(k) or "").lower() for k in ("team_name", "abbrev", "owner")]
                + [_manager_of(t).lower()])

    exact = [t for t in teams if q in fields(t)]
    if len(exact) == 1:
        return exact[0], None
    partial = [t for t in teams if any(q in f for f in fields(t) if f)]
    if len(partial) == 1:
        return partial[0], None
    if partial:
        return None, (f"'{query}' matches several teams: "
                      + ", ".join(t["team_name"] for t in partial) + ". Which one?")
    return None, (f"No team matching '{query}' in {year}. Teams: "
                  + ", ".join(t["team_name"] for t in teams))


def _ms_to_date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _fmt_pts(v):
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

@_safe
def list_seasons() -> str:
    """
    List the seasons in the database with how many weeks of scores each has.
    Call this first when the season is not obvious from the question.
    """
    years = _years()
    if not years:
        return "No seasons in the database."
    scores = _scores()
    rows = []
    for y in years:
        ys = scores[scores["year"] == y] if not scores.empty else scores
        weeks = sorted(int(w) for w in ys["week"].unique()) if not ys.empty else []
        teams = len(_teams(y))
        rows.append([y, teams, len(weeks), f"{weeks[0]}-{weeks[-1]}" if weeks else "none"])
    return (f"Newest season: {years[0]}.\n"
            + _table(["Season", "Teams", "Weeks scored", "Week range"], rows))


@_safe
def standings(year: int) -> str:
    """
    Season standings: record, points for and against, differential, streak
    and recent form for every team, best record first.

    Parameters
    ----------
    year : int
        The season, e.g. 2025.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    rec = stats.derive_records(_scores(y))
    if rec.empty:
        return f"No scores collected for {y} yet."
    espn = {int(r["team_id"]): int(r["rank"]) for r in db.get_all_latest_standings()
            if int(r["year"]) == y}
    rows = []
    for i, r in enumerate(rec.itertuples(index=False), start=1):
        rows.append([i, r.team_name, r.record, r.points_for, r.points_against,
                     f"{r.diff:+d}", r.streak, r.form, espn.get(int(r.team_id), "")])
    weeks = int(_scores(y)["week"].max())
    return (f"{y} standings through week {weeks} (ordered by wins, then points for; "
            f"the ESPN rank column is the league's official seed where available).\n"
            + _table(["#", "Team", "Record", "PF", "PA", "Diff", "Streak", "Last 5", "ESPN rank"], rows))


@_safe
def team_summary(year: int, team: str) -> str:
    """
    One team's season: owner, record, points, and every game with the
    opponent, scoreline, projection and result.

    Parameters
    ----------
    year : int
        The season.
    team : str
        Team name, abbreviation or owner; partial matches are fine.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    t, err = _match_team(y, team)
    if err:
        return err
    tid = int(t["team_id"])
    scores = _scores(y)
    rec = stats.derive_records(scores)
    mine = rec[rec["team_id"] == tid]
    head = f"{t['team_name']} ({y})"
    if _manager_of(t):
        head += f", managed by {_manager_of(t)}"
    if t.get("owner"):
        head += f", owner {t['owner']}"
    if mine.empty:
        return head + ": no games scored yet."
    m = mine.iloc[0]
    place = int(mine.index[0]) + 1
    lines = [head,
             f"Record {m['record']} ({place} of {len(rec)} by wins then points), "
             f"PF {m['points_for']}, PA {m['points_against']}, diff {m['diff']:+d}, "
             f"streak {m['streak'] or 'none'}."]
    log = stats.game_log(scores)
    games = log[log["team_id"] == tid].sort_values("week")
    rows = []
    for g in games.itertuples(index=False):
        rows.append([int(g.week), g.opponent_name, _fmt_pts(g.score), _fmt_pts(g.opponent_score),
                     _fmt_pts(g.projected_score), g.result or "pending"])
    lines.append(_table(["Week", "Opponent", "Score", "Opp score", "Projected", "Result"], rows))
    return "\n".join(lines)


@_safe
def head_to_head(year_or_all: str) -> str:
    """
    Every pairing's win-loss record as a grid: rows are the team, columns
    the opponent, cells the team's wins-losses against them.

    Parameters
    ----------
    year_or_all : str
        A season like "2025", or "all" for every season combined.
    """
    key = str(year_or_all or "").strip().lower()
    if key in ("all", "all-time", "alltime", "ever", "career"):
        scores = _scores()
        if scores.empty:
            return "No scores in the database."
        records, _ = stats.head_to_head_all_time(
            scores, pd.DataFrame(db.get_all_teams()), db.get_managers(), _years()[0])
        title = (f"All-time head-to-head across {', '.join(map(str, _years()))}, by manager: "
                 "each row is one person's teams under every name they used, shown under "
                 "their newest team name")
    else:
        y, err = _resolve_year(key)
        if err:
            return err
        records, _ = stats.head_to_head(_scores(y))
        title = f"{y} head-to-head"
    if records.empty:
        return "No games played yet."
    teams = list(records.index)
    rows = []
    for t in teams:
        rows.append([t] + [("—" if t == o else (records.at[t, o] or "0-0")) for o in teams])
    return (title + " (row team's wins-losses vs column team).\n"
            + _table(["Team"] + teams, rows))


@_safe
def rivalry(year: int, team_a: str, team_b: str) -> str:
    """
    The all-time history between two managers, across every season and
    through every team rename: series record, current streak, and every
    meeting with the team names and scores of the day. Use this, not
    head_to_head, for any question about two specific teams or people.

    Parameters
    ----------
    year : int
        A season both teams played in; it decides which team names the two
        are looked up and labelled by. The newest season if unsure.
    team_a : str
        Team name, abbreviation, owner or manager's first name.
    team_b : str
        The other team, the same way.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    a, err = _match_team(y, team_a)
    if err:
        return err
    b, err = _match_team(y, team_b)
    if err:
        return err
    reg = {int(k): int(v["reg_season_count"]) for k, v in db.get_all_league_settings().items()
           if v.get("reg_season_count")}
    r = stats.rivalry(_scores(), pd.DataFrame(db.get_all_teams()), y,
                      int(a["team_id"]), int(b["team_id"]), db.get_managers(), reg)
    who = " vs ".join(f"{r[s]['label']}" + (f" ({r[s]['manager']})" if r[s]["manager"] else "")
                      for s in "ab")
    lines = [who, *stats.rivalry_notes(r)]
    if r["meetings"]:
        rows = [[m["year"], stats.meeting_when(m).split(" ", 2)[2], m["a_name"], _fmt_pts(m["a_score"]), _fmt_pts(m["b_score"]),
                 m["b_name"], "postseason" if m["postseason"] else ""] for m in r["meetings"]]
        lines.append(_table(["Season", "Week", "Team", "Score", "Opp score", "Opponent", ""], rows))
    return "\n".join(lines)


@_safe
def week_results(year: int, week: int) -> str:
    """
    Every matchup's scoreline for one week, with projections and margins.

    Parameters
    ----------
    year : int
        The season.
    week : int
        The week number.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    scores = _scores(y)
    if scores.empty:
        return f"No scores collected for {y}."
    try:
        w = int(week)
    except (TypeError, ValueError):
        return f"'{week}' is not a week number."
    weeks = sorted(int(x) for x in scores["week"].unique())
    if w not in weeks:
        return f"No scores for {y} week {w}. Weeks on file: {weeks[0]}-{weeks[-1]}."
    log = stats.game_log(scores)
    wk = log[log["week"] == w]
    home = wk[wk["is_home"] == 1]
    if home.empty:
        # Older rows without a home flag: dedupe each pairing by lower id.
        home = wk[wk["team_id"] < wk["opponent_id"].fillna(-1)]
    rows = []
    for g in home.sort_values("margin", ascending=False, na_position="last").itertuples(index=False):
        rows.append([g.team_name, _fmt_pts(g.score), _fmt_pts(g.projected_score),
                     g.opponent_name, _fmt_pts(g.opponent_score),
                     "" if pd.isna(g.margin) else f"{g.margin:+.1f}",
                     g.result or "pending"])
    note = ""
    if "matchup_period" in wk.columns and (wk["matchup_period"] != w).any():
        note = (" Note: this week is part of a multi-week playoff round; the round "
                "is decided on the combined total, not this week alone.")
    return (f"{y} week {w} results (home team first).{note}\n"
            + _table(["Home", "Score", "Proj", "Away", "Score", "Margin", "Home result"], rows))


@_safe
def records(year: int) -> str:
    """
    A season's trophies and superlatives: highest and lowest scores, closest
    game, biggest blowout, streaks, best and worst versus projection.

    Parameters
    ----------
    year : int
        The season.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    awards = stats.trophies(_scores(y))
    if not awards:
        return f"No games scored for {y} yet."
    rows = [[a["title"], a["team"], a["week"] if a.get("week") else "", a["detail"]] for a in awards]
    return f"{y} season records.\n" + _table(["Award", "Team", "Week", "Detail"], rows)


@_safe
def all_time_records() -> str:
    """
    League records across every season in the database: single-week highs
    and lows, biggest margins, best season, longest streaks, most dominant
    rivalry.
    """
    scores = _scores()
    if scores.empty:
        return "No scores in the database."
    awards = stats.all_time_trophies(scores)
    if not awards:
        return "No games scored yet."
    rows = [[a["title"], a["team"], a.get("year") or "", a["week"] if a.get("week") else "", a["detail"]]
            for a in awards]
    return (f"All-time records across {', '.join(map(str, _years()))}.\n"
            + _table(["Award", "Team", "Season", "Week", "Detail"], rows))


@_safe
def trades(year: int) -> str:
    """
    Every trade in a season, newest first, with the players each side sent.

    Parameters
    ----------
    year : int
        The season.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    rows = [t for t in db.get_all_trades() if int(t["year"]) == y]
    if not rows:
        return f"No trades recorded for {y}."
    by_date = {}
    for r in rows:
        by_date.setdefault(r["trade_date"], []).append(r)
    out = [f"{len(by_date)} trade(s) in {y}, newest first."]
    for trade_date in sorted(by_date, reverse=True):
        legs = by_date[trade_date]
        sides = {}
        for leg in legs:
            key = (leg.get("from_team_name") or "?", leg.get("to_team_name") or "?")
            name = leg.get("player_name") or "?"
            if leg.get("position"):
                name += f" ({leg['position']})"
            sides.setdefault(key, []).append(name)
        parts = [f"{frm} sent {', '.join(players)} to {to}" for (frm, to), players in sides.items()]
        out.append(f"- {_ms_to_date(trade_date)}: " + "; ".join(parts))
    return "\n".join(out)


@_safe
def recent_moves(year: int, limit: int = 20) -> str:
    """
    The latest roster moves in a season from the activity feed: adds, drops,
    waiver claims and trades, newest first.

    Parameters
    ----------
    year : int
        The season.
    limit : int
        How many moves to return (default 20, max 60).
    """
    y, err = _resolve_year(year)
    if err:
        return err
    try:
        n = max(1, min(int(limit or 20), 60))
    except (TypeError, ValueError):
        n = 20
    rows = [a for a in db.get_all_activity() if int(a["year"]) == y][:n]
    if not rows:
        return f"No activity recorded for {y}."
    table = []
    for a in rows:
        player = a.get("player_name") or "?"
        if a.get("position"):
            player += f" ({a['position']})"
        bid = a.get("bid_amount")
        table.append([_ms_to_date(a["date"]), a.get("team_name") or "", a.get("action") or "",
                      player, f"${bid:.0f}" if bid else ""])
    return (f"Last {len(table)} moves in {y}.\n"
            + _table(["Date", "Team", "Action", "Player", "Bid"], table))


@_safe
def draft(year: int, team: Optional[str] = None) -> str:
    """
    A season's draft board in pick order, or one team's picks.

    Parameters
    ----------
    year : int
        The season.
    team : str, optional
        Limit to one team (name, abbreviation or owner). Omit for the whole draft.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    picks = [p for p in db.get_all_draft_picks() if int(p["year"]) == y]
    if not picks:
        return f"No draft recorded for {y}."
    title = f"{y} draft"
    if team:
        t, err = _match_team(y, team)
        if err:
            return err
        tid = int(t["team_id"])
        picks = [p for p in picks if p.get("team_id") is not None and int(p["team_id"]) == tid]
        title += f", {t['team_name']}'s picks"
    auction = any(p.get("bid_amount") for p in picks)
    rows = []
    for p in picks:
        row = [p["round_num"], p["overall_pick"], p.get("team_name") or "", p.get("player_name") or ""]
        if auction:
            row.append(f"${p['bid_amount']:.0f}" if p.get("bid_amount") else "")
        row.append("keeper" if p.get("keeper") else "")
        rows.append(row)
    headers = ["Rd", "Pick", "Team", "Player"] + (["Bid"] if auction else []) + ["Keeper"]
    return f"{title} ({len(rows)} picks).\n" + _table(headers, rows)


@_safe
def player_leaderboard(year: int, position: Optional[str] = None, limit: int = 15) -> str:
    """
    Top players by points scored while in a starting lineup (bench and IR
    weeks excluded), with the teams that started them.

    Parameters
    ----------
    year : int
        The season.
    position : str, optional
        Filter to one position: QB, RB, WR, TE, K, D/ST.
    limit : int
        How many players (default 15, max 50).
    """
    y, err = _resolve_year(year)
    if err:
        return err
    rows = [r for r in db.get_all_lineup_scores() if int(r["year"]) == y]
    if not rows:
        return f"No lineup data for {y}."
    try:
        n = max(1, min(int(limit or 15), 50))
    except (TypeError, ValueError):
        n = 15
    pos = (position or "").strip().upper().replace("DST", "D/ST")
    names = _team_names(y)
    agg = {}
    for r in rows:
        if (r.get("slot") or "").upper() in NON_STARTER_SLOTS:
            continue
        if pos and (r.get("position") or "").upper() != pos:
            continue
        key = r["player_id"]
        a = agg.setdefault(key, {"name": r.get("player_name") or str(key),
                                 "position": r.get("position") or "",
                                 "points": 0.0, "starts": 0, "teams": set()})
        a["points"] += float(r.get("points") or 0.0)
        a["starts"] += 1
        a["teams"].add(names.get(int(r["team_id"]), str(r["team_id"])))
    if not agg:
        where = f" at {pos}" if pos else ""
        return f"No starters{where} in {y}'s lineup data."
    ranked = sorted(agg.values(), key=lambda a: a["points"], reverse=True)[:n]
    table = [[i, a["name"], a["position"], round(a["points"], 1), a["starts"],
              round(a["points"] / a["starts"], 1) if a["starts"] else 0.0,
              ", ".join(sorted(a["teams"]))]
             for i, a in enumerate(ranked, start=1)]
    weeks = sorted({int(r["week"]) for r in rows})
    scope = f"{pos} " if pos else ""
    return (f"Top {len(table)} {scope}starters in {y} by points scored while starting, "
            f"weeks {weeks[0]}-{weeks[-1]}.\n"
            + _table(["#", "Player", "Pos", "Points", "Starts", "Per start", "Started by"], table))


@_safe
def schedule(year: int, team: str) -> str:
    """
    A team's upcoming games: the weeks not yet scored, with the opponent and
    home/away.

    Parameters
    ----------
    year : int
        The season.
    team : str
        Team name, abbreviation or owner; partial matches are fine.
    """
    y, err = _resolve_year(year)
    if err:
        return err
    t, err = _match_team(y, team)
    if err:
        return err
    tid = int(t["team_id"])
    sched = [s for s in db.get_all_schedule() if int(s["year"]) == y]
    if not sched:
        return f"No schedule on file for {y}."
    scores = _scores(y)
    latest = int(scores["week"].max()) if not scores.empty else 0
    mine = sorted((s for s in sched if int(s["team_id"]) == tid and int(s["week"]) > latest),
                  key=lambda s: int(s["week"]))
    if not mine:
        return f"{t['team_name']} has no unplayed games left on the {y} schedule."
    names = _team_names(y)
    rows = []
    for s in mine:
        opp = s.get("opponent_id")
        rows.append([int(s["week"]),
                     names.get(int(opp), f"team {opp}") if opp is not None else "bye",
                     "home" if s.get("is_home") else "away",
                     _fmt_pts(s.get("projected_score")) if s.get("projected_score") else ""])
    return (f"{t['team_name']}'s remaining {y} schedule (scores collected through week {latest}).\n"
            + _table(["Week", "Opponent", "Home/away", "Projected"], rows))


TOOLS = (
    list_seasons, standings, team_summary, head_to_head, rivalry, week_results,
    records, all_time_records, trades, recent_moves, draft,
    player_leaderboard, schedule,
)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

def system_prompt(today=None):
    today = today or date.today()
    if isinstance(today, (datetime, date)):
        today = today.strftime("%A, %B %d, %Y")
    return f"""You are the data analyst for an 8-team ESPN fantasy football league, answering questions on the league's dashboard.

Today is {today}.

Rules:
- Answer only from the results of the tools. Never guess at scores, records, players or dates. If you have not called a tool yet, call one before answering.
- Call list_seasons when the season is unclear; the newest season is the default. Say which season you are answering about.
- When the data is not there (no rows, a week not yet played, a season not collected), say so plainly in one sentence. Do not invent or estimate.
- You are neutral: no favourites, no trash talk, no speculation about who will win.
- You do not give start/sit, waiver, lineup or trade advice. If asked, say in one sentence that the Discord /ask analyst handles advice and offer the data instead.
- Be concise. Use markdown tables for lists of teams, players or games and short plain sentences otherwise. No headers. Round points to one decimal.
- Team names are ESPN team names; match a manager's nickname or partial name to the closest team when the tool tells you the options.
- Teams change names between seasons and ESPN reuses team ids, so a team name or id does not identify a manager across years. The rivalry tool and the all-time head_to_head grid follow the manager; trust them over matching names yourself.
"""


def make_client(today=None):
    """
    A configured chatlas Chat: Anthropic provider, the analyst system prompt,
    every tool registered. One per browser session, since the chat keeps the
    conversation history.
    """
    from chatlas import ChatAnthropic

    chat = ChatAnthropic(
        model=model_name(),
        system_prompt=system_prompt(today),
        api_key=_env("ANTHROPIC_API_KEY") or None,
        max_tokens=2048,
    )
    for tool in TOOLS:
        chat.register_tool(tool)
    return chat


def _turn_count(client):
    try:
        return len(client.get_turns())
    except Exception:  # noqa: BLE001 - a fake client may not have turns
        return 0


def _tokens_since(client, before):
    """
    (input, output) summed over the assistant turns added since `before`, or
    (None, None) when the client does not expose them. One question can be
    several API round trips when tools are called; each assistant turn
    carries the usage of its own request, so the sum is the question's cost.
    """
    try:
        turns = client.get_turns()[before:]
    except Exception:  # noqa: BLE001
        return None, None
    total_in = total_out = 0
    found = False
    for t in turns:
        if getattr(t, "role", None) != "assistant":
            continue
        tokens = getattr(t, "tokens", None)
        if not tokens or len(tokens) < 2:
            continue
        try:
            total_in += int(tokens[0])
            total_out += int(tokens[1])
            found = True
        except (TypeError, ValueError):
            continue
    return (total_in, total_out) if found else (None, None)


async def answer(client, viewer, question):
    """
    Stream the answer to one question as text chunks, then log it.

    `viewer` is the opaque per-browser key the UI hands over; it is stored
    for the daily caps, not as an identity. The question is logged in a
    finally block so a stream that is cancelled or fails part-way still
    counts: the API call was made either way.
    """
    before = _turn_count(client)
    stream = client.stream_async(question)
    if inspect.isawaitable(stream):
        stream = await stream
    try:
        async for chunk in stream:
            if chunk:
                yield chunk if isinstance(chunk, str) else str(chunk)
    finally:
        input_tokens, output_tokens = _tokens_since(client, before)
        db.log_chat(viewer, question, input_tokens, output_tokens)
