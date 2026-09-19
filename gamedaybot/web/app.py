# Shiny dashboard for the fantasy league: this week's results, standings,
# per-team pages, the record book, and the pre-draft player board. Reads the
# SQLite snapshots written by gamedaybot.espn.collector.
#
# Layout and reactive wiring only -- the palette lives in web/theme.py, the
# figures in web/charts.py, the season arithmetic in web/stats.py, the draft
# board columns in web/draft.py, and the visual system in web/www/dashboard.css.
#
# Deliberately comments rather than a module docstring: Shiny Express renders
# top-level string expressions as page content, so a docstring here shows up
# on the live dashboard.
import hashlib
import html
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import shiny.ui as core_ui  # express ui.value_box is a context manager; @render.ui needs the plain function
from shiny import reactive
from shiny.express import app_opts, input, render, ui
from shinywidgets import render_widget

import gamedaybot.storage.db as db
import gamedaybot.web.charts as charts
import gamedaybot.web.chat as datachat
import gamedaybot.web.draft as draft
import gamedaybot.web.stats as stats
import gamedaybot.web.theme as theme

db.init_db()

CURRENT_YEAR = datetime.now().year

# How often to check whether the collector has written new snapshots. The
# check is a single cheap aggregate query, not a full reload.
DB_POLL_SECONDS = 30

WWW = Path(__file__).parent / "www"

# Team logos are materialised from database blobs into files next to the
# database itself (the one place the container can always write) and served
# under /logos. Files rather than data URIs because one uploaded logo can be
# a quarter megabyte, and a URI that size would be repeated into the DOM once
# per row and into every chart's JSON; a URL is fetched once and cached.
LOGO_DIR = Path(db.DB_PATH).parent / "logos"
LOGO_DIR.mkdir(parents=True, exist_ok=True)
app_opts(static_assets={"/logos": LOGO_DIR})

_LOGO_EXT = {
    "image/svg+xml": "svg",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}

NAV_ITEMS = [
    ("week", "This week"),
    ("next", "Next up"),
    ("draft", "Draft"),
    ("league", "League"),
    ("teams", "Teams"),
    ("players", "Players"),
    ("records", "Records"),
    ("chat", "Chat"),
]

# The team the agent service manages, for the manager's log on its page.
AGENT_TEAM_ID = int(os.environ.get("TEAM_ID") or 0)

ui.page_opts(window_title="Fantasy Football Dashboard", fillable=False)

ui.head_content(
    # Bootstrap 5.3 reads this attribute and switches its own components to
    # dark; dashboard.css then redefines the --bs-* variables it uses. Doing
    # it this way keeps a Sass compiler out of the container, which is what
    # customising ui.Theme would have cost.
    core_ui.tags.script("document.documentElement.dataset.bsTheme = 'dark'"),
    # One delegated listener for every click-driven input that a per-session
    # @render.ui builds -- the nav, week rail, team rail, scorebug rows,
    # movers, week bests, standings rows, ledger rows. Shiny Express tagifies
    # the page once at startup and serves that markup to every session, so
    # none of this data-dependent, per-session UI can be wired with a real
    # input id ahead of time. Any element carrying data-set/data-value routes
    # through here instead of getting its own listener.
    # A per-browser token for the chat's daily cap, and the league passphrase
    # the viewer typed last time, both from localStorage. Neither is an
    # identity; the token only makes the cap survive a refresh.
    core_ui.tags.script(
        "(function () {"
        "  function send() {"
        "    if (!window.Shiny || !Shiny.setInputValue) { return setTimeout(send, 200); }"
        "    var t = null, p = null;"
        "    try {"
        "      t = localStorage.getItem('ff-viewer');"
        "      if (!t) { t = Math.random().toString(36).slice(2) + Date.now().toString(36);"
        "               localStorage.setItem('ff-viewer', t); }"
        "      p = localStorage.getItem('ff-chat-pass');"
        "    } catch (e) {}"
        "    Shiny.setInputValue('viewer_token', t || 'anon');"
        "    if (p) { Shiny.setInputValue('chat_pass_saved', p); }"
        "  }"
        "  document.addEventListener('shiny:connected', send);"
        "  document.addEventListener('click', function (e) {"
        "    if (e.target && e.target.id === 'chat_go') {"
        "      var v = document.getElementById('chat_pass');"
        "      try { if (v && v.value) { localStorage.setItem('ff-chat-pass', v.value); } } catch (err) {}"
        "    }"
        "  });"
        "})();"
    ),
    core_ui.tags.script(
        "document.addEventListener('click', function (e) {"
        "  var el = e.target.closest && e.target.closest('[data-set]');"
        "  if (el && window.Shiny) {"
        "    Shiny.setInputValue(el.dataset.set, el.dataset.value,"
        "                        {priority: 'event'});"
        "  }"
        "});"
    ),
    # Plotly measures its container once, at the moment it draws, and never
    # again. Both charts are built while the League screen is hidden -- they
    # have to be, since shinywidgets fixes an output's slot where it is
    # defined -- so both drew at Plotly's default 700px and stayed there
    # inside a 1232px column.
    #
    # Reacting to an event is not enough on its own. The wrapper is already
    # full width when the server paints it, so it never resizes; and Plotly
    # arrives as a module that can finish loading after the last DOM change,
    # so a pass triggered by that change finds no window.Plotly and gives up
    # with nothing left to re-trigger it. Anything moving therefore starts a
    # few seconds of cheap re-checks rather than a single pass.
    #
    # Each check compares the figure's own recorded width against the element
    # it was drawn into, which is what makes it safe to run from a
    # MutationObserver: the redraw is itself a DOM change, and once the two
    # agree the next pass does nothing.
    core_ui.tags.script(
        "(function () {"
        "  var timer = null, ticks = 0;"
        "  function fix(wrap) {"
        "    var plot = wrap.querySelector('.js-plotly-plot');"
        "    if (!plot || !window.Plotly || !plot._fullLayout) { return; }"
        "    var width = plot.clientWidth;"
        "    if (width > 0 && Math.abs(plot._fullLayout.width - width) > 1) {"
        "      window.Plotly.Plots.resize(plot);"
        "    }"
        "  }"
        "  function sweep() {"
        "    document.querySelectorAll('.chart-wrap').forEach(fix);"
        "  }"
        "  function poke() {"
        "    ticks = 0;"
        "    if (timer) { return; }"
        "    timer = setInterval(function () {"
        "      sweep();"
        "      if (++ticks > 40) { clearInterval(timer); timer = null; }"
        "    }, 250);"
        "  }"
        "  function start() {"
        "    if (window.ResizeObserver) {"
        "      var ro = new ResizeObserver(function (entries) {"
        "        entries.forEach(function (entry) { fix(entry.target); });"
        "      });"
        "      var seen = new WeakSet();"
        "      var observe = function () {"
        "        document.querySelectorAll('.chart-wrap').forEach(function (el) {"
        "          if (!seen.has(el)) { seen.add(el); ro.observe(el); }"
        "        });"
        "      };"
        "      observe();"
        "      document.addEventListener('shiny:value', observe);"
        "    }"
        "    new MutationObserver(poke).observe("
        "      document.body, {childList: true, subtree: true});"
        "    window.addEventListener('resize', poke);"
        "    poke();"
        "  }"
        "  if (document.readyState === 'loading') {"
        "    document.addEventListener('DOMContentLoaded', start);"
        "  } else { start(); }"
        "})();"
    ),
    core_ui.tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
    core_ui.tags.link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    core_ui.tags.link(
        rel="stylesheet",
        href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800"
             "&family=JetBrains+Mono:wght@400;600&display=swap",
    ),
)
ui.include_css(WWW / "dashboard.css")


# --------------------------------------------------------------------- state
#
# Five reactive values replace the old three plus the week-range slider.
# `team` and `week` are stored as an explicit override or None; None means
# "use the computed default", so the default (latest week, current 1 seed)
# keeps tracking the data until the reader actually picks something.
screen = reactive.value("week")
week = reactive.value(None)
scope = reactive.value("reg")
sort = reactive.value("seed")
team = reactive.value(None)
chart = reactive.value("race")
h2h_scope = reactive.value("season")
draft_sort = reactive.value("draft_rank")
draft_dir = reactive.value("asc")
draft_club = reactive.value("ALL")
_nav_touched = reactive.value(False)
_auto_screened = reactive.value(False)


# Everything below reads through these two polls, so a snapshot written by the
# collector reaches an already-open browser tab within DB_POLL_SECONDS -- no
# restart, no rebuild, no page refresh.
@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_scores():
    return pd.DataFrame(db.get_all_weekly_scores())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_standings():
    return pd.DataFrame(db.get_all_latest_standings())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_players():
    return pd.DataFrame(db.get_all_players())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_teams():
    return pd.DataFrame(db.get_all_teams())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _manager_names():
    """{manager key: what the league calls them}, from dev/set_managers.py."""
    return db.get_managers()


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_picks():
    return pd.DataFrame(db.get_all_draft_picks())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_schedule():
    return pd.DataFrame(db.get_all_schedule())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_trades():
    return pd.DataFrame(db.get_all_trades())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_content():
    """Prose the agents wrote for the dashboard, every season, newest first."""
    return db.get_all_site_content()


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_lineups():
    return pd.DataFrame(db.get_all_lineup_scores())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_settings():
    return db.get_all_league_settings()


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_activity():
    return pd.DataFrame(db.get_all_activity())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _agent_log():
    return db.get_agent_transactions(limit=60)


def _season_lineups():
    df = _all_lineups()
    return df[df["year"] == _year()] if not df.empty else df


def _season_activity():
    df = _all_activity()
    return df[df["year"] == _year()] if not df.empty else df


def _season_settings():
    """The season's league settings row, or {} before the first collect."""
    return _all_settings().get(_year(), {})


def _slot_counts():
    """Starting slot counts for the season, with ESPN's standard lineup as
    the fallback so bench regrets still compute on a backfilled year."""
    counts = _season_settings().get("slot_counts") or {}
    return counts or {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "D/ST": 1, "K": 1}


def _content(kind, weeks):
    """One kind of dashboard prose for the selected season, limited to the
    given weeks, newest week first."""
    year = _year()
    wanted = set(int(w) for w in weeks)
    return [r for r in _all_content()
            if r["kind"] == kind and int(r["year"]) == year and int(r["week"]) in wanted]


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_logos():
    """
    (year, team_id) -> /logos URL for every stored logo, writing any blob
    that has no file yet. The content hash is in the filename, so a changed
    logo gets a new URL and no browser cache ever serves the old image; the
    superseded files are a few orphaned kilobytes.
    """
    out = {}
    for row in db.get_all_team_logos():
        ext = _LOGO_EXT.get(row["content_type"] or "", "png")
        content = row["content"]
        digest = hashlib.md5(content).hexdigest()[:10]
        name = f"{row['year']}-{row['team_id']}-{digest}.{ext}"
        path = LOGO_DIR / name
        if not path.exists():
            path.write_bytes(content)
        out[(int(row["year"]), int(row["team_id"]))] = f"/logos/{name}"
    return out


def _year():
    return int(input.year()) if input.year() else CURRENT_YEAR


def _season_scores():
    """Every collected week of the selected season, unfiltered by scope."""
    df = _all_scores()
    return df[df["year"] == _year()] if not df.empty else df


def _season_players():
    """Player pool for the selected season."""
    df = _all_players()
    return df[df["year"] == _year()] if not df.empty else df


def _season_teams():
    df = _all_teams()
    return df[df["year"] == _year()] if not df.empty else df


def _season_picks():
    df = _all_picks()
    return df[df["year"] == _year()] if not df.empty else df


def _season_schedule():
    df = _all_schedule()
    return df[df["year"] == _year()] if not df.empty else df


def _season_trades():
    df = _all_trades()
    return df[df["year"] == _year()] if not df.empty else df


def _managers():
    """Every team-season tied to its manager, labelled for the selected
    season -- see stats.managers()."""
    return stats.managers(_all_scores(), _all_teams(), _manager_names(), _year())


def _reg_weeks_by_year():
    """{year: regular-season length}, for telling a playoff meeting apart."""
    return {int(y): int(s["reg_season_count"]) for y, s in _all_settings().items()
            if s.get("reg_season_count")}


def _standings_df():
    df = _all_standings()
    return df[df["year"] == _year()] if not df.empty else df


def _latest_week():
    df = _season_scores()
    return int(df["week"].max()) if not df.empty else None


def _current_week():
    """The selected week for This week -- an override, or the latest collected."""
    override = week.get()
    latest = _latest_week()
    if override is not None and latest is not None and 1 <= override <= latest:
        return override
    return latest


def _scope_bounds():
    """Week bounds for League, Teams and Records, from the scope segment."""
    df = _season_scores()
    if df.empty:
        return (1, 0)
    total = int(df["week"].max())
    reg = stats.regular_season_weeks(_standings_df(), df) or total

    if scope.get() == "post":
        return (reg + 1, total) if reg < total else (1, total)
    if scope.get() == "full":
        return (1, total)
    return (1, reg)


def _scope_scores():
    """The season narrowed to the scope segment's week bounds."""
    df = _season_scores()
    if df.empty:
        return df
    lo, hi = _scope_bounds()
    return df[(df["week"] >= lo) & (df["week"] <= hi)]


def _styles():
    """Team colors for the season, stable across every screen."""
    df = _season_scores()
    if df.empty:
        return {}
    return theme.team_styles(zip(df["team_id"], df["team_name"]))


def _logos_by_name():
    """
    team_name -> logo data URI, across every season, with a colored-monogram
    stand-in for any team that has no stored logo. Name-keyed because that is
    how the charts, scorebugs and head-to-head grid identify teams; when a
    name persists across seasons the newest season's logo wins.

    Colors for the monograms come from theme.team_styles over each season's
    own roster -- the same id-ordered rule _styles() applies to scores -- so
    the stand-in matches the team's line color even before a week is played.
    """
    logos = _all_logos()
    teams_df = _all_teams()
    if teams_df.empty:
        return {}
    out = {}
    for year, group in teams_df.groupby("year"):  # ascending: newest wins
        year_styles = theme.team_styles(zip(group["team_id"], group["team_name"]))
        for _, t in group.iterrows():
            uri = logos.get((int(year), int(t["team_id"])))
            if not uri:
                color = year_styles.get(t["team_name"], {}).get("color")
                uri = theme.monogram_data_uri(t["team_name"], color)
            out[t["team_name"]] = uri
    return out


