"""
Shiny dashboard for the fantasy league: score trends, standings/power
rankings, season recap. Reads directly from the SQLite snapshots written by
gamedaybot.espn.collector -- no separate API layer needed.
"""
import re
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import shiny.ui as core_ui  # express ui.value_box is a context manager; @render.ui needs the plain function
from shiny.express import input, render, ui
from shinywidgets import render_widget

import gamedaybot.storage.db as db

db.init_db()

CURRENT_YEAR = datetime.now().year

# One color per team, stable across tabs, so a team's line on Weekly Scores
# is the same color as its box on Consistency.
TEAM_PALETTE = px.colors.qualitative.Dark24

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+"
)


def _clean_label(name):
    return EMOJI_RE.sub("", name).strip()


def _team_colors(team_names):
    names = sorted(set(team_names))
    return {name: TEAM_PALETTE[i % len(TEAM_PALETTE)] for i, name in enumerate(names)}


def _empty_fig(message="No score data collected yet for this season."):
    return go.Figure().update_layout(
        annotations=[dict(text=message, showarrow=False, font=dict(size=15))],
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        margin=dict(t=20, b=20),
    )


ui.page_opts(title="🏈 Fantasy Football Dashboard", fillable=True)

with ui.sidebar(width=220):
    ui.input_select(
        "year", "Season",
        choices=[str(y) for y in (db.get_years() or [CURRENT_YEAR])],
    )
    ui.markdown(
        "Data updates automatically every Tuesday during the season. "
        "Charts below are interactive -- hover for detail, click a legend "
        "entry to isolate a team."
    )


def _weekly_scores_df(year):
    rows = db.get_weekly_scores(year)
    return pd.DataFrame(rows)


def _standings_df(year):
    rows = db.get_latest_standings(year)
    return pd.DataFrame(rows)


with ui.layout_columns(fill=False):
    @render.ui
    def stat_leader():
        df = _standings_df(int(input.year()))
        label = df.iloc[0]["team_name"] if not df.empty else "--"
        return core_ui.value_box("League Leader", label, showcase=core_ui.tags.span("🏆"),
                                  theme="text-blue")

    @render.ui
    def stat_high_score():
        df = _weekly_scores_df(int(input.year()))
        if df.empty:
            return core_ui.value_box("Highest Score", "--", showcase=core_ui.tags.span("🔥"),
                                      theme="text-orange")
        row = df.loc[df["score"].idxmax()]
        return core_ui.value_box(
            "Highest Score",
            f"{row['score']:.1f}",
            f"{row['team_name']} (wk {row['week']})",
            showcase=core_ui.tags.span("🔥"),
            theme="text-orange",
        )

    @render.ui
    def stat_avg_score():
        df = _weekly_scores_df(int(input.year()))
        avg = f"{df['score'].mean():.1f}" if not df.empty else "--"
        return core_ui.value_box("Avg Weekly Score", avg, showcase=core_ui.tags.span("📊"),
                                  theme="text-teal")

    @render.ui
    def stat_weeks_tracked():
        df = _weekly_scores_df(int(input.year()))
        weeks = int(df["week"].nunique()) if not df.empty else 0
        return core_ui.value_box("Weeks Tracked", str(weeks), showcase=core_ui.tags.span("📅"),
                                  theme="text-purple")


