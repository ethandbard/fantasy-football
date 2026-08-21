# Shiny dashboard for the fantasy league: score trends, standings, score
# spread, season recap. Reads the SQLite snapshots written by
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

# Upper bound for the week slider before the first sync effect runs. Any
# value at least as large as a real season works; the effect narrows it to
# the weeks actually collected as soon as the session starts.
WEEK_CEILING = 18

WWW = Path(__file__).parent / "www"

ui.page_opts(window_title="Fantasy Football Dashboard", fillable=False)

ui.head_content(
    # Bootstrap 5.3 reads this attribute and switches its own components to
    # dark; dashboard.css then redefines the --bs-* variables it uses. Doing
    # it this way keeps a Sass compiler out of the container, which is what
    # customising ui.Theme would have cost.
    core_ui.tags.script("document.documentElement.dataset.bsTheme = 'dark'"),
    # One delegated listener rather than an input per trophy: the cards are
    # rebuilt whenever the week range moves, and registering ten action
    # buttons against a list whose length depends on the data would mean
    # server-side ids that come and go.
    core_ui.tags.script(
        "document.addEventListener('click', function (e) {"
        "  var card = e.target.closest && e.target.closest('.trophy[data-idx]');"
        "  if (card && window.Shiny) {"
        "    Shiny.setInputValue('trophy_pick', card.dataset.idx,"
        "                        {priority: 'event'});"
        "  }"
        "});"
    ),
    core_ui.tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
    core_ui.tags.link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    core_ui.tags.link(
        rel="stylesheet",
        href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800"
             "&family=JetBrains+Mono:wght@400;600&display=swap",
    ),
)
ui.include_css(WWW / "dashboard.css")


# The team the reader has singled out, or None for the league view. Every tab
# reads this: the trend lines mute around it, its box lifts out of the spread,
# its standings row carries an accent edge. One selection, felt everywhere.
focus_team = reactive.value(None)

# The week a trophy card sent the reader to, marked on the trend chart so the
# jump lands somewhere they can see.
highlight_week = reactive.value(None)


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
    """Every collected week of the selected season, before the week filter."""
    df = _all_scores()
    return df[df["year"] == _year()] if not df.empty else df


def _standings_df():
    df = _all_standings()
    return df[df["year"] == _year()] if not df.empty else df


def _week_range():
    rng = input.week_range()
    return (int(rng[0]), int(rng[1])) if rng else (1, WEEK_CEILING)


def _scores():
    """
    The season narrowed to the selected weeks -- what every chart and tile
    actually reads.

    The week filter is the honest answer to a season that changes its own
    rules partway through: playoff weeks aggregate differently from regular
    ones, so rather than quietly deciding for the reader, the range is a
    control they can see and move.
    """
    df = _season_scores()
    if df.empty:
        return df
    lo, hi = _week_range()
    return df[(df["week"] >= lo) & (df["week"] <= hi)]


def _styles():
    """
    Team colors for the season, stable across every tab.

    Built from the whole season rather than the filtered slice so narrowing
    the week range never repaints a team a different color.
    """
    df = _season_scores()
    if df.empty:
        return {}
    return theme.team_styles(zip(df["team_id"], df["team_name"]))


# ---------------------------------------------------------------- masthead

