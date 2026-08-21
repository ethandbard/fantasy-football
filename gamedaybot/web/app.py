# Shiny dashboard for the fantasy league: this week's results, standings,
# per-team pages, and the record book. Reads the SQLite snapshots written by
# gamedaybot.espn.collector.
#
# Layout and reactive wiring only -- the palette lives in web/theme.py, the
# figures in web/charts.py, the season arithmetic in web/stats.py, and the
# visual system in web/www/dashboard.css.
#
# Deliberately comments rather than a module docstring: Shiny Express renders
# top-level string expressions as page content, so a docstring here shows up
# on the live dashboard.
from datetime import datetime
from pathlib import Path

import pandas as pd
import shiny.ui as core_ui  # express ui.value_box is a context manager; @render.ui needs the plain function
from shiny import reactive
from shiny.express import input, render, ui
from shinywidgets import render_widget

import gamedaybot.storage.db as db
import gamedaybot.web.charts as charts
import gamedaybot.web.stats as stats
import gamedaybot.web.theme as theme

db.init_db()

CURRENT_YEAR = datetime.now().year

# How often to check whether the collector has written new snapshots. The
# check is a single cheap aggregate query, not a full reload.
DB_POLL_SECONDS = 30

WWW = Path(__file__).parent / "www"

NAV_ITEMS = [
    ("week", "This week"),
    ("league", "League"),
    ("teams", "Teams"),
    ("records", "Records"),
]

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
    core_ui.tags.script(
        "document.addEventListener('click', function (e) {"
        "  var el = e.target.closest && e.target.closest('[data-set]');"
        "  if (el && window.Shiny) {"
        "    Shiny.setInputValue(el.dataset.set, el.dataset.value,"
        "                        {priority: 'event'});"
        "  }"
        "});"
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


# Everything below reads through these two polls, so a snapshot written by the
# collector reaches an already-open browser tab within DB_POLL_SECONDS -- no
# restart, no rebuild, no page refresh.
@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_scores():
    return pd.DataFrame(db.get_all_weekly_scores())


@reactive.poll(db.fingerprint, DB_POLL_SECONDS)
def _all_standings():
    return pd.DataFrame(db.get_all_latest_standings())


def _year():
    return int(input.year()) if input.year() else CURRENT_YEAR


def _season_scores():
    """Every collected week of the selected season, unfiltered by scope."""
    df = _all_scores()
    return df[df["year"] == _year()] if not df.empty else df


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
    stamp = db.last_collected()
    if not stamp:
        return "live"
    try:
        written = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "live"
    minutes = max(int((datetime.utcnow() - written).total_seconds() // 60), 0)
    if minutes < 2:
        return "synced just now"
    if minutes < 60:
        return f"synced {minutes}m ago"
    if minutes < 48 * 60:
        return f"synced {minutes // 60}h ago"
    return f"synced {minutes // 1440}d ago"


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

    @render.ui
    def synced_label():
        return core_ui.span(_freshness(), class_="synced")


@reactive.effect
def _sync_season_choices():
    """
    Keeps the season dropdown in step with the database. Without this the
    choices are whatever existed when the process started: Shiny Express
    tagifies the UI once at startup and serves that same markup to every
    request, so a new season would stay invisible until a container restart.
    """
    df = _all_scores()
    years = sorted(df["year"].unique().tolist(), reverse=True) if not df.empty else [CURRENT_YEAR]
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
    if input.scope() is not None:
        scope.set(input.scope())


@reactive.effect
def _on_scope_records_input():
    # Records gets its own radio-button ID rather than reusing "scope":
    # League and Records are two separate @render.ui trees, and their scope
    # segments can both be present in the client's input-binding table across
    # a screen switch (Shiny's client-side duplicate-ID check fires on the
    # overlap), so this one writes the same reactive.value under a name of
    # its own.
    if input.scope_records() is not None:
        scope.set(input.scope_records())


@reactive.effect
def _on_sort_input():
    if input.sort() is not None:
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
        winner = closest if closest["margin"] >= 0 else None
        if winner is None:
            loser_name, winner_name = closest["team_name"], closest["opponent_name"]
        else:
            winner_name, loser_name = closest["team_name"], closest["opponent_name"]
        parts.append(
            f"The {winner_name} survived {loser_name} by {abs(closest['margin']):.1f}"
        )
    standings = _standings_df()
    if not standings.empty:
        leader = standings.sort_values("rank").iloc[0]
        record = f"{int(leader['wins'])}-{int(leader['losses'])}"
        parts.append(f"{leader['team_name']} holds the 1 seed at {record}")
    if not parts:
        return "No completed matchups yet this week."
    return ", and ".join(parts) + "."


def _results_rows(week_scores, styles):
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
                core_ui.span(win_name, class_="name"),
                delta_span(win_name, win_delta),
                class_="side left win",
            ),
            core_ui.span(f"{win_score:.1f}", class_="score"),
            core_ui.span("–", class_="dash"),
            core_ui.span(f"{lose_score:.1f}", class_="score"),
            core_ui.div(
                core_ui.span(lose_name, class_="name"),
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


def _movers(week_scores):
    ranked = stats.rank_by_week(_season_scores())
    if ranked.empty:
        return None
    wk = _current_week()
    if wk is None:
        return None
    this = ranked[ranked["week"] == wk].set_index("team_name")["rank"]
    prev = ranked[ranked["week"] == wk - 1].set_index("team_name")["rank"]
    if this.empty:
        return None
    moved = (prev - this).reindex(this.index).fillna(0)
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
            ),
            core_ui.span(value, class_="value"),
            class_="bestrow", role="button",
        ))
    if not rows:
        return None
    return core_ui.div(core_ui.p("Week bests", class_="section-label"),
                       core_ui.div(*rows), class_="rail-block")


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
    week_scores = season[season["week"] == wk] if wk else season.iloc[0:0]
    latest = _latest_week()
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
                _results_rows(week_scores, _styles()),
                class_="results",
            ),
            core_ui.div(
                core_ui.p("Against their own average", class_="section-label"),
                _own_average_rows(week_scores),
            ),
            core_ui.div(
                _movers(week_scores),
                _week_bests(week_scores),
                class_="rail",
            ),
            class_="week-body",
        ),
        class_="screen",
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

    buttons = [
        _clickable(
            core_ui.tags.button, "week_pick", n,
            str(n),
            class_="week-btn active" if n == current else "week-btn",
            type="button",
        )
        for n in range(1, total + 1)
    ]
    return core_ui.div(
        core_ui.span("WEEK", class_="eyebrow"),
        *buttons,
        class_="weekrail",
    )