def _season_logo_ids():
    """team_id -> logo data URI for the selected season, monogram fallback
    included -- for the id-keyed Next up cards."""
    logos = _all_logos()
    teams_df = _season_teams()
    if teams_df.empty:
        return {}
    year = _year()
    year_styles = theme.team_styles(zip(teams_df["team_id"], teams_df["team_name"]))
    out = {}
    for _, t in teams_df.iterrows():
        uri = logos.get((year, int(t["team_id"])))
        if not uri:
            color = year_styles.get(t["team_name"], {}).get("color")
            uri = theme.monogram_data_uri(t["team_name"], color)
        out[int(t["team_id"])] = uri
    return out


def _logo_img(uri, cls="team-logo"):
    """The one img builder every logo spot shares; None-safe so callers can
    pass a lookup miss straight through."""
    if not uri:
        return None
    return core_ui.tags.img(src=uri, class_=cls, alt="", aria_hidden="true")


def _name_with_logo(name, uri, mirrored=False, name_cls="name"):
    """A team name with its logo beside it, logo toward the score column:
    `mirrored` for the left (right-aligned) side of a scorebug."""
    label = core_ui.span(name, class_=name_cls)
    img = _logo_img(uri)
    if img is None:
        return label
    children = (label, img) if mirrored else (img, label)
    return core_ui.div(*children, class_="name-row")


def _default_team():
    """The current 1 seed, until the reader picks a team of their own."""
    standings = _standings_df()
    if not standings.empty:
        return str(standings.sort_values("rank").iloc[0]["team_name"])
    records = stats.derive_records(_scope_scores())
    return str(records.iloc[0]["team_name"]) if not records.empty else None


def _current_team():
    picked = team.get()
    styles = _styles()
    if picked and picked in styles:
        return picked
    return _default_team()


def _freshness():
    """
    "Synced 3h ago" -- relative rather than a clock time, since collected_at
    is stored in UTC and the server has no idea what timezone the reader is
    in, so this is both shorter and the only version that cannot be wrong.
    """
    _all_scores()  # re-read whenever the collector writes
    ago = _ago(db.last_collected())
    return f"synced {ago}" if ago else "live"