with ui.div(class_="masthead"):
    ui.h1("🏈 Fantasy Football")

    with ui.div(class_="season-pick"):
        ui.input_select(
            "year", None,
            choices=[str(y) for y in (db.get_years() or [CURRENT_YEAR])],
        )

    @render.ui
    def through_week():
        df = _season_scores()
        if df.empty:
            return core_ui.span("no data yet", class_="season")
        return core_ui.span(f"through week {int(df['week'].max())}", class_="season")

    @render.ui
    def freshness():
        """
        Backs up the promise that this page keeps itself current.

        Relative rather than a clock time: collected_at is stored in UTC and
        the server has no idea what timezone the reader is in, so "3h ago" is
        both shorter and the only version that cannot be wrong.
        """
        _all_scores()  # re-read whenever the collector writes
        stamp = db.last_collected()
        if not stamp:
            return core_ui.span("live", class_="live")

        try:
            written = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return core_ui.span("live", class_="live")

        minutes = max(int((datetime.utcnow() - written).total_seconds() // 60), 0)
        if minutes < 2:
            ago = "just now"
        elif minutes < 60:
            ago = f"{minutes}m ago"
        elif minutes < 48 * 60:
            ago = f"{minutes // 60}h ago"
        else:
            ago = f"{minutes // 1440}d ago"
        return core_ui.span(f"updated {ago}", class_="live")


@reactive.effect
def _sync_season_choices():
    """
    Keeps the season dropdown in step with the database. Without this the
    choices are whatever existed when the process started: Shiny Express
    tagifies the UI once at startup and serves that same markup to every
    request, so a new season would stay invisible until a container restart.

    input.year() is read under isolate() so this effect depends only on the
    data, not on the selection it sets.
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


# ------------------------------------------------------------- control bar

# The slider is the single source of truth for which weeks are in play. The
# three presets write into it and never hold state of their own, so there is
# no way for a "Regular season" button to sit lit while the range says weeks
# 3-9 -- a trap any two-way binding between a mode switch and a range would
# have walked straight into.
with ui.div(class_="controlbar"):
    with ui.div(class_="presets"):
        ui.input_action_link("preset_reg", "Regular season")
        ui.input_action_link("preset_post", "Playoffs")
        ui.input_action_link("preset_all", "Full season")

    ui.input_slider("week_range", None, min=1, max=WEEK_CEILING,
                    value=(1, WEEK_CEILING), step=1, ticks=False)

    @render.ui
    def range_label():
        lo, hi = _week_range()
        reg = stats.regular_season_weeks(_standings_df(), _season_scores())
        total = int(_season_scores()["week"].max()) if not _season_scores().empty else 0

        if (lo, hi) == (1, reg) and reg:
            text = "regular season"
        elif reg and (lo, hi) == (reg + 1, total):
            text = "playoffs"
        elif (lo, hi) == (1, total) and total:
            text = "full season"
        elif lo == hi:
            text = f"week {lo}"
        else:
            text = f"weeks {lo}–{hi}"
        return core_ui.span(text, class_="range-label")

    @render.ui
    def focus_chip():
        team = focus_team()
        if not team:
            return core_ui.span("all teams", class_="chip chip-empty")
        return core_ui.span(team, class_="chip")

    ui.input_action_button("reset_view", "Reset", class_="btn-reset")


@reactive.effect
def _sync_week_bounds():
    """
    Pulls the slider's bounds down to the weeks actually collected, and on the
    first pass parks the range on the regular season.

    Same reasoning as _sync_season_choices: Express builds the UI once at
    startup, so a slider that ran to week 18 would keep running to week 18
    for the rest of the container's life.
    """
    df = _season_scores()
    if df.empty:
        return

    total = int(df["week"].max())
    reg = stats.regular_season_weeks(_standings_df(), df) or total

    with reactive.isolate():
        current = input.week_range()

    # Only reposition the handles when the current range makes no sense for
    # this season -- otherwise a poll landing mid-read would yank the range
    # out from under whatever the reader had dialled in.
    if current and int(current[1]) <= total and int(current[0]) >= 1 and current[1] != WEEK_CEILING:
        selected = (int(current[0]), int(current[1]))
    else:
        selected = (1, reg)

    core_ui.update_slider("week_range", min=1, max=total, value=selected)


@reactive.effect
@reactive.event(input.preset_reg)
def _preset_regular():
    df = _season_scores()
    if df.empty:
        return
    reg = stats.regular_season_weeks(_standings_df(), df) or int(df["week"].max())
    core_ui.update_slider("week_range", value=(1, reg))


@reactive.effect
@reactive.event(input.preset_post)
def _preset_playoffs():
    df = _season_scores()
    if df.empty:
        return
    total = int(df["week"].max())
    reg = stats.regular_season_weeks(_standings_df(), df)
    # A season with no playoff weeks collected yet stays on the full range
    # rather than collapsing to an empty selection.
    core_ui.update_slider("week_range", value=(reg + 1, total) if reg < total else (1, total))


@reactive.effect
@reactive.event(input.preset_all)
def _preset_all():
    df = _season_scores()
    if df.empty:
        return
    core_ui.update_slider("week_range", value=(1, int(df["week"].max())))


@reactive.effect
@reactive.event(input.reset_view)
def _reset_view():
    """One way back to the opening view, from wherever the reader wandered."""
    focus_team.set(None)
    highlight_week.set(None)
    df = _season_scores()
    if not df.empty:
        reg = stats.regular_season_weeks(_standings_df(), df) or int(df["week"].max())
        core_ui.update_slider("week_range", value=(1, reg))


@reactive.effect
@reactive.event(input.trophy_pick)
def _open_trophy():
    """
    Follows a trophy card through to the chart.

    "Biggest Blowout, week 13" was the most interesting fact on the old
    dashboard and a dead end -- there was no way to go look at week 13. This
    focuses the team, marks the week, and moves the reader to the trend.
    """
    awards = stats.trophies(_scores())
    try:
        award = awards[int(input.trophy_pick())]
    except (TypeError, ValueError, IndexError):
        return

    focus_team.set(award["focus"])
    highlight_week.set(award["week"])
    core_ui.update_navs("tabs", selected="Trend")


@reactive.effect
def _focus_from_standings():
    """Selecting a standings row is the primary way into a single-team view."""
    selected = standings_table.cell_selection()
    rows = selected.get("rows", ()) if selected else ()
    if not rows:
        return

    # data_view() returns the rows as the reader currently sees them, so this
    # stays correct after they sort by any column. Indexing the source frame
    # instead would focus whichever team happened to sit at that position
    # before the sort.
    view = standings_table.data_view()
    if view.empty or rows[0] >= len(view):
        return
    focus_team.set(str(view.iloc[rows[0]]["Team"]))


# ------------------------------------------------------------- stat tiles

def _tile(label, value, sub=None, spark=None, value_class=""):
    return core_ui.div(
        core_ui.span(label, class_="k"),
        core_ui.span(value, class_=f"v {value_class}".strip()),
        core_ui.HTML(spark) if spark else (core_ui.span(sub, class_="sub") if sub else None),
        class_="tile",
    )


@render.ui
def stat_tiles():
    """
    The four headline numbers, over the selected weeks.

    One render for all four rather than one apiece: they share a single pass
    over the season, and rendering them together means the grid can hold them
    to a common height instead of the four different heights the old value
    boxes settled at.
    """
    scores = _scores()
    standings = _standings_df()

    if scores.empty:
        tiles = [_tile(k, "--") for k in
                 ("League Leader", "Highest Score", "Avg Weekly Score", "Weeks Shown")]
        return core_ui.div(*tiles, class_="tiles")

    leader = standings.iloc[0] if not standings.empty else None
    high = scores.loc[scores["score"].idxmax()]
    weekly_avg = scores.groupby("week")["score"].mean().sort_index()
    lo, hi = _week_range()
    collected = int(_season_scores()["week"].nunique())
    shown = int(scores["week"].nunique())

    return core_ui.div(
        _tile(
            "League Leader",
            leader["team_name"] if leader is not None else "--",
            sub=(f"{int(leader['wins'])}-{int(leader['losses'])}"
                 if leader is not None else None),
            value_class="name",
        ),
        _tile(
            "Highest Score", f"{high['score']:.1f}",
            sub=f"{high['team_name']} · wk {int(high['week'])}",
        ),
        _tile(
            "Avg Weekly Score", f"{scores['score'].mean():.1f}",
            spark=theme.sparkline(weekly_avg.tolist()),
        ),
        # Says which weeks the three numbers to its left were built from, so
        # the basis for the average is never a guess.
        _tile(
            "Weeks Shown", f"{lo}–{hi}" if lo != hi else str(lo),
            sub=f"{shown} of {collected} collected",
        ),
        class_="tiles",
    )


@render.ui
def team_drilldown():
    """
    The focused team's season, week by week.

    An inline panel rather than a modal: it survives a tab change, needs no
    dismiss gesture, and does not trap focus on a phone. It appears only when
    a team is picked, so the league view keeps its full width.
    """
    team = focus_team()
    if not team:
        return None

    log = stats.game_log(_scores())
    games = log[log["team_name"] == team].sort_values("week")
    if games.empty:
        return None

    rows = []
    for _, g in games.iterrows():
        result = g["result"] or "—"
        margin = "" if pd.isna(g["margin"]) else f"{g['margin']:+.1f}"
        projected = "—" if pd.isna(g["projected_score"]) else f"{g['projected_score']:.1f}"
        rows.append(core_ui.tags.tr(
            core_ui.tags.td(f"{int(g['week'])}"),
            core_ui.tags.td(g["opponent_name"], class_="opp"),
            core_ui.tags.td(f"{g['score']:.1f}"),
            core_ui.tags.td(projected, class_="dim"),
            core_ui.tags.td(result, class_=f"res res-{result.lower()}"),
            core_ui.tags.td(margin, class_="dim"),
        ))

    record = stats.derive_records(_scores())
    mine = record[record["team_name"] == team]
    summary = ""
    if not mine.empty:
        r = mine.iloc[0]
        summary = (f"{r['record']} · {r['points_for']} for · "
                   f"{r['points_against']} against · {r['diff']:+d} diff")

    best = games.loc[games["score"].idxmax()]
    worst = games.loc[games["score"].idxmin()]
    colors = _styles().get(team, {})

    return core_ui.div(
        core_ui.div(
            core_ui.span(team, class_="dt"),
            core_ui.span(summary, class_="ds"),
            core_ui.HTML(theme.sparkline(games["score"].tolist(), width=150, height=32,
                                         color=colors.get("color"))),
            class_="drill-head",
        ),
        core_ui.div(
            core_ui.span(f"Best {best['score']:.1f} in wk {int(best['week'])}", class_="pill"),
            core_ui.span(f"Worst {worst['score']:.1f} in wk {int(worst['week'])}", class_="pill"),
            class_="drill-pills",
        ),
        core_ui.div(
            core_ui.tags.table(
                core_ui.tags.thead(core_ui.tags.tr(
                    core_ui.tags.th("Wk"), core_ui.tags.th("Opponent", class_="opp"),
                    core_ui.tags.th("Score"), core_ui.tags.th("Proj"),
                    core_ui.tags.th("Res"), core_ui.tags.th("Margin"),
                )),
                core_ui.tags.tbody(*rows),
            ),
            class_="drill-log",
        ),
        class_="drill",
    )


# ------------------------------------------------------------------- tabs

with ui.navset_card_tab(id="tabs"):
    with ui.nav_panel("Trend"):
        @render.ui
        def trend_note():
            """
            States the takeaway instead of restating the tab label.

            The old build opened every tab with a heading that repeated the
            tab you had just clicked, spending the most prominent line on the
            page saying nothing.
            """
            scores = _scores()
            if scores.empty:
                return core_ui.HTML("<p class='takeaway'>No weeks in this range.</p>")

            rec = stats.derive_records(scores)
            if rec.empty:
                return core_ui.HTML("<p class='takeaway'>No completed matchups yet.</p>")

            by_points = rec.sort_values("points_for", ascending=False)
            top_scorer = by_points.iloc[0]
            leader = rec.iloc[0]

            if top_scorer["team_name"] == leader["team_name"]:
                line = (f"<b>{leader['team_name']}</b> leads at {leader['record']} "
                        f"and has scored the most points — {leader['points_for']}.")
            else:
                place = int(rec.index[rec["team_name"] == top_scorer["team_name"]][0]) + 1
                line = (f"<b>{leader['team_name']}</b> leads at {leader['record']}, but "
                        f"<b>{top_scorer['team_name']}</b> has scored the most points "
                        f"({top_scorer['points_for']}) while sitting {place}th.")
            return core_ui.HTML(f"<p class='takeaway'>{line}</p>")

        with ui.div(class_="viewswitch"):
            ui.input_radio_buttons(
                "trend_view", None,
                {
                    "score": "Weekly score",
                    "cumulative": "Cumulative",
                    "rank": "Rank by week",
                    "projection": "vs Projection",
                },
                selected="score", inline=True,
            )

        with ui.div(class_="chart-wrap"):
            @render_widget
            def score_trend_plot():
                df = _scores()
                if df.empty:
                    return charts.as_widget(charts.empty_fig())

                view = input.trend_view()
                styles, focus = _styles(), focus_team()

                if view == "cumulative":
                    fig = charts.cumulative_points(df, styles, focus)
                elif view == "rank":
                    fig = charts.rank_curve(stats.rank_by_week(df), styles, focus)
                elif view == "projection":
                    proj = stats.vs_projection(df)
                    if proj.empty:
                        return charts.as_widget(charts.empty_fig(
                            "No projections were recorded for these weeks."))
                    fig = charts.projection_bars(proj, styles, focus)
                else:
                    fig = charts.score_trend(df, styles, focus, highlight_week())

                return charts.as_widget(fig)

    with ui.nav_panel("Standings"):
        @render.ui
        def standings_note():
            return core_ui.HTML(
                "<p class='takeaway'>Ordered by wins, then points for, over the "
                "weeks selected above. <b>Seed</b> is ESPN's official standing "
                "for the full regular season — it applies division and "
                "head-to-head tiebreaks that aren't published, so it can "
                "disagree with the order here.</p>"
            )

        @render.data_frame
        def standings_table():
            scores = _scores()
            cols = ["#", "Team", "Record", "Win%", "PF", "PA", "Diff",
                    "Streak", "Last 5", "Seed"]
            if scores.empty:
                return render.DataGrid(pd.DataFrame(columns=cols))

            rec = stats.derive_records(scores)
            espn = _standings_df()
            seeds = dict(zip(espn["team_name"], espn["rank"])) if not espn.empty else {}

            out = pd.DataFrame({
                "#": range(1, len(rec) + 1),
                "Team": rec["team_name"],
                "Record": rec["record"],
                # Formatted to a fixed three decimals so the column is a
                # block of digits rather than a ragged mix of 0.5 and 0.643.
                # Safe to sort as text: every value is 0.000-1.000, so the
                # lexicographic order and the numeric order are the same.
                "Win%": rec["win_pct"].map("{:.3f}".format),
                "PF": rec["points_for"],
                "PA": rec["points_against"],
                "Diff": rec["diff"],
                "Streak": rec["streak"],
                "Last 5": rec["form"],
                "Seed": rec["team_name"].map(seeds),
            })[cols]

            # Tint the qualifying rows rather than drawing a divider under the
            # fourth one. A line only means "cutoff" while the table is in
            # rank order -- sort by points for and it strands itself mid-table,
            # reading as a stray rule. A tint belongs to the team, so it
            # travels with the row wherever the reader sorts it.
            cutoff = len(out) // 2
            styles = [
                {"rows": [i], "style": {"backgroundColor": "rgba(240, 99, 30, 0.07)"}}
                for i in range(cutoff)
            ]
            styles.append({"rows": [0], "style": {"color": theme.INK, "fontWeight": "600"}})

            # Left to itself the grid hands every column a similar width, which
            # squeezes "Seemed like the thing to do" into three wrapped lines
            # while a two-character Streak column sits half empty. The numeric
            # columns only ever hold a few characters, so cap them and give the
            # remainder to the names.
            # Numbers right-align so their digits stack into a scannable
            # column; the rank and the team name are text and read from the
            # left. The stylesheet's default assumes the name is the first
            # column, which is true of every grid here except this one.
            styles += [
                {"cols": [0], "style": {"width": "3.5rem", "textAlign": "left",
                                        "color": theme.INK_MUTE, "fontWeight": "400"}},
                {"cols": [1], "style": {"minWidth": "13rem", "whiteSpace": "nowrap",
                                        "textAlign": "left", "color": theme.INK,
                                        "fontWeight": "600"}},
                {"cols": [2, 3, 4, 5, 6, 7, 9], "style": {"width": "5rem"}},
                {"cols": [8], "style": {"width": "7rem", "whiteSpace": "nowrap"}},
            ]

            return render.DataGrid(
                out, width="100%", height="fit-content",
                selection_mode="row", styles=styles,
            )

    with ui.nav_panel("Spread"):
        @render.ui
        def spread_note():
            return core_ui.HTML(
                "<p class='takeaway'>Each box is one team's range of weekly "
                "scores; the dots are the weeks themselves. The table ranks "
                "them from steadiest to streakiest — <b>CV</b> is the spread "
                "as a percentage of the team's own average, so a high scorer "
                "and a low scorer can be compared directly.</p>"
            )

        with ui.div(class_="split"):
            with ui.div(class_="chart-wrap"):
                @render_widget
                def consistency_plot():
                    df = _scores()
                    if df.empty:
                        return charts.as_widget(charts.empty_fig())
                    order = (df.groupby("team_name")["score"].median()
                             .sort_values(ascending=False).index)
                    return charts.as_widget(
                        charts.spread_box(df, _styles(), list(order), focus_team())
                    )

            @render.data_frame
            def consistency_table():
                df = _scores()
                cols = ["Team", "Median", "Floor", "Ceiling", "Std", "CV"]
                if df.empty:
                    return render.DataGrid(pd.DataFrame(columns=cols))

                con = stats.consistency(df)
                out = pd.DataFrame({
                    "Team": con["team_name"],
                    "Median": con["median"],
                    "Floor": con["floor"],
                    "Ceiling": con["ceiling"],
                    "Std": con["std"],
                    "CV": con["cv"].map("{:.1f}%".format),
                })[cols]
                return render.DataGrid(out, width="100%", height="fit-content",
                                       selection_mode="row")

    with ui.nav_panel("Head to head"):
        @render.ui
        def h2h_note():
            return core_ui.HTML(
                "<p class='takeaway'>Read a row against a column: the cell "
                "holds the row team's record against that opponent, tinted by "
                "the average margin. Green means the row team usually wins "
                "that matchup.</p>"
            )

        with ui.div(class_="chart-wrap chart-tall"):
            @render_widget
            def h2h_plot():
                df = _scores()
                if df.empty:
                    return charts.as_widget(charts.empty_fig())
                records, margins = stats.head_to_head(df)
                if records.empty:
                    return charts.as_widget(charts.empty_fig(
                        "No completed matchups in these weeks."))
                return charts.as_widget(charts.h2h_grid(records, margins))

    with ui.nav_panel("Trophies"):
        @render.ui
        def trophy_cards():
            """
            Highlights as a card grid rather than a sortable table.

            A table invited sorting, which could only scramble a curated
            order, and it had a column headed "Icon" holding one emoji per
            row -- a header naming the mechanism rather than the content.
            Cards drop both problems and give each award room for its value
            in words: "0.8 pt margin", not a bare 0.8 under "Detail".
            """
            awards = stats.trophies(_scores())
            if not awards:
                return core_ui.HTML(
                    "<p class='takeaway'>No completed weeks in this range yet.</p>"
                )

            cards = []
            for i, a in enumerate(awards):
                week = (core_ui.span(f"WK {a['week']}", class_="tw")
                        if a["week"] is not None else
                        core_ui.span("SEASON", class_="tw"))
                cards.append(core_ui.tags.button(
                    core_ui.span(a["icon"], class_="ti"),
                    core_ui.div(
                        core_ui.span(a["title"], class_="tt"),
                        core_ui.span(a["team"], class_="tm"),
                        core_ui.span(a["detail"], class_="td"),
                        class_="tbody",
                    ),
                    week,
                    class_="trophy", data_idx=str(i), type="button",
                ))

            return core_ui.div(
                core_ui.HTML(
                    "<p class='takeaway'>Pick any award to follow that team "
                    "on the trend chart, with its week marked.</p>"
                ),
                core_ui.div(*cards, class_="trophies"),
            )


ui.markdown(
    "<p class='footnote'>New snapshots are collected every Tuesday during the "
    "season and appear here on their own — no refresh needed. Pick a team in "
    "the standings to follow it across every tab. On the charts, click a team "
    "in the legend to hide it, or double-click to show only that team.</p>"
)