# ------------------------------------------------------------- screen: LEAGUE

@render.ui
def screen_league():
    if screen() != "league":
        return None

    scoped = _scope_scores()
    lo, hi = _scope_bounds()

    if scoped.empty:
        return core_ui.div(
            _scope_sort_row(),
            core_ui.p("No weeks in this range.", class_="empty-note"),
            class_="screen",
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
        note = "Sorted by record, then points for -- the same rule the standings use every week."

    log = stats.game_log(scoped)

    rows = []
    for i, r in rec.iterrows():
        team_name = r["team_name"]
        colors = styles.get(team_name, {"color": theme.INK_MUTE})
        seed = seeds.get(team_name)
        row_scores = log[log["team_name"] == team_name].sort_values("week")

        results = row_scores["result"].tolist()[-5:]
        pips = [core_ui.span(class_=f"pip {r2.lower() if r2 else 't'}") for r2 in results] or [None]

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
        # The playoff cutoff line: only meaningful in seed order, so it is
        # simply absent for any other sort.
        if current_sort == "seed" and i == len(rec) // 2 - 1:
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

    range_note = f"weeks {lo}–{hi}" if lo != hi else f"week {lo}"

    return core_ui.div(
        _scope_sort_row(range_note),
        core_ui.h1("STANDINGS", class_="screen-title board"),
        core_ui.p(core_ui.HTML(note), class_="screen-note"),
        board,
        class_="screen",
    )


@render.ui
def race_visibility_style():
    """
    Toggles the race chart's container rather than conditionally rendering
    the widget itself -- shinywidgets fixes a Plotly widget's output slot at
    the point it is defined in the script, so it has to live outside the
    conditional screen trees; this hides it everywhere but League instead.
    """
    display = "block" if screen() == "league" else "none"
    return core_ui.tags.style(f"#race-wrap {{ display: {display}; }}")


def _scope_sort_row(range_note=None):
    return core_ui.div(
        core_ui.div(
            core_ui.div(
                ui.input_radio_buttons(
                    "scope", None,
                    {"reg": "Regular", "post": "Playoffs", "full": "Full"},
                    selected=scope.get(), inline=True,
                ),
                class_="segment",
            ),
            core_ui.span(range_note or "", class_="range-note") if range_note else None,
            class_="left",
        ),
        core_ui.div(
            ui.input_radio_buttons(
                "sort", None,
                {"seed": "Seed", "points": "Points", "form": "Form"},
                selected=sort.get(), inline=True,
            ),
            class_="segment",
        ),
        class_="controlrow",
    )


with ui.div(id="race-wrap", class_="race-wrap"):
    core_ui.p("The race", class_="section-label")

    with ui.div(class_="chart-wrap"):
        @render_widget
        def race_plot_widget():
            scoped = _scope_scores()
            if scoped.empty:
                return charts.as_widget(charts.empty_fig())
            ranked = stats.rank_by_week(scoped)
            return charts.as_widget(charts.rank_curve(ranked, _styles()))


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
            core_ui.span(f"{games['score'].mean():.1f}" if not games.empty else "--", class_="v"),
            core_ui.span(f"League avg {league_avg:.1f}" if league_avg is not None else "",
                        class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Ceiling", class_="k"),
            core_ui.span(f"{games['score'].max():.1f}" if not games.empty else "--", class_="v"),
            core_ui.span(f"Week {ceiling_wk}" if ceiling_wk else "", class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Floor", class_="k"),
            core_ui.span(f"{games['score'].min():.1f}" if not games.empty else "--", class_="v"),
            core_ui.span(f"Week {floor_wk}" if floor_wk else "", class_="sub"),
            class_="cell",
        ),
        core_ui.div(
            core_ui.span("Vs projection", class_="k"),
            core_ui.span(f"{proj_avg:+.1f}" if proj_avg is not None else "--", class_="v"),
            core_ui.span("per week", class_="sub"),
            class_="cell",
        ),
        class_="profile-stats",
    )

    profile = core_ui.div(
        core_ui.div(
            core_ui.div(
                core_ui.span(f"SEED {seed} · WEEKS {lo}–{hi}" if seed else f"WEEKS {lo}–{hi}",
                            class_="eyebrow"),
                core_ui.h1(current, class_="team-name"),
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

    h2h_rows = []
    records_tbl, margins_tbl = stats.head_to_head(scoped)
    if current in records_tbl.index:
        row_margins = margins_tbl.loc[current].dropna().sort_values(ascending=False)
        for opp in row_margins.index:
            m = row_margins[opp]
            rec_text = records_tbl.at[current, opp]
            pct = min(abs(m) / 40, 1.0) * 100
            h2h_rows.append(core_ui.div(
                core_ui.span(opp, class_="opp"),
                core_ui.span(rec_text, class_="record"),
                core_ui.div(
                    core_ui.div(class_=f"bar {'pos' if m >= 0 else 'neg'}",
                                style=f"width:{pct / 2:.1f}%"),
                    class_="bar-track",
                ),
                class_="h2h-row",
            ))

    return core_ui.div(
        _team_rail(styles, current),
        profile,
        core_ui.div(
            core_ui.div(
                core_ui.p("Game log", class_="section-label"),
                core_ui.div(*log_rows) if log_rows else
                core_ui.p("No games played yet in this range.", class_="empty-note"),
            ),
            core_ui.div(
                range_block,
                core_ui.p("Head to head", class_="section-label"),
                core_ui.div(*h2h_rows) if h2h_rows else None,
            ),
            class_="team-body",
        ),
        class_="screen",
    )


def _team_rail(styles, current):
    pills = [
        _clickable(
            core_ui.tags.button, "team_pick", name,
            core_ui.span(class_="dot", style=f"background:{style['color']}"),
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


# ------------------------------------------------------------ screen: RECORDS

@render.ui
def screen_records():
    if screen() != "records":
        return None

    scoped = _scope_scores()
    lo, hi = _scope_bounds()
    if scoped.empty:
        return core_ui.div(
            _scope_sort_row_records(),
            core_ui.p("No weeks in this range.", class_="empty-note"),
            class_="screen",
        )

    awards = stats.trophies(scoped)
    single_week = [a for a in awards if a["week"] is not None]
    season = [a for a in awards if a["week"] is None]

    def ledger_row(a):
        value_text = a["detail"].split(" ")[0]
        scoreline = a["detail"].split(" — ")[1] if " — " in a["detail"] else ""
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
            core_ui.span(value_text, class_="value"),
            core_ui.div(
                core_ui.span(week_badge, class_="weekbadge"),
                core_ui.span("→", class_="arrow"),
                class_="meta",
            ),
            class_="ledger-row",
            role="button",
        )

    range_note = f"weeks {lo}–{hi}" if lo != hi else f"week {lo}"

    return core_ui.div(
        _scope_sort_row_records(range_note),
        core_ui.h1("RECORD BOOK", class_="screen-title board"),
        core_ui.p(
            "Every line goes somewhere — click one and you land on that "
            "team's page with the week already loaded.",
            class_="screen-note",
        ),
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
        class_="screen",
    )


def _scope_sort_row_records(range_note=None):
    return core_ui.div(
        core_ui.div(
            core_ui.div(
                ui.input_radio_buttons(
                    "scope_records", None,
                    {"reg": "Regular", "post": "Playoffs", "full": "Full"},
                    selected=scope.get(), inline=True,
                ),
                class_="segment",
            ),
            core_ui.span(range_note or "", class_="range-note") if range_note else None,
            class_="left",
        ),
        class_="controlrow",
    )


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
    "season and appear here on their own — no refresh needed.</p>"
)