def _ago(stamp):
    """A UTC "YYYY-MM-DD HH:MM:SS" stamp as "just now", "12m ago", "3h ago",
    or "2d ago"; None when the stamp is missing or unreadable."""
    if not stamp:
        return None
    try:
        written = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    minutes = max(int((datetime.utcnow() - written).total_seconds() // 60), 0)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 48 * 60:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


def _clickable(tag, set_input, value, *children, **attrs):
    """A div/button wired to the delegated click listener in ui.head_content."""
    attrs.setdefault("tabindex", "0")
    return tag(*children, **{"data-set": set_input, "data-value": str(value)}, **attrs)


# ------------------------------------------------------------------- top bar

with ui.div(class_="topbar"):
    with ui.div(class_="mark"):
        core_ui.div(class_="mark-block")
        core_ui.span("FANTASY FOOTBALL", class_="wordmark")

    @render.ui
    def top_nav():
        current = screen()
        items = [
            _clickable(
                core_ui.tags.button, "nav_pick", key,
                label,
                class_="nav-item active" if key == current else "nav-item",
                type="button",
            )
            for key, label in NAV_ITEMS
        ]
        return core_ui.div(*items, class_="nav")

    core_ui.div(class_="spacer")

    with ui.div(class_="season-pick"):
        ui.input_select(
            "year", None,
            choices=[str(y) for y in (db.get_years() or [CURRENT_YEAR])],
        )

    with ui.div(class_="topbar-links"):
        core_ui.tags.a(
            "ethandbard.com", href="https://ethandbard.com",
            target="_blank", rel="noopener",
        )
        core_ui.tags.a(
            "Docs", href="https://fantasy-docs.ethandbard.com/",
            target="_blank", rel="noopener",
        )

    @render.ui
    def synced_label():
        text = _freshness()
        # "synced" is split off so a phone can drop it: the topbar has no room
        # for the long form ("synced just now" overran it by 19px), and the
        # live dot beside the time already carries that half of the meaning.
        if text.startswith("synced "):
            return core_ui.span(
                core_ui.span("synced ", class_="synced-word"),
                text[len("synced "):],
                class_="synced",
            )
        return core_ui.span(text, class_="synced")


@reactive.effect
def _sync_season_choices():
    """
    Keeps the season dropdown in step with the database. Without this the
    choices are whatever existed when the process started: Shiny Express
    tagifies the UI once at startup and serves that same markup to every
    request, so a new season would stay invisible until a container restart.
    """
    scores = _all_scores()
    players = _all_players()
    teams = _all_teams()
    picks = _all_picks()
    years = set()
    for frame in (scores, players, teams, picks):
        if not frame.empty:
            years.update(int(y) for y in frame["year"].unique().tolist())
    years = sorted(years, reverse=True) or [CURRENT_YEAR]
    choices = [str(y) for y in years]

    with reactive.isolate():
        current = input.year()

    core_ui.update_select(
        "year", choices=choices,
        selected=current if current in choices else choices[0],
    )


# ------------------------------------------------------------ input routing

@reactive.effect
@reactive.event(input.nav_pick)
def _on_nav():
    screen.set(input.nav_pick())
    _nav_touched.set(True)


@reactive.effect
def _default_to_draft():
    """
    Open on Draft when this season has a player pool and no weekly scores,
    which is the preseason case. Stops deciding once the reader has picked
    a destination or the first poll has enough to choose.
    """
    if _auto_screened.get() or _nav_touched.get():
        return
    scores = _season_scores()
    players = _season_players()
    if scores.empty and not players.empty:
        screen.set("draft")
        _auto_screened.set(True)
    elif not scores.empty or not players.empty:
        _auto_screened.set(True)


@reactive.effect
@reactive.event(input.draft_club)
def _on_draft_club():
    if input.draft_club():
        draft_club.set(input.draft_club())


@reactive.effect
@reactive.event(input.draft_sort)
def _on_draft_sort():
    key = input.draft_sort()
    if not key:
        return
    if key == draft_sort.get():
        draft_dir.set("desc" if draft_dir.get() == "asc" else "asc")
    else:
        draft_sort.set(key)
        draft_dir.set("asc" if key in draft.ASC_KEYS else "desc")


@reactive.effect
@reactive.event(input.week_pick)
def _on_week_pick():
    try:
        week.set(int(input.week_pick()))
    except (TypeError, ValueError):
        pass


@reactive.effect
@reactive.event(input.team_pick)
def _on_team_pick():
    """Every team-focused click routes through here: standings, scorebugs,
    movers, week bests, the ledger, and the team pills themselves."""
    team.set(input.team_pick())
    screen.set("teams")


@reactive.effect
@reactive.event(input.team_select)
def _on_team_select():
    """The mobile full-width <select> -- writes the same reactive.value the
    pill rail does, per the handoff."""
    if input.team_select():
        team.set(input.team_select())


@reactive.effect
def _on_scope_input():
    if input.scope() is not None and input.scope() != scope.get():
        scope.set(input.scope())


@reactive.effect
def _on_chart_input():
    if input.chart_pick() is not None and input.chart_pick() != chart.get():
        chart.set(input.chart_pick())


@reactive.effect
def _on_h2h_scope_input():
    if input.h2h_scope_pick() is not None and input.h2h_scope_pick() != h2h_scope.get():
        h2h_scope.set(input.h2h_scope_pick())


@reactive.effect
def _on_sort_input():
    if input.sort() is not None and input.sort() != sort.get():
        sort.set(input.sort())


@reactive.effect
def _advance_week():
    """
    Advances the default week when a new snapshot lands, as long as the
    reader has not moved it themselves. A season's week 1 then appears on its
    own with no refresh.
    """
    latest = _latest_week()
    if latest is None:
        return
    if week.get() is None:
        return  # already following latest
    if week.get() > latest:
        week.set(None)


# ---------------------------------------------------------- screen: THIS WEEK

def _week_headline(week_scores):
    log = stats.game_log(week_scores)
    played = log[log["result"] != ""]
    matchups = played[played["is_home"] == 1]
    parts = []
    if not matchups.empty:
        closest = matchups.loc[matchups["margin"].abs().idxmin()]
        if closest["margin"] >= 0:
            winner_name, loser_name = closest["team_name"], closest["opponent_name"]
        else:
            winner_name, loser_name = closest["opponent_name"], closest["team_name"]
        parts.append(
            f"{winner_name} survived {loser_name} by {abs(closest['margin']):.1f}"
        )
    standings = _standings_df()
    if not standings.empty:
        leader = standings.sort_values("rank").iloc[0]
        record = f"{int(leader['wins'])}-{int(leader['losses'])}"
        parts.append(f"{leader['team_name']} holds the 1 seed at {record}")
    if not parts:
        return "No completed matchups yet this week."
    return ", and ".join(parts) + "."


def _results_rows(week_scores, styles, logos=None):
    logos = logos or {}
    log = stats.game_log(week_scores)
    played = log[(log["result"] != "") & (log["is_home"] == 1)]
    if played.empty:
        return core_ui.p("No games played yet this week.", class_="empty-note")

    proj = stats.vs_projection(week_scores)
    proj_by_team = dict(zip(proj["team_name"], proj["vs_proj"])) if not proj.empty else {}

    rows = []
    for _, g in played.sort_values("margin", key=lambda s: s.abs()).iterrows():
        winner = g if g["margin"] >= 0 else None
        if winner is None:
            win_name, win_score = g["opponent_name"], g["opponent_score"]
            lose_name, lose_score = g["team_name"], g["score"]
        else:
            win_name, win_score = g["team_name"], g["score"]
            lose_name, lose_score = g["opponent_name"], g["opponent_score"]
        margin = abs(g["margin"])

        if margin < 1:
            tag_class, tag_text = "margin nailbiter", "nailbiter"
        elif margin > 40:
            tag_class, tag_text = "margin", "blowout"
        else:
            tag_class, tag_text = "margin", f"{margin:.1f} pts"

        win_delta = proj_by_team.get(win_name)
        lose_delta = proj_by_team.get(lose_name)

        def delta_span(name, value):
            if value is None or pd.isna(value):
                return None
            cls = "delta over" if value >= 0 else "delta under"
            return core_ui.span(f"{value:+.1f} vs proj", class_=cls)

        rows.append(_clickable(
            core_ui.div, "team_pick", win_name,
            core_ui.div(
                _name_with_logo(win_name, logos.get(win_name), mirrored=True),
                delta_span(win_name, win_delta),
                class_="side left win",
            ),
            core_ui.span(f"{win_score:.1f}", class_="score"),
            core_ui.span("–", class_="dash"),
            core_ui.span(f"{lose_score:.1f}", class_="score"),
            core_ui.div(
                _name_with_logo(lose_name, logos.get(lose_name)),
                delta_span(lose_name, lose_delta),
                class_="side right lose",
            ),
            core_ui.span(tag_text, class_=tag_class),
            class_="scorebug",
            role="button",
        ))
    return core_ui.div(*rows, class_="resultrows")


def _own_average_rows(week_scores):
    if week_scores.empty:
        return None
    season = _season_scores()
    avg = season.groupby("team_name")["score"].mean()
    this_week = week_scores.set_index("team_name")["score"]
    deviation = (this_week - avg).dropna().sort_values(ascending=False)
    if deviation.empty:
        return None
    largest = deviation.abs().max() or 1.0

    rows = []
    for team_name, dev in deviation.items():
        pct = min(abs(dev) / largest, 1.0) * 100
        cls = "over" if dev >= 0 else "under"
        rows.append(core_ui.div(
            core_ui.span(team_name, class_="team"),
            core_ui.div(
                core_ui.div(class_=f"bar {cls}", style=f"width:{pct / 2:.1f}%"),
                class_="track",
            ),
            core_ui.span(f"{dev:+.1f}", class_=f"value {cls}"),
            class_="avgrow",
        ))
    return core_ui.div(*rows, class_="avgrows")


def _movers(wk):
    """
    Who climbed and who slid, comparing the standing after `wk` with the
    standing after the week before it.

    Takes the week rather than reading _current_week(), because a playoff
    round wants its *last* week: rank_by_week only credits a round's win on
    the week the round ends, so passing the round's first week -- which is
    what the week rail selects -- would compare a week to itself and report
    that nobody moved.
    """
    ranked = stats.rank_by_week(_season_scores())
    if ranked.empty or wk is None:
        return None
    this = ranked[ranked["week"] == wk].set_index("team_name")["rank"]
    prev = ranked[ranked["week"] == wk - 1].set_index("team_name")["rank"]
    if this.empty:
        return None
    moved = (prev - this).reindex(this.index).fillna(0)
    if not moved.any():
        # Five rows of "no change" is not a movers list. Common at the end of
        # a playoff round, where every surviving team wins on the same week
        # and the order comes out of it untouched.
        return core_ui.div(
            core_ui.p("Movers", class_="section-label"),
            core_ui.p("Nobody changed places.", class_="empty-note"),
            class_="rail-block",
        )
    order = moved.abs().sort_values(ascending=False).index[:5]

    rows = []
    for name in order:
        delta = int(moved.get(name, 0))
        if delta > 0:
            cls, glyph = "move up", f"▲ {delta}"
        elif delta < 0:
            cls, glyph = "move down", f"▼ {abs(delta)}"
        else:
            cls, glyph = "move mute", "—"
        rows.append(_clickable(
            core_ui.div, "team_pick", name,
            core_ui.span(str(int(this[name])), class_="rank"),
            core_ui.span(name, class_="name"),
            core_ui.span(glyph, class_=cls),
            class_="moverrow", role="button",
        ))
    return core_ui.div(core_ui.p("Movers", class_="section-label"),
                       core_ui.div(*rows), class_="rail-block")


def _week_bests(week_scores):
    awards = stats.trophies(week_scores)
    by_title = {a["title"]: a for a in awards}
    wanted = [
        ("Highest Score", "TOP SCORE"),
        ("Biggest Blowout", "BIGGEST BLOWOUT"),
        ("Closest Matchup", "CLOSEST GAME"),
        ("Best vs Projection", "BEST VS PROJECTION"),
    ]
    rows = []
    for key, label in wanted:
        a = by_title.get(key)
        if not a:
            continue
        value = a["detail"].split(" — ")[0].split(",")[0]
        rows.append(_clickable(
            core_ui.div, "team_pick", a["focus"],
            core_ui.div(
                core_ui.span(label, class_="label"),
                core_ui.span(a["team"], class_="team"),
                class_="stack",
            ),
            core_ui.span(value, class_="value"),
            class_="bestrow", role="button",
        ))
    if not rows:
        return None
    return core_ui.div(core_ui.p("Week bests", class_="section-label"),
                       core_ui.div(*rows), class_="rail-block")


def _round_weeks(season, wk):
    """
    Every week sharing wk's matchup_period, so a playoff round picked from
    the week rail (which points at the round's first week) pulls in both.
    Returns [wk] for a regular week, or when matchup_period isn't collected.
    """
    if wk is None or season.empty or "matchup_period" not in season.columns:
        return [wk] if wk is not None else []
    row = season[season["week"] == wk]
    if row.empty or pd.isna(row["matchup_period"].iloc[0]):
        return [wk]
    mp = row["matchup_period"].iloc[0]
    weeks = sorted(season[season["matchup_period"] == mp]["week"].unique().tolist())
    return weeks or [wk]


def _round_headline(round_log, weeks):
    """A sentence for a playoff round, in place of the weekly one -- the
    weekly headline talks about a single week's margin, which is not what a
    two-week round is decided on."""
    parts = [f"Weeks {weeks[0]} and {weeks[-1]} count as one game — "
             f"the two-week total decides it."]
    matchups = round_log[(round_log["result"] != "") & (round_log["is_home"] == 1)]
    if not matchups.empty:
        closest = matchups.loc[matchups["margin"].abs().idxmin()]
        if closest["margin"] >= 0:
            winner, loser = closest["team_name"], closest["opponent_name"]
        else:
            winner, loser = closest["opponent_name"], closest["team_name"]
        parts.append(f"The closest was {winner} over {loser} by "
                     f"{abs(closest['margin']):.1f}.")
    return " ".join(parts)


def _round_rows(round_scores, weeks):
    """
    The round's matchups, one row per game.

    Built on the same scorebug markup a regular week uses, with each side's
    week-by-week split tucked under its name. The previous version listed all
    eight teams in score order with a W or an L beside each, which told the
    reader everything except the one thing a results list is for -- who
    played whom.
    """
    log = stats.matchup_log(round_scores)
    if log.empty:
        return core_ui.p("No data for this round.", class_="empty-note")

    # One row per matchup rather than two: every game appears once from each
    # side, so the home row alone is the whole matchup.
    matchups = log[log["is_home"] == 1]
    if matchups.empty:
        return core_ui.p("No completed matchups in this round.", class_="empty-note")

    per_week = round_scores.set_index(["team_id", "week"])["score"]
    logos = _logos_by_name()

    def side(team_id, name, css):
        splits = [
            core_ui.span(f"Wk {w} · {per_week[(team_id, w)]:.1f}", class_="round-week")
            for w in weeks if (team_id, w) in per_week.index
        ]
        return core_ui.div(
            _name_with_logo(name, logos.get(name), mirrored="left" in css),
            core_ui.div(*splits, class_="round-weeks"),
            class_=css,
        )

    rows = []
    for _, g in matchups.sort_values("margin", key=lambda c: c.abs()).iterrows():
        if g["margin"] >= 0:
            win_id, win_name, win_score = g["team_id"], g["team_name"], g["score"]
            lose_id, lose_name = g["opponent_id"], g["opponent_name"]
            lose_score = g["opponent_score"]
        else:
            win_id, win_name, win_score = g["opponent_id"], g["opponent_name"], g["opponent_score"]
            lose_id, lose_name = g["team_id"], g["team_name"]
            lose_score = g["score"]

        margin = abs(g["margin"])
        tag_text = "blowout" if margin > 80 else f"{margin:.1f} pts"

        rows.append(_clickable(
            core_ui.div, "team_pick", win_name,
            side(win_id, win_name, "side left win"),
            core_ui.span(f"{win_score:.1f}", class_="score"),
            core_ui.span("–", class_="dash"),
            core_ui.span(f"{lose_score:.1f}", class_="score"),
            side(lose_id, lose_name, "side right lose"),
            core_ui.span(tag_text, class_="margin"),
            class_="scorebug", role="button",
        ))
    return core_ui.div(*rows, class_="resultrows")


@render.ui
def screen_week():
    if screen() != "week":
        return None

    season = _season_scores()
    if season.empty:
        return core_ui.div(
            core_ui.p("No score data collected yet for this season.", class_="empty-note"),
            class_="screen",
        )

    wk = _current_week()
    weeks = _round_weeks(season, wk)
    is_round = len(weeks) > 1
    week_scores = season[season["week"] == wk] if wk else season.iloc[0:0]
    latest = _latest_week()

    if is_round:
        round_scores = season[season["week"].isin(weeks)]
        round_label = f"ROUND {weeks[0]}–{weeks[-1]}"
        stamp = "Latest" if latest in weeks else f"Weeks {weeks[0]}–{weeks[-1]}"
        return core_ui.div(
            core_ui.div(
                core_ui.h1(round_label, class_="screen-title wk"),
                core_ui.span(f"{stamp} · {_freshness()}", class_="stamp"),
                class_="title-row",
            ),
            core_ui.p(_round_headline(stats.matchup_log(round_scores), weeks),
                       class_="headline"),
            core_ui.div(
                core_ui.div(
                    core_ui.p("Round results", class_="section-label"),
                    _round_rows(round_scores, weeks),
                    class_="results",
                ),
                core_ui.div(
                    # The round's last week, so the movement shown is the
                    # round's own -- see _movers.
                    _movers(weeks[-1]),
                    class_="rail",
                ),
                class_="week-body",
            ),
            _recap_block(_content("recap", weeks)),
            class_="screen",
        )

    stamp = "Latest" if wk == latest else f"Week {wk}"

    return core_ui.div(
        core_ui.div(
            core_ui.h1(f"WEEK {wk}", class_="screen-title wk"),
            core_ui.span(f"{stamp} · {_freshness()}", class_="stamp"),
            class_="title-row",
        ),
        core_ui.p(_week_headline(week_scores), class_="headline"),
        core_ui.div(
            core_ui.div(
                core_ui.p("Results", class_="section-label"),
                _results_rows(week_scores, _styles(), _logos_by_name()),
                class_="results",
            ),
            core_ui.div(
                core_ui.p("Against their own average", class_="section-label"),
                _own_average_rows(week_scores),
            ),
            core_ui.div(
                _movers(wk),
                _week_bests(week_scores),
                class_="rail",
            ),
            class_="week-body",
        ),
        _recap_block(_content("recap", [wk])),
        class_="screen",
    )


def _recap_block(rows):
    """The week's recap, written by the league recap agent on Tuesday morning."""
    return _prose_block(rows, "recap")


def _prose_block(rows, noun, extra_class=""):
    """
    One piece of agent-written prose (the newest row handed in), or None
    when nothing has been written for these weeks yet. `noun` names it in
    the label: "Week 3 recap", "Week 4 preview", "Week 3 power rankings".

    The body is markdown from a model that only ever saw league data, but
    it is still escaped before rendering so a stray angle bracket in a team
    name (or anything a future job pulls from the web) is text, never
    markup. Markdown emphasis, headings, and lists survive the escape.
    """
    if not rows:
        return None
    row = rows[0]
    ago = _ago(row.get("written_at"))
    label = f"Week {int(row['week'])} {noun}"
    if ago:
        label += f" · written {ago}"
    return core_ui.div(
        core_ui.p(label, class_="section-label"),
        core_ui.h2(row["title"], class_="recap-title") if row.get("title") else None,
        core_ui.div(ui.markdown(html.escape(row["body"], quote=False)), class_="recap-body"),
        class_=f"recap-section {extra_class}".strip(),
    )


def _moves_list(activity_df, logos, limit=40):
    """
    The add/drop/waiver ledger, newest first, grouped by day. One row per
    player moved; a claim and its drop share a timestamp and read as one
    move. Trades live in their own ledger.
    """
    if activity_df.empty:
        return core_ui.p("No moves recorded yet this season.", class_="empty-note")
    df = activity_df.sort_values("date", ascending=False).head(limit)
    verbs = {"WAIVER ADDED": "claimed", "FA ADDED": "added", "DROPPED": "dropped", "ADDED": "added"}
    groups = []
    for day, day_rows in df.groupby(df["date"].map(lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()), sort=False):
        items = []
        for _, r in day_rows.iterrows():
            verb = verbs.get(r["action"], str(r["action"]).lower())
            cls = "add" if verb in ("claimed", "added") else "drop"
            items.append(_clickable(
                core_ui.div, "team_pick", r["team_name"] or "",
                _logo_img(logos.get(r["team_name"]), "team-logo move-logo"),
                core_ui.span(r["team_name"] or "—", class_="move-team"),
                core_ui.span(verb, class_=f"move-verb {cls}"),
                core_ui.span(r["player_name"] or "?", class_="move-player"),
                core_ui.span(r["position"] or "", class_="move-pos"),
                class_="move-row", role="button",
            ))
        groups.append(core_ui.div(
            core_ui.span(day.strftime("%a %b %d").upper(), class_="move-day"),
            *items,
            class_="move-group",
        ))
    return core_ui.div(*groups, class_="moves-list")


def _managers_log(rows):
    """
    The agent's executed ESPN moves with its stated reasons, newest first.
    Only the moves themselves: no plans, no research, nothing that has not
    already happened on ESPN where the league can see it anyway.
    """
    done = [r for r in rows if r.get("ok") and not r.get("dry_run")]
    if not done:
        return None
    items = []
    for r in done[:15]:
        stamp = r.get("at") or ""
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).strftime("%b %d")
        except ValueError:
            when = stamp[:10]
        items.append(core_ui.div(
            core_ui.span(when, class_="log-when"),
            core_ui.div(
                core_ui.span(r.get("description") or r.get("kind") or "", class_="log-what"),
                core_ui.span(r.get("reason") or "", class_="log-why") if r.get("reason") else None,
                class_="log-stack",
            ),
            class_="log-row",
        ))
    return core_ui.div(
        core_ui.p("Manager's log · moves the team's agent made", class_="section-label"),
        *items,
        class_="managers-log",
    )


@render.ui
def week_rail():
    if screen() != "week":
        return None
    season = _season_scores()
    if season.empty:
        return None
    total = int(season["week"].max())
    current = _current_week()
    reg = stats.regular_season_weeks(_standings_df(), season) or total
    reg = min(reg, total)

    buttons = [
        _clickable(
            core_ui.tags.button, "week_pick", n,
            str(n),
            class_="week-btn active" if n == current else "week-btn",
            type="button",
        )
        for n in range(1, reg + 1)
    ]

    if total > reg and "matchup_period" in season.columns:
        playoffs = season[season["week"] > reg]
        for i, mp in enumerate(sorted(playoffs["matchup_period"].dropna().unique()), start=1):
            round_weeks = sorted(playoffs[playoffs["matchup_period"] == mp]["week"].unique())
            if not round_weeks:
                continue
            label = (f"R{i} · {round_weeks[0]}-{round_weeks[-1]}"
                      if len(round_weeks) > 1 else f"R{i} · {round_weeks[0]}")
            buttons.append(_clickable(
                core_ui.tags.button, "week_pick", round_weeks[0],
                label,
                class_="week-btn round active" if current in round_weeks else "week-btn round",
                type="button",
            ))

    return core_ui.div(
        core_ui.span("WEEK", class_="eyebrow"),
        *buttons,
        class_="weekrail",
    )


# ----------------------------------------------------------- screen: NEXT UP

def _nu_side(team_id, names, rec_by_id, scores_by_id, css, logos=None, manager=None):
    """One team's half of an upcoming-matchup card: logo, name, manager,
    record, season average, and last-five pips. Preseason, only the logo,
    name and manager exist yet."""
    logos = logos or {}
    name = names.get(team_id, f"Team {team_id}")
    children = [_name_with_logo(name, logos.get(team_id),
                                mirrored="left" in css)]

    r = rec_by_id.get(team_id)
    scores = scores_by_id.get(team_id, [])
    if r is not None and scores:
        avg = sum(scores) / len(scores)
        sub = f"{r.record} · avg {avg:.1f}"
        children.append(core_ui.span(
            f"{manager} · {sub}" if manager else sub, class_="nu-sub",
        ))
        pips = [core_ui.span(class_=f"pip {c.lower()}") for c in r.form.split()]
        if pips:
            children.append(core_ui.div(*pips, class_="form-pips nu-pips"))
    elif manager:
        children.append(core_ui.span(manager, class_="nu-sub"))

    return _clickable(core_ui.div, "team_pick", name, *children,
                      class_=css, role="button")


def _nu_rivalry(r):
    """
    The history under an upcoming-matchup card: the all-time series as a
    headline, every past meeting as a pip (green when the left team won, red
    when the right did -- the same sides as the probability bar above it),
    grouped by season, then the streak and last-meeting lines.

    All of it is between managers, not team names: a team renamed every year
    would otherwise read "first meeting" every September.
    """
    notes = stats.rivalry_notes(r)
    children = [core_ui.p(notes[0], class_="nu-h2h")]

    if r["meetings"]:
        strip = []
        for year in r["seasons"]:
            pips = []
            for m in (m for m in r["meetings"] if m["year"] == year):
                cls = {"a": "w", "b": "l"}.get(m["winner"], "t")
                pips.append(core_ui.span(
                    class_=f"pip {cls}{' post' if m['postseason'] else ''}",
                    title=(f"{stats.meeting_when(m)}{' (postseason)' if m['postseason'] else ''}: "
                           f"{m['a_name']} {m['a_score']:.1f} – {m['b_score']:.1f} {m['b_name']}"),
                ))
            strip.append(core_ui.span(
                core_ui.span(str(year), class_="nu-series-year"),
                core_ui.span(*pips, class_="form-pips"),
                class_="nu-series-season",
            ))
        children.append(core_ui.div(*strip, class_="nu-series"))

    if len(notes) > 1:
        children.append(core_ui.p(" ".join(notes[1:]), class_="nu-rivalry-detail"))
    return core_ui.div(*children, class_="nu-rivalry")


def _nu_prob_bar(name_a, name_b, p):
    """The rough win-probability read, drawn as a split bar."""
    if p is None:
        return None
    pa = round(p * 100)
    return core_ui.div(
        core_ui.div(
            core_ui.span(f"{pa}%", class_="nu-prob-num left"),
            core_ui.div(
                core_ui.div(class_="nu-prob-fill", style=f"width:{pa}%"),
                class_="nu-prob-track",
            ),
            core_ui.span(f"{100 - pa}%", class_="nu-prob-num right"),
            class_="nu-prob-row",
        ),
        class_="nu-prob",
    )


@render.ui
def screen_next():
    if screen() != "next":
        return None

    def empty(text):
        return core_ui.div(
            core_ui.h1("NEXT UP", class_="screen-title wk"),
            core_ui.p(text, class_="empty-note"),
            class_="screen",
        )

    sched = _season_schedule()
    if sched.empty:
        return empty("No schedule collected yet for this season.")

    season = _season_scores()
    wk = stats.upcoming_week(sched, season)
    if wk is None:
        return empty(f"The {_year()} season has been played out — "
                     "nothing left on the schedule.")

    teams_df = _season_teams()
    names = (dict(zip(teams_df["team_id"], teams_df["team_name"]))
             if not teams_df.empty else {})

    week_sched = sched[sched["week"] == wk]
    matchups = week_sched[week_sched["is_home"] == 1]
    proj = week_sched.set_index("team_id")["projected_score"]

    rec_df = stats.derive_records(season)
    rec_by_id = ({r.team_id: r for r in rec_df.itertuples()}
                 if not rec_df.empty else {})
    scores_by_id = ({tid: g["score"].tolist()
                     for tid, g in season.groupby("team_id")}
                    if not season.empty else {})
    all_scores, all_teams, manager_names = _all_scores(), _all_teams(), _manager_names()
    reg_weeks = _reg_weeks_by_year()
    logo_ids = _season_logo_ids()

    # A playoff round spans more than one week; say so instead of pretending
    # the round's first week is a normal game.
    mp = week_sched["matchup_period"].iloc[0]
    round_weeks = sorted(sched[sched["matchup_period"] == mp]["week"].unique().tolist())
    if len(round_weeks) > 1:
        note = (f"Weeks {round_weeks[0]}–{round_weeks[-1]} count as one "
                "playoff round — the multi-week total decides it. "
                "Projections refresh daily until kickoff.")
    else:
        note = ("Projections are ESPN's, refreshed daily until kickoff. "
                "The probability bar is a rough read from each team's scored "
                "weeks — not a real model, and it knows nothing about "
                "injuries or byes.")

    cards = []
    for _, m in matchups.iterrows():
        home_id, away_id = int(m["team_id"]), int(m["opponent_id"])
        home_name = names.get(home_id, f"Team {home_id}")
        away_name = names.get(away_id, f"Team {away_id}")

        def proj_text(tid):
            value = proj.get(tid)
            return f"{value:.1f}" if pd.notna(value) and value else "—"

        home_proj, away_proj = proj.get(home_id), proj.get(away_id)
        if pd.notna(home_proj) and pd.notna(away_proj) and home_proj and away_proj:
            gap = home_proj - away_proj
            if abs(gap) < 0.5:
                tag_text = "even"
            else:
                fav = home_name if gap > 0 else away_name
                tag_text = f"{fav} by {abs(gap):.1f}"
        else:
            tag_text = "no projection"

        p = stats.win_probability(scores_by_id.get(home_id, []),
                                  scores_by_id.get(away_id, []))
        # `before` keeps a week that is scored but still on screen out of
        # its own history.
        rivalry = stats.rivalry(all_scores, all_teams, _year(), home_id, away_id,
                                manager_names, reg_weeks, before=(_year(), wk))

        cards.append(core_ui.div(
            core_ui.div(
                _nu_side(home_id, names, rec_by_id, scores_by_id, "side left",
                         logo_ids, rivalry["a"]["manager"]),
                core_ui.span(proj_text(home_id), class_="score"),
                core_ui.span("–", class_="dash"),
                core_ui.span(proj_text(away_id), class_="score"),
                _nu_side(away_id, names, rec_by_id, scores_by_id, "side right",
                         logo_ids, rivalry["b"]["manager"]),
                core_ui.span(tag_text, class_="margin", title="Projected margin"),
                class_="scorebug nu-bug",
            ),
            _nu_prob_bar(home_name, away_name, p),
            _nu_rivalry(rivalry),
            class_="nextup-card",
        ))

    if not cards:
        return empty("No matchups scheduled for the coming week.")

    return core_ui.div(
        core_ui.div(
            core_ui.h1(f"NEXT UP · WEEK {wk}", class_="screen-title wk"),
            core_ui.span(_freshness(), class_="stamp"),
            class_="title-row",
        ),
        core_ui.p(note, class_="headline"),
        core_ui.div(*cards, class_="nextup-list"),
        _prose_block(_content("preview", [wk]), "preview", "preview-section"),
        class_="screen",
    )


# ------------------------------------------------------------- screen: DRAFT

with ui.div(id="draft-controls-wrap", class_="controlrow"):
    with ui.div(class_="left"):
        with ui.div(class_="control"):
            core_ui.span("Position", class_="control-label")
            with ui.div(class_="segment"):
                ui.input_radio_buttons(
                    "draft_pos", None,
                    {"ALL": "All", "QB": "QB", "RB": "RB", "WR": "WR",
                     "TE": "TE", "K": "K", "DST": "D/ST"},
                    selected="ALL", inline=True,
                )
        with ui.div(class_="control"):
            core_ui.span("View", class_="control-label")
            with ui.div(class_="segment"):
                ui.input_radio_buttons(
                    "draft_view", None,
                    {"list": "List", "board": "Board"},
                    selected="list", inline=True,
                )
        with ui.div(class_="control draft-search"):
            core_ui.span("Find", class_="control-label")
            ui.input_text("draft_q", None, placeholder="Name or team")


@render.ui
def draft_controls_visibility_style():
    display = "flex" if screen() == "draft" else "none"
    return core_ui.tags.style(f"#draft-controls-wrap {{ display: {display}; }}")


def _draft_value(key, row):
    if key in row.index:
        value = row[key]
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return value
    stats_map = row["projected_stats"] if "projected_stats" in row.index else None
    if isinstance(stats_map, dict):
        return stats_map.get(key)
    return None


def _draft_cell(key, row):
    if key == "name":
        tag = draft.injury_tag(row["injury_status"] if "injury_status" in row.index else None)
        chip = core_ui.span(tag, class_="inj") if tag else None
        return core_ui.div(
            core_ui.span(str(row["name"] if "name" in row.index else ""), class_="pname"),
            chip,
            class_="player-cell",
        )
    if key == "position":
        pos = str(row["position"]) if "position" in row.index and pd.notna(row["position"]) else ""
        slug = pos.lower().replace("/", "")
        return core_ui.span(pos, class_=f"pos-badge pos-{slug}")
    value = _draft_value(key, row)
    if key == "adp_delta":
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "—"
        rounded = int(round(value))
        if rounded > 0:
            return core_ui.span(draft.format_stat(value, "signed"), class_="delta-pos")
        if rounded < 0:
            return core_ui.span(draft.format_stat(value, "signed"), class_="delta-neg")
        return "0"
    if key in ("pro_team", "draft_team", "pick_label"):
        return draft.format_stat(value, "text")
    if key == "percent_owned":
        return draft.format_stat(value, "pct")
    if key in ("draft_rank", "bye_week"):
        return draft.format_stat(value, "int")
    return draft.format_stat(value)


def _pick_tagline(kind, row):
    """One steal/reach line: tag, player, then pick and ADP delta."""
    delta = draft.format_stat(row["adp_delta"], "signed")
    return (
        core_ui.span(kind, class_="tag steal" if kind == "STEAL" else "tag reach"),
        core_ui.span(row["name"], class_="pick-name"),
        core_ui.span(f"{row['pick_label']} · {delta} vs ADP", class_="pick-meta"),
    )


def _steal_reach_chips(steals, reaches):
    chips = [core_ui.div(*_pick_tagline("STEAL", row), class_="draft-chip")
             for _, row in steals.iterrows()]
    chips += [core_ui.div(*_pick_tagline("REACH", row), class_="draft-chip")
              for _, row in reaches.iterrows()]
    if not chips:
        return None
    return core_ui.div(*chips, class_="draft-chips")


def _club_card(card, rank=None):
    picks = []
    if card["best_value"] is not None:
        picks.append(core_ui.div(*_pick_tagline("STEAL", card["best_value"]),
                                 class_="card-pick"))
    if card["biggest_reach"] is not None:
        picks.append(core_ui.div(*_pick_tagline("REACH", card["biggest_reach"]),
                                 class_="card-pick"))
    return core_ui.div(
        core_ui.div(
            core_ui.span(str(rank), class_="card-rank") if rank else None,
            core_ui.span(card["club"], class_="card-club"),
            class_="card-head",
        ),
        core_ui.div(
            core_ui.span(f"{card['projected']:,.0f}", class_="card-proj"),
            core_ui.span("PROJ PTS DRAFTED", class_="card-proj-label"),
            class_="card-projrow",
        ),
        core_ui.p(card["shape"], class_="card-shape"),
        *picks,
        class_="club-card",
    )


def _draft_grid_ui(clubs, rows, logos):
    cells = [core_ui.div("RD", class_="gcell ghead gr-label")]
    for club in clubs:
        logo = logos.get(club)
        cells.append(core_ui.div(
            core_ui.tags.img(src=logo, class_="glogo") if logo else None,
            core_ui.span(club, class_="gclub"),
            class_="gcell ghead",
        ))
    for round_num, row_cells in rows:
        cells.append(core_ui.div(str(round_num), class_="gcell gr-label"))
        for cell in row_cells:
            if cell is None:
                cells.append(core_ui.div(class_="gcell gempty"))
                continue
            pos = str(cell["position"]) if pd.notna(cell["position"]) else ""
            slug = pos.lower().replace("/", "")
            cells.append(core_ui.div(
                core_ui.div(
                    core_ui.span(pos, class_=f"pos-badge pos-{slug}"),
                    core_ui.span(str(int(cell["overall_pick"])), class_="gpick"),
                    class_="gtop",
                ),
                core_ui.span(cell["name"], class_="gname"),
                class_=f"gcell gplayer gpos-{slug}",
            ))
    template = f"36px repeat({len(clubs)}, minmax(118px, 1fr))"
    return core_ui.div(
        core_ui.div(*cells, class_="draft-grid",
                    style=f"--grid-cols: {template}"),
        class_="draft-grid-wrap",
    )


def _draft_return_df():
    picks, players = _season_picks(), _season_players()
    if picks.empty or players.empty or "total_points" not in players.columns:
        return pd.DataFrame()
    if players["total_points"].fillna(0).sum() <= 0:
        return pd.DataFrame()
    return stats.draft_return(picks, players)


@render.ui
def draftret_visibility_style():
    display = "block" if screen() == "draft" and not _draft_return_df().empty else "none"
    return core_ui.tags.style(f"#draftret-wrap {{ display: {display}; }}")


with ui.div(id="draftret-wrap", class_="race-wrap analytics-wrap draftret-wrap"):
    core_ui.h1("DRAFT RETURN", class_="screen-title board")
    core_ui.p("Season points by pick", class_="section-label")
    core_ui.p("The line is the typical return at each pick, a rolling median. Above it the pick "
              "outperformed its slot; the five biggest gaps each way are named.", class_="screen-note")

    with ui.div(class_="chart-wrap"):
        @render_widget
        def draftret_widget():
            dr = _draft_return_df()
            if dr.empty:
                return charts.as_widget(charts.empty_fig("No season points yet."))
            return charts.as_widget(charts.draft_return_scatter(dr, _styles()))

    @render.ui
    def draftret_lists():
        dr = _draft_return_df()
        if dr.empty:
            return None

        def col(title, tag):
            part = dr[dr["tag"] == tag].sort_values("delta", ascending=(tag == "bust"))
            items = [_clickable(
                core_ui.div, "team_pick", r["team_name"] or "",
                core_ui.span(f"#{int(r['overall_pick'])}", class_="dr-pick"),
                core_ui.span(r["player_name"], class_="dr-player"),
                core_ui.span(r["team_name"] or "", class_="dr-team"),
                core_ui.span(f"{r['delta']:+.0f}", class_=f"dr-delta {tag}"),
                class_="dr-row", role="button",
            ) for _, r in part.iterrows()]
            return core_ui.div(core_ui.p(title, class_="section-label"), *items, class_="dr-col")

        return core_ui.div(col("Steals", "steal"), col("Busts", "bust"), class_="dr-lists")



@render.ui
def screen_draft():
    if screen() != "draft":
        return None

    pool = _season_players()
    if pool.empty:
        return core_ui.div(
            core_ui.h1("DRAFT BOARD", class_="screen-title board"),
            core_ui.p(
                "No player pool collected yet. The container pulls it on "
                "startup, or run python dev/collect_players.py.",
                class_="empty-note",
            ),
            class_="screen",
        )

    position = input.draft_pos() or "ALL"
    if position == "DST":
        position = "D/ST"
    query = input.draft_q() or ""
    club = draft_club.get() or "ALL"
    sort_key = draft_sort.get() or "draft_rank"
    descending = draft_dir.get() == "desc"
    view = input.draft_view() or "list"

    board = draft.attach_picks(pool, _season_picks())
    flat = draft.flatten_stats(board)
    filtered = draft.filter_players(flat, position, query, club=club)
    ordered = draft.sort_players(filtered, sort_key, descending)
    total = len(ordered)
    cap = None if query or position != "ALL" or club != "ALL" else 400
    shown = ordered.head(cap) if cap and total > cap else ordered

    cols = draft.columns_for(position)
    col_template = draft.column_template(cols)

    def header_cell(key, label):
        active = key == sort_key
        arrow = ""
        if active:
            arrow = " ↑" if draft_dir.get() == "asc" else " ↓"
        return _clickable(
            core_ui.tags.button, "draft_sort", key,
            label + arrow,
            class_="col active" if active else "col",
            type="button",
        )

    head = core_ui.div(
        *[header_cell(key, label) for key, label in cols],
        class_="draft-head",
    )

    rows = []
    for _, row in shown.iterrows():
        cells = []
        for key, _label in cols:
            value = _draft_cell(key, row)
            extra = " fpts" if key == "projected_points" else ""
            extra += " player" if key == "name" else ""
            extra += " rk" if key == "draft_rank" else ""
            extra += " club" if key == "draft_team" else ""
            if isinstance(value, str):
                cells.append(core_ui.span(value, class_="dcell" + extra))
            else:
                cells.append(core_ui.div(value, class_="dcell" + extra))
        rows.append(core_ui.div(*cells, class_="draft-row"))

    picks = _season_picks()
    n_picks = 0 if picks.empty else len(picks)
    if cap and total > cap:
        note = (
            f"Projected FPTS use this league's scoring. "
            f"Showing {len(shown)} of {total} — pick a position, a club, or search to go deeper."
        )
    elif n_picks:
        note = (
            f"Projected FPTS use this league's scoring. "
            f"{n_picks} picks in, {total} player{'s' if total != 1 else ''} shown."
        )
    else:
        note = (
            f"Projected FPTS use this league's scoring. "
            f"{total} player{'s' if total != 1 else ''}."
        )

    clubs = []
    teams = _season_teams()
    club_names = []
    if not picks.empty:
        club_names = list(picks.drop_duplicates("team_name")["team_name"].dropna())
    elif not teams.empty:
        club_names = list(teams["team_name"].dropna())
    if club_names:
        clubs.append(_clickable(
            core_ui.tags.button, "draft_club", "ALL", "All clubs",
            class_="team-pill active" if club == "ALL" else "team-pill",
            type="button",
        ))
        for name in club_names:
            clubs.append(_clickable(
                core_ui.tags.button, "draft_club", name, name,
                class_="team-pill active" if club == name else "team-pill",
                type="button",
            ))
        if n_picks:
            clubs.append(_clickable(
                core_ui.tags.button, "draft_club", draft.UNDRAFTED, "Undrafted",
                class_="team-pill active" if club == draft.UNDRAFTED else "team-pill",
                type="button",
            ))

    # Chips follow the position tab so QB shows QB steals, not the same
    # league-wide three on every view. Club and search stay out of it: the
    # club card already covers the former, and chips flickering while
    # typing a name helps nobody.
    chip_pool = board if position == "ALL" else board[board["position"] == position]
    steals, reaches = draft.steals_and_reaches(chip_pool)
    chips = _steal_reach_chips(steals, reaches)

    if view == "board" and n_picks:
        cards = draft.club_summaries(board)
        grid_clubs, grid_rows = draft.grid_data(board)
        logos = _logos_by_name()
        content = [
            _draft_grid_ui(grid_clubs, grid_rows, logos),
            core_ui.p("Projected draft standings", class_="section-label"),
            core_ui.div(
                *[_club_card(card, rank=i + 1) for i, card in enumerate(cards)],
                class_="club-cards",
            ),
        ]
        note = (f"{n_picks} picks over {len(grid_rows)} rounds. Cards are "
                "ordered by total projected points drafted.")
    else:
        content = []
        if club not in ("ALL", draft.UNDRAFTED):
            card = next((c for c in draft.club_summaries(board)
                         if c["club"] == club), None)
            if card is not None:
                content.append(core_ui.div(_club_card(card),
                                           class_="club-cards single"))
        content.append(core_ui.div(
            core_ui.div(head, *rows, class_="draft-table"),
            class_="draft-wrap",
            style=f"--draft-cols:{col_template}",
        ))

    return core_ui.div(
        core_ui.div(
            core_ui.h1("DRAFT BOARD", class_="screen-title board"),
            core_ui.span(_freshness(), class_="stamp"),
            class_="title-row",
        ),
        core_ui.p(note, class_="screen-note"),
        chips,
        core_ui.div(*clubs, class_="teamrail draft-clubs") if clubs else None,
        *content,
        class_="screen",
    )


# ------------------------------------------------------------- screen: LEAGUE

with ui.div(id="scope-sort-wrap", class_="controlrow"):
    with ui.div(class_="left"):
        # Every segment carries a label naming what it drives. Three
        # unlabelled pill groups in a row left the reader to work out which
        # one moved the standings and which one moved the grid at the bottom
        # of the page by trying them.
        with ui.div(class_="control"):
            core_ui.span("Weeks", class_="control-label")
            with ui.div(class_="segment"):
                # Built once at page level (not inside a @render.ui tree, which
                # was the root cause of the echo loop -- see _on_scope_input).
                # scope/sort are read via reactive.isolate() here since this
                # code runs once at page build time, outside any reactive
                # context; `selected` only needs each value's initial default.
                with reactive.isolate():
                    initial_scope, initial_sort = scope.get(), sort.get()
                ui.input_radio_buttons(
                    "scope", None,
                    {"reg": "Regular", "post": "Playoffs", "full": "Full"},
                    selected=initial_scope, inline=True,
                )

        @render.ui
        def _range_note_ui():
            lo, hi = _scope_bounds()
            if lo > hi:
                return None
            text = f"weeks {lo}–{hi}" if lo != hi else f"week {lo}"
            return core_ui.span(text, class_="range-note")

    with ui.div(id="sort-only-wrap", class_="control"):
        core_ui.span("Sort", class_="control-label")
        with ui.div(class_="segment"):
            ui.input_radio_buttons(
                "sort", None,
                {"seed": "Seed", "points": "Points", "form": "Form"},
                selected=initial_sort, inline=True,
            )


@render.ui
def scope_sort_visibility_style():
    """Shows the scope/sort segments per screen without rebuilding them --
    the same display-toggle trick as race-wrap, so the controls that drive
    `scope` and `sort` are never recreated by the value they set."""
    current = screen()
    # Teams is in this list because screen_teams reads _scope_scores(): the
    # game log and every profile stat were already being cut at the regular
    # season's end by a control the reader could not see.
    scope_display = "flex" if current in ("league", "teams", "records") else "none"
    sort_display = "flex" if current == "league" else "none"
    return core_ui.tags.style(
        f"#scope-sort-wrap {{ display: {scope_display}; }} "
        f"#sort-only-wrap {{ display: {sort_display}; }}"
    )


with reactive.isolate():
    initial_h2h_scope = h2h_scope.get()

with ui.div(id="h2h-scope-wrap", class_="controlrow"):
    with ui.div(class_="control"):
        core_ui.span("Head to head", class_="control-label")
        with ui.div(class_="segment"):
            ui.input_radio_buttons(
                "h2h_scope_pick", None,
                {"season": "Season", "all": "All-time"},
                selected=initial_h2h_scope, inline=True,
            )


@render.ui
def h2h_scope_visibility_style():
    display = "flex" if screen() in ("league", "teams") else "none"
    return core_ui.tags.style(f"#h2h-scope-wrap {{ display: {display}; }}")


def _h2h_frame():
    """The head-to-head (records, margins) pair for whichever scope the
    Season | All-time toggle is set to.

    "Season" honours the Regular/Playoffs/Full segment, because the grid sits
    on the same screens that segment already narrows -- reading the whole
    season under a heading that says "weeks 15-18" was the grid disagreeing
    with the range note directly above it.
    """
    if h2h_scope.get() == "all":
        return stats.head_to_head_all_time(_all_scores(), _all_teams(),
                                           _manager_names(), _year())
    return stats.head_to_head(_scope_scores())


def _h2h_matrix(records_tbl, margins_tbl, current=None, logos=None):
    """
    The head-to-head grid: row team vs column opponent, cells tinted by
    average margin (green toward the row team's wins, red toward its
    losses), diagonal blank. When `current` is set, that team's row and
    column are raised and everything else dims -- the team-screen variant.
    """
    teams_list = list(records_tbl.index)
    logos = logos or {}
    if not teams_list:
        return core_ui.p("No matchups in this range.", class_="empty-note")

    def head_cls(name, base):
        return f"{base} raised" if current and name == current else base

    def head(name, base):
        # A long team name is ellipsed to fit its column, so the full one is
        # carried on the title attribute rather than lost.
        return core_ui.span(
            _logo_img(logos.get(name), "team-logo h2h-logo"),
            core_ui.span(name, class_="h2h-head-name"),
            class_=head_cls(name, base), title=name,
        )

    header = [core_ui.span("", class_="h2h-corner")]
    header += [head(opp, "h2h-col-head") for opp in teams_list]
    rows = [core_ui.div(*header, class_="h2h-grid-row h2h-head")]

    for team_name in teams_list:
        if current:
            row_cls = "h2h-grid-row raised" if team_name == current else "h2h-grid-row dim"
        else:
            row_cls = "h2h-grid-row"
        cells = [head(team_name, "h2h-row-head")]
        for opp in teams_list:
            if opp == team_name:
                cells.append(core_ui.span("", class_="h2h-cell blank"))
                continue
            rec = records_tbl.at[team_name, opp]
            if not rec:
                cells.append(core_ui.span("", class_="h2h-cell empty"))
                continue
            margin = margins_tbl.at[team_name, opp]
            pct = min(abs(margin) / 40, 1.0) if pd.notna(margin) else 0.0
            color = "var(--win)" if margin >= 0 else "var(--loss)"
            alpha = round(18 + pct * 42)
            cell_cls = head_cls(opp, "h2h-cell")
            cells.append(_clickable(
                core_ui.div, "team_pick", team_name,
                core_ui.span(rec, class_="h2h-cell-record"),
                core_ui.span(f"{margin:+.1f}", class_="h2h-cell-margin"),
                class_=cell_cls,
                style=f"background:color-mix(in srgb, {color} {alpha}%, transparent)",
                role="button",
            ))
        rows.append(core_ui.div(*cells, class_=row_cls))

    return core_ui.div(*rows, class_="h2h-matrix",
                       style=f"--h2h-cols:{len(teams_list)}")


def _trade_list(trades_df, logos):
    """
    The season's trade ledger, newest first. Rows sharing a trade_date are
    one trade; each is drawn as the date beside a side-by-side "receives"
    column per team, so the whole swap reads at a glance.
    """
    if trades_df.empty:
        return core_ui.p("No trades yet this season.", class_="empty-note")

    rows = []
    for trade_date, g in sorted(trades_df.groupby("trade_date"),
                                key=lambda kv: kv[0], reverse=True):
        when = datetime.fromtimestamp(trade_date / 1000)
        sides = []
        for team_name, players in g.groupby("to_team_name", sort=False):
            player_rows = [
                core_ui.div(
                    core_ui.span(p["position"] or "", class_="trade-pos"),
                    core_ui.span(p["player_name"] or "Unknown player",
                                 class_="trade-player-name"),
                    class_="trade-player",
                )
                for _, p in players.iterrows()
            ]
            sides.append(core_ui.div(
                _clickable(
                    core_ui.div, "team_pick", team_name,
                    _logo_img(logos.get(team_name)),
                    core_ui.span(team_name, class_="trade-team-name"),
                    core_ui.span("receives", class_="trade-recv"),
                    class_="trade-team", role="button",
                ),
                *player_rows,
                class_="trade-side",
            ))
        rows.append(core_ui.div(
            core_ui.span(f"{when:%b} {when.day}", class_="trade-date"),
            core_ui.div(*sides, class_="trade-sides"),
            class_="trade-row",
        ))
    return core_ui.div(*rows, class_="trade-list")


@render.ui
def screen_league():
    if screen() != "league":
        return None

    scoped = _scope_scores()

    if scoped.empty:
        # Trades still render: post-draft trades exist before a single week
        # has been played, and hiding them behind the score gate would blank
        # the ledger exactly when the league is talking about it.
        return core_ui.div(
            core_ui.p("No weeks in this range.", class_="empty-note"),
            class_="screen screen-top",
        )

    rec = stats.derive_records(scoped)
    espn = _standings_df()
    seeds = dict(zip(espn["team_name"], espn["rank"])) if not espn.empty else {}
    styles = _styles()

    current_sort = sort.get()
    if current_sort == "points":
        rec = rec.sort_values("points_for", ascending=False).reset_index(drop=True)
        note = "Sorted by total points for over the scoped weeks."
    elif current_sort == "form":
        recent_wins = rec["form"].map(lambda f: f.count("W"))
        rec = rec.assign(_form_wins=recent_wins).sort_values(
            "_form_wins", ascending=False).drop(columns="_form_wins").reset_index(drop=True)
        note = "Sorted by wins in the last five games."
    else:
        rec = rec.sort_values(
            ["wins", "points_for"], ascending=[False, False]
        ).reset_index(drop=True)
        note = "Sorted by record, then points for — the same rule the standings use every week."

    log = stats.game_log(scoped)
    logos = _logos_by_name()

    rows = []
    for i, r in rec.iterrows():
        team_name = r["team_name"]
        colors = styles.get(team_name, {"color": theme.INK_MUTE})
        seed = seeds.get(team_name)
        row_scores = log[log["team_name"] == team_name].sort_values("week")

        # Straight off the record's own form string rather than recomputed
        # from the weekly log: a two-week playoff round is one game, and
        # counting weeks here put four pips next to a 2-0 record.
        results = r["form"].split()
        pips = [core_ui.span(class_=f"pip {r2.lower()}") for r2 in results] or [None]

        diff = r["diff"]
        diff_cls = "pvalue pos" if diff >= 0 else "pvalue neg"

        streak = r["streak"]
        streak_chip = None
        if streak:
            streak_chip = core_ui.span(streak, class_=f"streak-chip {streak[0].lower()}")

        rows.append(_clickable(
            core_ui.div, "team_pick", team_name,
            core_ui.span(str(seed) if seed else str(i + 1), class_="seed"),
            core_ui.div(
                core_ui.div(class_="colorbar", style=f"background:{colors['color']}"),
                _logo_img(logos.get(team_name)),
                core_ui.span(team_name, class_="team-name"),
                streak_chip,
                class_="team-cell",
            ),
            core_ui.span(r["record"], class_="record"),
            core_ui.div(*pips, class_="form-pips"),
            core_ui.div(
                core_ui.div(core_ui.span("PF", class_="plabel"),
                            core_ui.span(str(r["points_for"]), class_="pvalue")),
                core_ui.div(core_ui.span("PA", class_="plabel"),
                            core_ui.span(str(r["points_against"]), class_="pvalue")),
                core_ui.div(core_ui.span("DIFF", class_="plabel"),
                            core_ui.span(f"{diff:+d}", class_=diff_cls)),
                class_="points-stack",
            ),
            core_ui.div(
                core_ui.HTML(theme.sparkline(row_scores["score"].tolist(),
                                             color=colors.get("color"))),
                class_="trend",
            ),
            class_=f"board-row seed-{seed}" if seed == 1 else "board-row",
            role="button",
        ))
        # The playoff cutoff line: only meaningful in seed order over the
        # regular season, since that is the stretch that decides who reaches
        # the playoffs. Absent for any other sort or scope rather than drawn
        # somewhere it cannot mean anything.
        if (current_sort == "seed" and scope.get() == "reg"
                and i == len(rec) // 2 - 1):
            rows.append(core_ui.div(
                core_ui.span("PLAYOFF CUTOFF", class_="label"),
                class_="cutoff-row",
            ))

    board = core_ui.div(
        core_ui.div(
            core_ui.span("Seed", class_="col"), core_ui.span("Team", class_="col"),
            core_ui.span("Record", class_="col"), core_ui.span("Last 5", class_="col"),
            core_ui.span("Points", class_="col"), core_ui.span("Trend", class_="col"),
            class_="board-head",
        ),
        *rows,
    )

    return core_ui.div(
        core_ui.h1("STANDINGS", class_="screen-title board"),
        core_ui.p(core_ui.HTML(note), class_="screen-note"),
        board,
        class_="screen screen-top",
    )


@render.ui
def race_visibility_style():
    """
    Toggles each chart's container rather than conditionally rendering the
    widget itself -- shinywidgets fixes a Plotly widget's output slot at the
    point it is defined in the script, so both charts have to live outside
    the conditional screen trees; this hides them everywhere but League, and
    between each other, by CSS instead.
    """
    on_league = screen() == "league"
    race_display = "block" if on_league and chart() == "race" else "none"
    scores_display = "block" if on_league and chart() == "scores" else "none"
    totals_display = "block" if on_league and chart() == "totals" else "none"
    return core_ui.tags.style(
        f"#race-wrap {{ display: {race_display}; }} "
        f"#scores-wrap {{ display: {scores_display}; }} "
        f"#totals-wrap {{ display: {totals_display}; }}"
    )


with reactive.isolate():
    initial_chart = chart.get()

with ui.div(id="chart-toggle-wrap", class_="controlrow"):
    with ui.div(class_="control"):
        core_ui.span("Chart", class_="control-label")
        with ui.div(class_="segment"):
            ui.input_radio_buttons(
                "chart_pick", None,
                {"race": "Race", "scores": "Scores", "totals": "Totals"},
                selected=initial_chart, inline=True,
            )


@render.ui
def chart_toggle_visibility_style():
    display = "flex" if screen() == "league" else "none"
    return core_ui.tags.style(f"#chart-toggle-wrap {{ display: {display}; }}")


with ui.div(id="race-wrap", class_="race-wrap"):
    core_ui.p("The race", class_="section-label")

    with ui.div(class_="chart-wrap"):
        @render_widget
        def race_plot_widget():
            scoped = _scope_scores()
            if scoped.empty:
                return charts.as_widget(charts.empty_fig())
            ranked = stats.rank_by_week(scoped)
            widget = charts.as_widget(
                charts.rank_curve(ranked, _styles(), _logos_by_name()))
            charts.bind_hover_dim(widget)
            return widget


@reactive.effect
def _race_highlight():
    charts.set_highlight(race_plot_widget.widget, team.get())


with ui.div(id="scores-wrap", class_="race-wrap"):
    core_ui.p("Points by week", class_="section-label")

    with ui.div(class_="chart-wrap"):
        @render_widget
        def scores_plot_widget():
            scoped = _scope_scores()
            if scoped.empty:
                return charts.as_widget(charts.empty_fig())
            widget = charts.as_widget(
                charts.score_lines(scoped, _styles(), _logos_by_name()))
            charts.bind_hover_dim(widget)
            return widget


@reactive.effect
def _scores_highlight():
    charts.set_highlight(scores_plot_widget.widget, team.get())


with ui.div(id="totals-wrap", class_="race-wrap"):
    core_ui.p("Running total points — hover a team to compare its points against",
              class_="section-label")

    with ui.div(class_="chart-wrap"):
        @render_widget
        def totals_plot_widget():
            scoped = _scope_scores()
            if scoped.empty:
                return charts.as_widget(charts.empty_fig())
            widget = charts.as_widget(
                charts.total_lines(stats.cumulative_points(scoped),
                                   _styles(), _logos_by_name()))
            charts.bind_hover_dim(widget)
            return widget


@reactive.effect
def _totals_highlight():
    charts.set_highlight(totals_plot_widget.widget, team.get())


def _reg_weeks():
    """Regular-season length: the stored setting, else inferred from the data."""
    season = _season_scores()
    stored = _season_settings().get("reg_season_count")
    if stored:
        return int(stored)
    return stats.regular_season_weeks(_standings_df(), season) or (int(season["week"].max()) if not season.empty else 14)


def _playoff_teams():
    return int(_season_settings().get("playoff_team_count") or 4)


def _luck_table(luck_df):
    if luck_df.empty:
        return core_ui.p("No weeks in this range.", class_="empty-note")
    rows = [core_ui.div(
        core_ui.span("Team", class_="col"), core_ui.span("Record", class_="col"),
        core_ui.span("All-play", class_="col"), core_ui.span("Exp. wins", class_="col"),
        core_ui.span("Luck", class_="col"),
        class_="luck-row luck-head",
    )]
    for _, r in luck_df.iterrows():
        luck = float(r["luck"])
        cls = "pos" if luck > 0.25 else ("neg" if luck < -0.25 else "even")
        rows.append(_clickable(
            core_ui.div, "team_pick", r["team_name"],
            core_ui.span(r["team_name"], class_="luck-team"),
            core_ui.span(f"{int(r['wins'])}-{int(r['losses'])}", class_="luck-num"),
            core_ui.span(f"{int(r['all_play_wins'])}-{int(r['all_play_losses'])}", class_="luck-num"),
            core_ui.span(f"{r['expected_wins']:.1f}", class_="luck-num"),
            core_ui.span(f"{luck:+.1f}", class_=f"luck-num luck-{cls}"),
            class_="luck-row", role="button",
        ))
    return core_ui.div(*rows, class_="luck-table")


def _odds_note(odds_df, reg_weeks, playoff_teams):
    if odds_df.empty:
        return "No games played yet."
    left = int(odds_df["games_left"].max()) if "games_left" in odds_df else 0
    if left == 0:
        return (f"The regular season is over: the top {playoff_teams} by record, then points, "
                "are in. Odds are what happened.")
    return (f"{playoff_teams} of {len(odds_df)} make it after week {reg_weeks}. Odds are from 4,000 "
            f"simulated finishes of the remaining {left} games per team, each team scoring around "
            "its own average with its own spread. Not a model of injuries, byes, or trades.")


@render.ui
def analytics_visibility_style():
    display = "block" if screen() == "league" else "none"
    return core_ui.tags.style(
        f"#luck-wrap, #odds-wrap, #proj-wrap, #league-more {{ display: {display}; }}")


with ui.div(id="luck-wrap", class_="race-wrap analytics-wrap"):
    core_ui.p("Luck · all-play record and expected wins", class_="section-label")
    core_ui.p("All-play counts a week as a win against every team you outscored. Expected wins "
              "is that share summed over the weeks; luck is real wins minus expected. Above the "
              "diagonal in the chart is good and lucky, below it is good and robbed.",
              class_="screen-note")

    with ui.div(class_="analytics-grid"):
        @render.ui
        def luck_table_ui():
            return _luck_table(stats.all_play(_scope_scores()))

        with ui.div(class_="chart-wrap chart-square"):
            @render_widget
            def luck_widget():
                scoped = _scope_scores()
                if scoped.empty:
                    return charts.as_widget(charts.empty_fig())
                return charts.as_widget(charts.luck_quadrant(stats.all_play(scoped), _styles(), _logos_by_name()))


with ui.div(id="odds-wrap", class_="race-wrap analytics-wrap"):
    core_ui.p("Playoff odds", class_="section-label")

    @render.ui
    def odds_note_ui():
        season = _season_scores()
        reg = _reg_weeks()
        odds = stats.playoff_odds(season[season["week"] <= reg], _season_schedule(), reg, _playoff_teams())
        return core_ui.p(_odds_note(odds, reg, _playoff_teams()), class_="screen-note")

    with ui.div(class_="chart-wrap chart-short"):
        @render_widget
        def odds_widget():
            season = _season_scores()
            if season.empty:
                return charts.as_widget(charts.empty_fig())
            reg = _reg_weeks()
            odds = stats.playoff_odds(season[season["week"] <= reg], _season_schedule(), reg, _playoff_teams())
            return charts.as_widget(charts.playoff_odds_bars(odds, _styles()))


with ui.div(id="proj-wrap", class_="race-wrap analytics-wrap"):
    core_ui.p("Against projection · average points over or under ESPN's number", class_="section-label")

    with ui.div(class_="chart-wrap chart-short"):
        @render_widget
        def proj_widget():
            scoped = _scope_scores()
            if scoped.empty:
                return charts.as_widget(charts.empty_fig())
            return charts.as_widget(charts.projection_bars(stats.projection_accuracy(scoped), _styles()))


@render.ui
def screen_league_more():
    """The rest of the League page, below the chart slots: the grid, the
    ledgers, and the power rankings."""
    if screen() != "league":
        return None
    scoped = _scope_scores()
    logos = _logos_by_name()
    h2h_records, h2h_margins = _h2h_frame()
    return core_ui.div(
        core_ui.div(
            core_ui.p("Head to head", class_="section-label"),
            _h2h_matrix(h2h_records, h2h_margins, logos=logos),
            class_="h2h-section",
        ) if not scoped.empty else None,
        core_ui.div(
            core_ui.div(
                core_ui.p("Trades", class_="section-label"),
                _trade_list(_season_trades(), logos),
                class_="trades-section",
            ),
            core_ui.div(
                core_ui.p("Moves", class_="section-label"),
                _moves_list(_season_activity(), logos),
                class_="moves-section",
            ),
            class_="ledgers",
        ),
        _prose_block(_content("power", range(1, 19)), "power rankings", "power-section"),
        class_="screen screen-bottom",
    )


# -------------------------------------------------------------- screen: TEAMS

@render.ui
def screen_teams():
    if screen() != "teams":
        return None

    styles = _styles()
    if not styles:
        return core_ui.div(
            core_ui.p("No score data collected yet for this season.", class_="empty-note"),
            class_="screen",
        )

    current = _current_team()
    scoped = _scope_scores()
    log = stats.game_log(scoped)
    games = log[log["team_name"] == current].sort_values("week") if not log.empty else log
    colors = styles.get(current, {"color": theme.INK_MUTE})
    logos = _logos_by_name()

    records = stats.derive_records(scoped)
    mine = records[records["team_name"] == current]
    espn = _standings_df()
    seed_row = espn[espn["team_name"] == current] if not espn.empty else espn
    seed = int(seed_row.iloc[0]["rank"]) if not seed_row.empty else None
    lo, hi = _scope_bounds()

    record_text = mine.iloc[0]["record"] if not mine.empty else "0-0"
    streak_text = mine.iloc[0]["streak"] if not mine.empty else ""

    con = stats.consistency(scoped)
    mine_con = con[con["team_name"] == current]
    league_avg = scoped["score"].mean() if not scoped.empty else None
    league_median = scoped["score"].median() if not scoped.empty else None

    ceiling_wk = floor_wk = None
    proj_avg = None
    if not games.empty:
        ceiling_wk = int(games.loc[games["score"].idxmax()]["week"])
        floor_wk = int(games.loc[games["score"].idxmin()]["week"])
        proj_games = games[games["projected_score"].notna() & (games["projected_score"] > 0)]
        if not proj_games.empty:
            proj_avg = (proj_games["score"] - proj_games["projected_score"]).mean()

    stat_cells = core_ui.div(
        core_ui.div(
            core_ui.span("Average", class_="k"),
            core_ui.span(f"{games['score'].mean():.1f}" if not games.empty else "—", class_="v"),
            core_ui.span(f"League avg {league_avg:.1f}" if league_avg is not None else "",
                        class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Ceiling", class_="k"),
            core_ui.span(f"{games['score'].max():.1f}" if not games.empty else "—", class_="v"),
            core_ui.span(f"Week {ceiling_wk}" if ceiling_wk else "", class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Floor", class_="k"),
            core_ui.span(f"{games['score'].min():.1f}" if not games.empty else "—", class_="v"),
            core_ui.span(f"Week {floor_wk}" if floor_wk else "", class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Vs projection", class_="k"),
            core_ui.span(f"{proj_avg:+.1f}" if proj_avg is not None else "—", class_="v"),
            core_ui.span("per week", class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Points for", class_="k"),
            core_ui.span(f"{mine.iloc[0]['points_for']:,}" if not mine.empty else "—", class_="v"),
            core_ui.span(_ordinal_rank(records, "points_for", current, high_is_good=True), class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Points against", class_="k"),
            core_ui.span(f"{mine.iloc[0]['points_against']:,}" if not mine.empty else "—", class_="v"),
            core_ui.span(_ordinal_rank(records, "points_against", current, high_is_good=False), class_="sub"),
            class_="cell",
        ),
        class_="profile-stats",
    )

    profile = core_ui.div(
        core_ui.div(
            core_ui.div(
                core_ui.span(f"SEED {seed} · WEEKS {lo}–{hi}" if seed else f"WEEKS {lo}–{hi}",
                            class_="eyebrow"),
                core_ui.div(
                    _logo_img(logos.get(current), "team-logo profile-logo"),
                    core_ui.h1(current, class_="team-name"),
                    class_="profile-title",
                ),
                _lineage_line(current),
            ),
            core_ui.span(f"{record_text} · {streak_text}" if streak_text else record_text,
                        class_="record-streak"),
            class_="profile-head",
            style=(f"background: linear-gradient(180deg, {colors['color']}26 0%, "
                   f"{colors['color']}00 100%)"),
        ),
        stat_cells,
        class_="profile",
        style=f"border-top-color:{colors['color']}",
    )

    log_rows = []
    for _, g in games.iterrows():
        result = g["result"] or "—"
        res_cls = {"W": "win", "L": "loss"}.get(result, "mute")
        delta = None
        if pd.notna(g.get("projected_score")) and g["projected_score"]:
            d = g["score"] - g["projected_score"]
            delta = core_ui.span(f"{d:+.1f}", class_=f"delta {'over' if d >= 0 else 'under'}")
        log_rows.append(core_ui.div(
            core_ui.span(f"WK {int(g['week'])}", class_="wk"),
            core_ui.span(result, class_=f"res {res_cls}"),
            core_ui.span(g["opponent_name"], class_="opp"),
            core_ui.span(f"{g['score']:.1f}", class_="score"),
            core_ui.span(f"{g['opponent_score']:.1f}" if pd.notna(g["opponent_score"]) else "—",
                        class_="oscore"),
            delta,
            class_="gamelog-row",
        ))

    range_block = None
    if not scoped.empty and not mine_con.empty:
        lo_score, hi_score = scoped["score"].min(), scoped["score"].max()
        span = (hi_score - lo_score) or 1.0
        floor_v, ceil_v = mine_con.iloc[0]["floor"], mine_con.iloc[0]["ceiling"]
        band_left = (floor_v - lo_score) / span * 100
        band_width = (ceil_v - floor_v) / span * 100
        my_median = mine_con.iloc[0]["median"]
        my_tick = (my_median - lo_score) / span * 100
        league_tick = (league_median - lo_score) / span * 100 if league_median is not None else 0

        range_block = core_ui.div(
            core_ui.p("Range vs the league", class_="section-label"),
            core_ui.div(
                core_ui.div(
                    core_ui.div(class_="line"),
                    core_ui.div(class_="band", style=(
                        f"left:{band_left:.1f}%;width:{band_width:.1f}%;"
                        f"background:{colors['color']}44")),
                    core_ui.div(class_="tick mine", style=f"left:{my_tick:.1f}%"),
                    core_ui.div(class_="tick league", style=f"left:{league_tick:.1f}%"),
                    class_="range-track",
                ),
                core_ui.div(
                    core_ui.span(f"Floor {floor_v:.1f}"),
                    core_ui.span(f"Ceiling {ceil_v:.1f}"),
                    class_="range-values",
                ),
                core_ui.div(
                    core_ui.span(core_ui.span(class_="swatch", style="background:var(--ink)"),
                                "team median", class_="item"),
                    core_ui.span(core_ui.span(class_="swatch", style="background:var(--ink-mute)"),
                                "league median", class_="item"),
                    class_="range-legend",
                ),
                class_="range-block",
            ),
        )

    h2h_records, h2h_margins = _h2h_frame()
    h2h_scope_note = "all seasons" if h2h_scope.get() == "all" else f"weeks {lo}–{hi}"
    teams_df = _season_teams()
    agent_names = set(teams_df[teams_df["team_id"] == AGENT_TEAM_ID]["team_name"]) if not teams_df.empty else set()
    is_agent_team = AGENT_TEAM_ID and current in agent_names and _year() == CURRENT_YEAR

    return core_ui.div(
        _team_rail(styles, current, logos),
        profile,
        core_ui.div(
            _week_bars(games, scoped),
            class_="team-strip",
        ),
        core_ui.div(
            core_ui.div(
                core_ui.p("Game log", class_="section-label"),
                core_ui.div(*log_rows) if log_rows else
                core_ui.p("No games played yet in this range.", class_="empty-note"),
            ),
            core_ui.div(
                range_block,
                core_ui.p(f"Head to head · {h2h_scope_note}", class_="section-label"),
                _h2h_list(h2h_records, h2h_margins, current, logos),
                _bench_regrets_block(current, scoped),
                _managers_log(_agent_log()) if is_agent_team else None,
            ),
            class_="team-body",
        ),
        class_="screen",
    )


def _scoped_lineups():
    """This season's lineup rows narrowed to the scope segment's weeks."""
    df = _season_lineups()
    if df.empty:
        return df
    lo, hi = _scope_bounds()
    return df[(df["week"] >= lo) & (df["week"] <= hi)]


def _bench_regrets_block(current, scoped):
    """
    Optimal lineup against the one started, for the team on screen: a
    summary line, then the weeks with the most points left on the bench.
    A loss the optimal lineup would have won is called out.
    """
    lineups = _scoped_lineups()
    if lineups.empty or scoped.empty:
        return None
    regrets = stats.bench_regrets(lineups, _slot_counts(), scoped)
    mine = regrets[regrets["team_name"] == current].sort_values("regret", ascending=False)
    if mine.empty:
        return None
    total = float(mine["regret"].sum())
    flipped = int(mine["flipped"].sum())
    weeks = len(mine)
    summary = f"{total:.1f} points left on the bench over {weeks} week{'s' if weeks != 1 else ''}"
    summary += f", {flipped} loss{'es' if flipped != 1 else ''} the best lineup would have won." if flipped else "."
    rows = []
    for _, r in mine.head(6).iterrows():
        if float(r["regret"]) <= 0:
            continue
        rows.append(core_ui.div(
            core_ui.span(f"WK {int(r['week'])}", class_="wk"),
            core_ui.span(f"{r['actual_points']:.1f}", class_="started"),
            core_ui.span("→", class_="arrow"),
            core_ui.span(f"{r['optimal_points']:.1f}", class_="optimal"),
            core_ui.span(f"+{r['regret']:.1f}", class_="regret"),
            core_ui.span("would have won", class_="flip") if bool(r["flipped"]) else None,
            class_="regret-row",
        ))
    return core_ui.div(
        core_ui.p("Bench regrets · started → best possible", class_="section-label"),
        core_ui.p(summary, class_="regret-summary"),
        *rows,
        class_="regrets-block",
    )


@render.ui
def position_visibility_style():
    display = "block" if screen() == "teams" else "none"
    return core_ui.tags.style(f"#position-wrap {{ display: {display}; }}")


with ui.div(id="position-wrap", class_="race-wrap analytics-wrap"):
    core_ui.p("Where the points come from · starters by position, every team", class_="section-label")

    with ui.div(class_="chart-wrap chart-short"):
        @render_widget
        def position_widget():
            lineups = _scoped_lineups()
            if lineups.empty:
                return charts.as_widget(charts.empty_fig("No lineups collected yet for this season."))
            teams_df = _season_teams()
            names = dict(zip(teams_df["team_id"], teams_df["team_name"])) if not teams_df.empty else {}
            if not names:
                scoped = _scope_scores()
                names = dict(zip(scoped["team_id"], scoped["team_name"])) if not scoped.empty else {}
            contrib = stats.position_contribution(lineups, names=names)
            return charts.as_widget(charts.position_stack(contrib, _styles()))


def _ordinal_rank(records, column, team_name, high_is_good):
    """"3rd of 8" for a team's place on one derive_records column; blank when
    the team has no row."""
    if records.empty or team_name not in set(records["team_name"]):
        return ""
    ordered = records.sort_values(column, ascending=not high_is_good).reset_index(drop=True)
    place = int(ordered.index[ordered["team_name"] == team_name][0]) + 1
    suffix = "th" if 11 <= place % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(place % 10, "th")
    return f"{place}{suffix} of {len(ordered)}"


def _week_bars(games, scoped):
    """
    One bar per played week for the team on screen, win or loss colored,
    against a line at the league average for the scoped weeks. Bars are
    scaled to the league's best single-week score so the same height means
    the same score on every team's page.
    """
    if games.empty:
        return None
    top = float(scoped["score"].max()) or 1.0
    league_avg = float(scoped["score"].mean())
    avg_pct = league_avg / top * 100

    bars, labels = [], []
    for _, g in games.iterrows():
        pct = float(g["score"]) / top * 100
        res = g["result"] or ""
        cls = {"W": "win", "L": "loss"}.get(res, "mute")
        opp_score = f"{g['opponent_score']:.1f}" if pd.notna(g["opponent_score"]) else "—"
        bars.append(core_ui.div(
            core_ui.span(f"{g['score']:.1f}", class_="bar-val", style=f"bottom:calc({pct:.1f}% + 4px)"),
            core_ui.div(class_=f"bar {cls}", style=f"height:{pct:.1f}%"),
            class_="weekbar",
            title=f"Week {int(g['week'])}: {res or '—'} {g['score']:.1f} vs {g['opponent_name']} {opp_score}",
        ))
        labels.append(core_ui.span(f"{int(g['week'])}", class_=f"bar-wk {cls}"))

    return core_ui.div(
        # The average is named here rather than floated on the line itself,
        # where it collided with the last bar's value label.
        core_ui.p(f"Week by week · dashed line is the league average, {league_avg:.1f}",
                  class_="section-label"),
        core_ui.div(
            core_ui.div(*bars, class_="weekbars"),
            core_ui.div(class_="weekbars-avg", style=f"bottom:{avg_pct:.1f}%",
                        title=f"league average {league_avg:.1f}"),
            class_="weekbars-wrap",
        ),
        core_ui.div(*labels, class_="weekbars-weeks"),
        class_="weekbars-block", style=f"--n:{len(bars)}",
    )


def _lineage_line(current):
    """Who runs the team and what it was called in other seasons -- the
    all-time head-to-head below counts those seasons too, so say whose they
    are. None when neither is known."""
    index = _managers()
    mine = index[(index["year"] == _year()) & (index["team_name"] == current)]
    if mine.empty:
        return None
    me = mine.iloc[0]
    seasons = index[(index["mid"] == me["mid"]) & (index["year"] != _year())]
    other = [f"{r.team_name} ({r.year})"
             for r in seasons.sort_values("year", ascending=False).itertuples()
             if r.team_name != current]
    parts = []
    if me["manager"]:
        parts.append(f"Managed by {me['manager']}")
    if other:
        parts.append("also " + ", ".join(other))
    return core_ui.p(" · ".join(parts), class_="profile-lineage") if parts else None


def _h2h_list(records_tbl, margins_tbl, current, logos=None):
    """
    The team-page head-to-head: one row per opponent played, sorted by
    average margin, with the record, a bar to either side of zero, and the
    margin itself. Clicking an opponent opens their page. Opponents not yet
    played are named in one muted line so the list is never silently short.

    The League page keeps the full grid; a 400px column is too narrow for
    an 8x8 matrix, and a team page only needs its own row of it anyway.
    """
    logos = logos or {}
    if current not in records_tbl.index:
        return core_ui.p("No matchups in this range.", class_="empty-note")

    played, unplayed = [], []
    for opp in records_tbl.columns:
        if opp == current:
            continue
        rec = records_tbl.at[current, opp]
        if not rec:
            unplayed.append(opp)
            continue
        margin = margins_tbl.at[current, opp]
        played.append((opp, rec, float(margin) if pd.notna(margin) else 0.0))
    played.sort(key=lambda r: (-r[2], r[0]))

    rows = []
    for opp, rec, margin in played:
        width = min(abs(margin) / 40, 1.0) * 50
        rows.append(_clickable(
            core_ui.div, "team_pick", opp,
            core_ui.span(
                _logo_img(logos.get(opp), "team-logo h2h-logo"),
                core_ui.span(opp, class_="opp-name"),
                class_="opp", title=opp,
            ),
            core_ui.span(rec, class_="record"),
            core_ui.div(
                core_ui.div(class_=f"bar {'pos' if margin >= 0 else 'neg'}", style=f"width:{width:.1f}%"),
                class_="bar-track",
            ),
            core_ui.span(f"{margin:+.1f}", class_=f"margin {'pos' if margin >= 0 else 'neg'}"),
            class_="h2h-row", role="button",
        ))

    if not rows:
        return core_ui.p("No games played yet in this range.", class_="empty-note")

    note = None
    if unplayed:
        note = core_ui.p("Not yet played: " + ", ".join(unplayed), class_="h2h-unplayed")
    return core_ui.div(
        core_ui.div(
            core_ui.span("Opponent", class_="col"), core_ui.span("Rec", class_="col"),
            core_ui.span("Avg margin", class_="col"), core_ui.span("", class_="col"),
            class_="h2h-list-head",
        ),
        *rows, note,
        class_="h2h-list",
    )


def _team_rail(styles, current, logos=None):
    logos = logos or {}
    pills = [
        _clickable(
            core_ui.tags.button, "team_pick", name,
            # The logo replaces the color dot when there is one -- the
            # monogram fallback carries the team color itself, so the dot
            # only survives for a team with no logo row at all.
            _logo_img(logos.get(name), "team-logo pill-logo")
            or core_ui.span(class_="dot", style=f"background:{style['color']}"),
            core_ui.span(name),
            class_="team-pill active" if name == current else "team-pill",
            type="button",
        )
        for name, style in styles.items()
    ]
    return core_ui.div(
        core_ui.div(*pills, class_="teamrail"),
        core_ui.div(
            ui.input_select(
                "team_select", None,
                choices=list(styles.keys()),
                selected=current,
            ),
            class_="team-select-mobile",
        ),
    )


# ------------------------------------------------------------ screen: PLAYERS

players_pos = reactive.value("ALL")


@reactive.effect
@reactive.event(input.players_pos)
def _on_players_pos():
    players_pos.set(input.players_pos())


@render.ui
def screen_players():
    if screen() != "players":
        return None

    lineups = _scoped_lineups()
    scoped = _scope_scores()
    lo, hi = _scope_bounds()
    if lineups.empty:
        return core_ui.div(
            core_ui.h1("PLAYERS", class_="screen-title board"),
            core_ui.p("No lineups collected yet for this season. They arrive with the Tuesday snapshot.",
                      class_="empty-note"),
            class_="screen",
        )

    board = stats.player_leaderboard(lineups)
    pos = players_pos.get()
    if pos != "ALL":
        board = board[board["position"] == pos]
    board = board.head(60)

    teams_df = _season_teams()
    names = dict(zip(teams_df["team_id"], teams_df["team_name"])) if not teams_df.empty else {}
    if not names and not scoped.empty:
        names = dict(zip(scoped["team_id"], scoped["team_name"]))
    logos = _logos_by_name()

    pills = [_clickable(core_ui.tags.button, "players_pos", key, label,
                        class_="team-pill active" if pos == key else "team-pill", type="button")
             for key, label in (("ALL", "All"), ("QB", "QB"), ("RB", "RB"), ("WR", "WR"),
                                ("TE", "TE"), ("K", "K"), ("D/ST", "D/ST"))]

    head = core_ui.div(
        core_ui.span("#", class_="col"), core_ui.span("Player", class_="col"),
        core_ui.span("Pos", class_="col"), core_ui.span("Team", class_="col"),
        core_ui.span("Starts", class_="col"), core_ui.span("Points", class_="col"),
        core_ui.span("Avg", class_="col"), core_ui.span("Best", class_="col"),
        core_ui.span("Boom", class_="col"), core_ui.span("Bust", class_="col"),
        class_="pl-row pl-head",
    )
    rows = []
    for i, r in enumerate(board.itertuples(), start=1):
        team_name = names.get(int(r.team_id), f"Team {r.team_id}")
        rows.append(_clickable(
            core_ui.div, "team_pick", team_name,
            core_ui.span(str(i), class_="pl-rank"),
            core_ui.span(r.player_name, class_="pl-name"),
            core_ui.span(r.position, class_="pl-pos"),
            core_ui.span(_logo_img(logos.get(team_name), "team-logo pl-logo"),
                         core_ui.span(team_name, class_="pl-team-name"), class_="pl-team"),
            core_ui.span(f"{int(r.starts)}/{int(r.weeks_rostered)}", class_="pl-num"),
            core_ui.span(f"{r.points_as_starter:.1f}", class_="pl-num pl-points"),
            core_ui.span(f"{r.avg_as_starter:.1f}", class_="pl-num"),
            core_ui.span(f"{r.best_points:.1f}", core_ui.span(f" wk {int(r.best_week)}", class_="pl-sub"), class_="pl-num"),
            core_ui.span(f"{r.boom_rate * 100:.0f}%", class_="pl-num pl-boom"),
            core_ui.span(f"{r.bust_rate * 100:.0f}%", class_="pl-num pl-bust"),
            class_="pl-row", role="button",
        ))

    return core_ui.div(
        core_ui.div(
            core_ui.h1("PLAYERS", class_="screen-title board"),
            core_ui.span(f"Weeks {lo}–{hi} · {_freshness()}", class_="stamp"),
            class_="title-row",
        ),
        core_ui.p("Points as a starter, from every played week's lineups. Starts are out of weeks on a "
                  "roster; a boom is a start at 1.5× the player's own average, a bust is one at half. "
                  "Points scored on the bench count for nobody, which is the whole point of the Teams "
                  "page's bench regrets.", class_="screen-note"),
        core_ui.div(*pills, class_="teamrail draft-clubs"),
        core_ui.div(head, *rows, class_="pl-table") if rows else
        core_ui.p("Nobody at that position has started a game in this range.", class_="empty-note"),
        class_="screen",
    )


# ------------------------------------------------------------ screen: RECORDS

@render.ui
def screen_records():
    if screen() != "records":
        return None

    scoped = _scope_scores()
    if scoped.empty:
        return core_ui.div(
            core_ui.p("No weeks in this range.", class_="empty-note"),
            class_="screen",
        )

    awards = stats.trophies(scoped)
    single_week = [a for a in awards if a["week"] is not None]
    season = [a for a in awards if a["week"] is None]
    # Named in the headings, because the Weeks segment narrows this half of
    # the page and not the all-time half below it -- a control that visibly
    # moves only some of what is on screen has to say which part.
    lo, hi = _scope_bounds()

    def ledger_row(a, week_badge=None):
        value_text, unit_text = stats.split_detail(a["detail"])
        scoreline = a["detail"].split(" — ")[1] if " — " in a["detail"] else ""
        if week_badge is None:
            week_badge = f"WK {a['week']}" if a["week"] is not None else "SEASON"
        return _clickable(
            core_ui.div, "team_pick", a["focus"],
            core_ui.span(a["icon"], class_="glyph"),
            core_ui.div(
                core_ui.span(a["title"], class_="award"),
                core_ui.span(a["team"], class_="team"),
                core_ui.span(scoreline, class_="scoreline") if scoreline else None,
                class_="stack",
            ),
            core_ui.span(
                value_text,
                core_ui.span(unit_text, class_="unit") if unit_text else None,
                class_="value",
            ),
            core_ui.div(
                core_ui.span(week_badge, class_="weekbadge"),
                core_ui.span("→", class_="arrow"),
                class_="meta",
            ),
            class_="ledger-row",
            role="button",
        )

    def all_time_row(a):
        if a["week"] is not None:
            badge = f"WK {a['week']} · {a['year']}"
        elif a["year"] is not None:
            badge = str(a["year"])
        else:
            badge = "ALL-TIME"
        return ledger_row(a, week_badge=badge)

    all_time_awards = stats.all_time_trophies(_all_scores())
    champion_rows = _champion_rows()

    return core_ui.div(
        core_ui.h1("RECORD BOOK", class_="screen-title board"),
        core_ui.p(
            "Every line goes somewhere — click one and you land on that "
            "team's page with the week already loaded.",
            class_="screen-note",
        ),
        core_ui.h2(f"This season · weeks {lo}–{hi}"
                    if lo <= hi else "This season",
                    class_="records-section-title"),
        core_ui.div(
            core_ui.p("Single week", class_="ledger-heading"),
            *[ledger_row(a) for a in single_week],
            class_="ledger-group",
        ) if single_week else None,
        core_ui.div(
            core_ui.p("Season", class_="ledger-heading"),
            *[ledger_row(a) for a in season],
            class_="ledger-group",
        ) if season else None,
        core_ui.h2("All time", class_="records-section-title"),
        core_ui.div(
            core_ui.p("Every season in the database, whole — the Weeks "
                       "segment narrows this season's half of the page, not "
                       "this one. Keyed on (year, week) so week numbers never "
                       "collide across seasons. Champions come from the "
                       "bracket once a season's final is played.",
                       class_="ledger-heading records-alltime-note"),
            *[all_time_row(a) for a in champion_rows],
            *[all_time_row(a) for a in all_time_awards],
            class_="ledger-group",
        ) if all_time_awards or champion_rows else None,
        _bracket_block(),
        class_="screen",
    )


def _champion_rows():
    """One record-book row per decided season: the champion, with the
    final's score as the detail so the row reads like the others."""
    all_scores = _all_scores()
    settings = _all_settings()
    rows = []
    for year, name in sorted(stats.champions(all_scores, settings).items(), reverse=True):
        cfg = settings.get(int(year), {})
        season = all_scores[all_scores["year"] == int(year)]
        detail = "won the final"
        for rnd in stats.bracket(season, int(cfg.get("playoff_team_count") or 4), int(cfg.get("reg_season_count") or 14)):
            for g in rnd.get("games", []):
                if g.get("kind") == "final" and g.get("winner") == name:
                    loser = g["away"] if g["home"] == name else g["home"]
                    w = g["home_score"] if g["home"] == name else g["away_score"]
                    l = g["away_score"] if g["home"] == name else g["home_score"]
                    detail = f"{w:.1f} pts — beat {loser} {l:.1f} in the final"
        rows.append({"icon": "🏆", "title": "Champion", "team": name, "focus": name,
                     "detail": detail, "week": None, "year": int(year)})
    return rows


def _bracket_block():
    """
    The selected season's playoff bracket, round by round, once a playoff
    game has been played. The final and third-place game are named; the
    consolation side is shown smaller.
    """
    season = _season_scores()
    if season.empty:
        return None
    reg = _reg_weeks()
    rounds = stats.bracket(season, _playoff_teams(), reg)
    rounds = [r for r in rounds if r.get("games")]
    if not rounds:
        return None
    logos = _logos_by_name()
    labels = {"final": "Final", "third": "Third place", "playoff": "Playoff", "consolation": "Consolation"}

    def game(g):
        winner = g.get("winner")

        def side(name, score, seed):
            cls = "bk-side win" if name == winner else "bk-side"
            return _clickable(
                core_ui.div, "team_pick", name,
                _logo_img(logos.get(name), "team-logo bk-logo"),
                core_ui.span(f"{seed} " if seed else "", class_="bk-seed"),
                core_ui.span(name, class_="bk-name"),
                core_ui.span(f"{score:.1f}" if score is not None else "—", class_="bk-score"),
                class_=cls, role="button",
            )
        return core_ui.div(
            core_ui.span(labels.get(g.get("kind"), ""), class_="bk-kind"),
            side(g["home"], g.get("home_score"), g.get("home_seed")),
            side(g["away"], g.get("away_score"), g.get("away_seed")),
            class_=f"bk-game {g.get('kind') or ''}",
        )

    cols = []
    for r in rounds:
        weeks = r.get("weeks") or []
        title = f"Round {r['round']} · week{'s' if len(weeks) > 1 else ''} {weeks[0]}" + (f"–{weeks[-1]}" if len(weeks) > 1 else "")
        cols.append(core_ui.div(core_ui.p(title, class_="section-label"), *[game(g) for g in r["games"]], class_="bk-round"))
    return core_ui.div(
        core_ui.h2("Playoff bracket", class_="records-section-title"),
        core_ui.div(*cols, class_="bracket"),
        class_="bracket-section",
    )


# --------------------------------------------------------------- screen: CHAT

chat_ok = reactive.value(False)
_chat_client = {"client": None}


def _viewer():
    try:
        return str(input.viewer_token() or "anon")
    except Exception:
        return "anon"


@reactive.effect
def _chat_saved_pass():
    """A passphrase remembered by the browser opens the gate on load."""
    try:
        saved = input.chat_pass_saved()
    except Exception:
        return
    if saved and datachat.check_passphrase(saved):
        chat_ok.set(True)


@reactive.effect
@reactive.event(input.chat_go)
def _chat_gate_submit():
    if datachat.check_passphrase(input.chat_pass() or ""):
        chat_ok.set(True)
    else:
        ui.notification_show("That is not the league passphrase.", type="warning", duration=4)


@render.ui
def chat_visibility_style():
    display = "block" if screen() == "chat" else "none"
    # The input only shows once the gate is open; a box you cannot use is
    # a promise the page cannot keep.
    box = "block" if (screen() == "chat" and chat_ok.get() and datachat.enabled()[0]) else "none"
    return core_ui.tags.style(f"#chat-wrap {{ display: {display}; }} #datachat-box {{ display: {box}; }}")


with ui.div(id="chat-wrap", class_="screen chat-screen"):
    core_ui.h1("ASK THE DATA", class_="screen-title board")

    @render.ui
    def chat_gate():
        enabled, reason = datachat.enabled()
        if not enabled:
            return core_ui.p(reason, class_="empty-note")
        if chat_ok.get():
            return core_ui.p(
                "Answers come from this site's own tables: scores, standings, head to head, "
                "records, lineups, the draft, trades, and moves. For start, sit, and trade advice "
                "use /ask in Discord, which reads live ESPN data.",
                class_="screen-note",
            )
        return core_ui.div(
            core_ui.p("The chat is for league members. Enter the passphrase from the league "
                      "Discord once and this browser remembers it.", class_="screen-note"),
            core_ui.div(
                ui.input_password("chat_pass", None, placeholder="League passphrase"),
                ui.input_action_button("chat_go", "Open the chat", class_="btn-accent"),
                class_="chat-gate",
            ),
        )

    datachat_ui = ui.Chat(id="datachat")
    with ui.div(id="datachat-box"):
        datachat_ui.ui(
            placeholder="Who has the best record against Space Cadets? Top scorers at RB?",
            height="560px",
            greeting="Ask me anything the dashboard's data can answer. I know every season in the "
                     "database, not what happened on ESPN this morning.",
        )

    @datachat_ui.on_user_submit
    async def _chat_turn(user_input: str):
        if not chat_ok.get():
            await datachat_ui.append_message("Enter the league passphrase above first.")
            return
        viewer = _viewer()
        ok, reason = datachat.allow(viewer)
        if not ok:
            await datachat_ui.append_message(reason)
            return
        if _chat_client["client"] is None:
            _chat_client["client"] = datachat.make_client()
        await datachat_ui.append_message_stream(datachat.answer(_chat_client["client"], viewer, user_input))


# ---------------------------------------------------------------- bottom bar

@render.ui
def bottom_nav():
    current = screen()
    cells = [
        _clickable(
            core_ui.tags.button, "nav_pick", key,
            core_ui.div(class_="bb-bar"),
            core_ui.span(label.upper(), class_="bb-label"),
            class_="bb-item active" if key == current else "bb-item",
            type="button",
        )
        for key, label in NAV_ITEMS
    ]
    return core_ui.div(*cells, class_="bottombar")


ui.markdown(
    "<p class='footnote'>New snapshots are collected every Tuesday during the "
    "season and appear here on their own — no refresh needed. The draft board "
    "refreshes daily, including before kickoff.</p>"
    "<p class='footnote footnote-links'>"
    "<a href='https://ethandbard.com' target='_blank' rel='noopener'>ethandbard.com</a>"
    " · "
    "<a href='https://fantasy-docs.ethandbard.com/' target='_blank' rel='noopener'>Docs</a>"
    "</p>"
)