with ui.navset_card_tab():
    with ui.nav_panel("Weekly Scores"):
        ui.markdown("#### Weekly Scores by Team")

        @render_widget
        def score_trend_plot():
            df = _weekly_scores_df(int(input.year()))
            if df.empty:
                return _empty_fig()
            colors = _team_colors(df["team_name"])
            fig = px.line(
                df.sort_values("week"), x="week", y="score", color="team_name",
                markers=True, color_discrete_map=colors,
                labels={"week": "Week", "score": "Score", "team_name": "Team"},
            )
            fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
            fig.update_layout(
                legend_title_text="Team", hovermode="x unified",
                margin=dict(t=10, b=10),
                height=480,
            )
            return fig

    with ui.nav_panel("Standings"):
        ui.markdown("#### Standings")
        ui.markdown(
            "<small><span style='color:#b8860b'>&#9608;</span> League leader &nbsp;&nbsp;"
            "<span style='color:#666'>&#9608;</span> Playoff cutoff</small>"
        )

        @render.data_frame
        def standings_table():
            df = _standings_df(int(input.year()))
            cols = ["Rank", "Team", "Wins", "Losses", "Ties", "Points For", "Points Against"]
            if df.empty:
                return render.DataGrid(pd.DataFrame(columns=cols))
            out = df.assign(
                points_for=df["points_for"].round(1),
                points_against=df["points_against"].round(1),
            ).rename(columns={
                "rank": "Rank", "team_name": "Team", "wins": "Wins",
                "losses": "Losses", "ties": "Ties",
                "points_for": "Points For", "points_against": "Points Against",
            })[cols]

            playoff_cutoff = len(out) // 2

            def _row_style(row_index):
                if row_index == 0:
                    return {"backgroundColor": "#fdf3d7", "fontWeight": "600"}
                if row_index == playoff_cutoff - 1:
                    return {"borderBottom": "2px solid #666"}
                return None

            return render.DataGrid(
                out, width="100%", height="fit-content",
                styles=[{"rows": [i], "style": _row_style(i)}
                        for i in range(len(out)) if _row_style(i)],
            )

    with ui.nav_panel("Consistency"):
        ui.markdown("#### Score Distribution by Team")

        @render_widget
        def consistency_plot():
            df = _weekly_scores_df(int(input.year()))
            if df.empty:
                return _empty_fig()
            colors = _team_colors(df["team_name"])
            order = (df.groupby("team_name")["score"].median()
                     .sort_values(ascending=False).index)
            df = df.assign(team_label=df["team_name"].map(_clean_label))
            fig = px.box(
                df, x="team_name", y="score", color="team_name",
                color_discrete_map=colors,
                category_orders={"team_name": list(order)},
                labels={"team_name": "Team", "score": "Score"},
                points="all", hover_name="team_name",
            )
            label_map = {name: _clean_label(name) for name in order}
            fig.update_xaxes(
                tickmode="array", tickvals=list(order),
                ticktext=[label_map[n] for n in order],
                tickangle=0,
            )
            fig.update_layout(showlegend=False, margin=dict(t=10, b=10), height=480)
            return fig

    with ui.nav_panel("Trophies"):
        ui.markdown("#### Season Recap: Trophies")

        @render.data_frame
        def recap_table():
            year = int(input.year())
            rows = db.get_weekly_scores(year)
            cols = ["Icon", "Trophy", "Team", "Week", "Detail"]
            if not rows:
                return render.DataGrid(pd.DataFrame(columns=cols))

            best = max(rows, key=lambda r: r["score"])
            worst = min(rows, key=lambda r: r["score"])
            margins = [
                (r, abs(r["score"] - _opponent_score(rows, r)))
                for r in rows if r["is_home"]
            ]
            closest = min(margins, key=lambda x: x[1]) if margins else None
            blowout = max(margins, key=lambda x: x[1]) if margins else None

            out = [
                {"Icon": "🔥", "Trophy": "Highest Score", "Team": best["team_name"],
                 "Week": best["week"], "Detail": round(best["score"], 1)},
                {"Icon": "🥶", "Trophy": "Lowest Score", "Team": worst["team_name"],
                 "Week": worst["week"], "Detail": round(worst["score"], 1)},
            ]
            if closest:
                r, margin = closest
                out.append({"Icon": "😅", "Trophy": "Closest Matchup", "Team": f"{r['team_name']} vs {r['opponent_name']}",
                             "Week": r["week"], "Detail": round(margin, 1)})
            if blowout:
                r, margin = blowout
                out.append({"Icon": "💥", "Trophy": "Biggest Blowout", "Team": f"{r['team_name']} vs {r['opponent_name']}",
                             "Week": r["week"], "Detail": round(margin, 1)})
            return render.DataGrid(pd.DataFrame(out)[cols], width="100%", height="fit-content")


def _opponent_score(rows, home_row):
    for r in rows:
        if (r["week"] == home_row["week"] and
                r["team_id"] == home_row["opponent_id"]):
            return r["score"]
    return home_row["score"]
