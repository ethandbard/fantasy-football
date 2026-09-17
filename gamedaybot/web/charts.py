"""
Figure builders for the dashboard.

Every figure goes out through style_fig() so the tabs share one look, and
every figure is built for a dark ground: backgrounds are transparent rather
than dark-colored, so the card underneath shows through and there is no
seam where the plot ends.
"""
import plotly.graph_objects as go

import gamedaybot.web.theme as theme

# Plotly renders to canvas and never sees the page's CSS, so the type stack is
# repeated here. Kept in step with --font-body in www/dashboard.css.
_FONT = "Archivo, 'Helvetica Neue', Arial, sans-serif"
_MONO = "'JetBrains Mono', ui-monospace, Menlo, monospace"

# Lasso and box select do nothing useful on a line chart or a box plot, and
# the zoom pair duplicates scroll and drag. What is left is the only button
# anyone in the league will use plus a way back out of an accidental zoom.
PLOT_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": [
        "lasso2d", "select2d", "zoomIn2d", "zoomOut2d", "autoScale2d",
        "toggleSpikelines", "hoverClosestCartesian", "hoverCompareCartesian",
    ],
}

_AXIS = dict(
    gridcolor=theme.LINE,
    zerolinecolor=theme.LINE,
    linecolor=theme.LINE,
    tickfont=dict(color=theme.INK_MUTE, size=11, family=_MONO),
    title_font=dict(color=theme.INK_DIM, size=11, family=_MONO),
)


def style_fig(fig, **overrides):
    """
    Applies the shared dark treatment. Call this last, so it wins.

    Overrides are merged into the base layout rather than passed alongside
    it, so a caller can replace a key the base already sets -- height, say --
    instead of handing update_layout the same keyword twice.
    """
    layout = dict(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_FONT, color=theme.INK_DIM, size=12),
        margin=dict(t=8, b=8, l=8, r=8),
        autosize=True,
        height=460,
        hoverlabel=dict(
            bgcolor=theme.RAISED,
            bordercolor=theme.LINE,
            font=dict(family=_MONO, size=12, color=theme.INK),
            align="left",
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            title_text="", font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
    )
    layout.update(overrides)
    fig.update_layout(**layout)
    fig.update_xaxes(**_AXIS)
    fig.update_yaxes(**_AXIS)
    return fig


def as_widget(fig):
    """
    Hands Plotly its client-side config.

    shinywidgets renders an ipywidget, and the widget's config is a private
    trait rather than part of the documented surface -- so this sets it when
    present and leans on the .modebar rules in www/dashboard.css otherwise.
    The stylesheet has to restyle the modebar for a dark ground regardless,
    so the fallback costs nothing.
    """
    widget = go.FigureWidget(fig)
    if hasattr(widget, "_config"):
        widget._config = {**(widget._config or {}), **PLOT_CONFIG}
    return widget


def empty_fig(message="No score data collected yet for this season."):
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False,
        font=dict(size=14, color=theme.INK_MUTE, family=_FONT),
    )
    style_fig(fig, showlegend=False)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def spread_labels(points, min_gap):
    """
    Nudge end-of-line labels apart so a cluster stays readable.

    `points` is [(key, y), ...]; returns {key: y} with every pair at least
    `min_gap` apart, keeping the original top-to-bottom order. Labels are
    pushed down from the highest, then the whole block is shifted back up by
    however far it overran, so a crowded chart drifts rather than sliding off
    the bottom.

    Only the label moves -- the line still ends at the real value, which is
    the trade a slope chart always makes: four teams finishing within a point
    of each other cannot each have a legible name at their exact height.
    """
    if not points:
        return {}

    ordered = sorted(points, key=lambda kv: kv[1], reverse=True)
    placed = []
    for key, y in ordered:
        if placed and y > placed[-1][1] - min_gap:
            y = placed[-1][1] - min_gap
        placed.append((key, y))

    # Push the block back up by the distance the lowest label was driven
    # past where it started, so the group stays centred on its own data.
    overrun = ordered[-1][1] - placed[-1][1]
    if overrun > 0:
        shift = overrun / 2
        placed = [(key, y + shift) for key, y in placed]

    return dict(placed)


def _endpoint_logo(fig, uri, x, y, x_range, sizey):
    """
    A team's logo just right of its line's endpoint, level with the line.

    Sized as a fraction of the x-axis span so the box scales with the plot
    the same way the data does, keeping it visually constant whether the
    scope is four playoff weeks or a full season. sizing="contain" keeps the
    source image's aspect inside the box whatever shape it arrives in.
    """
    span = x_range[1] - x_range[0]
    fig.add_layout_image(
        source=uri, xref="x", yref="y",
        x=x + span * 0.012, y=y, xanchor="left", yanchor="middle",
        sizex=span * 0.034, sizey=sizey, sizing="contain", layer="above",
    )


# xshift for the right-edge name annotations: past the logo when one is
# drawn, snug against the line when not.
_LABEL_SHIFT_PLAIN = 14
_LABEL_SHIFT_LOGO = 48


def rank_curve(ranked, styles, logos=None):
    """
    "The race": standing after every week, first place at the top.

    Modelled on an F1 standings chart: diagonal transitions so a crossing is
    visible, a name at both the starting order (left) and the current order
    (right) so a line never has to be traced back to find out who it is, and
    markers only where a team's rank actually moved -- a marker every week
    just adds noise once the line itself carries the shape. `logos` (name ->
    image data URI) adds each team's logo at its line's right endpoint.

    The y-axis is reversed because a rank of 1 is the good end, and a chart
    where the leader sits at the bottom reads backwards no matter how it is
    labelled.
    """
    logos = logos or {}
    fig = go.Figure()
    teams = int(ranked["rank"].max()) if not ranked.empty else 1
    weeks = sorted(ranked["week"].unique()) if not ranked.empty else []
    x_pad = 0.6
    x_range = [weeks[0] - x_pad, weeks[-1] + x_pad] if weeks else [0.5, 1.5]

    for name in sorted(ranked["team_name"].unique()):
        rows = ranked[ranked["team_name"] == name].sort_values("week")
        style = styles.get(name, {"color": theme.INK_DIM, "dash": "solid"})
        changed = rows["rank"].diff().fillna(1) != 0  # first point always marked
        marker_size = [7 if c else 0 for c in changed]

        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["rank"], name=name, mode="lines+markers",
            line=dict(color=style["color"], width=3.2,
                      dash=style["dash"], shape="linear"),
            marker=dict(size=marker_size, color=style["color"]),
            showlegend=False,
            hovertemplate=f"<b>{name}</b><br>Week %{{x}} · rank %{{y}}<extra></extra>",
        ))

        first, last = rows.iloc[0], rows.iloc[-1]
        # xshift rather than padding spaces: the left-hand names have to clear
        # the rank tick labels, and a pixel offset says that in a way a
        # variable-width space never can.
        fig.add_annotation(
            x=first["week"], y=first["rank"], text=name, xshift=-52,
            showarrow=False, xanchor="right", align="right",
            font=dict(family=_FONT, size=11, color=theme.INK_MUTE),
        )
        logo = logos.get(name)
        if logo:
            _endpoint_logo(fig, logo, last["week"], last["rank"], x_range,
                           sizey=0.62)
        fig.add_annotation(
            x=last["week"], y=last["rank"], text=name,
            xshift=_LABEL_SHIFT_LOGO if logo else _LABEL_SHIFT_PLAIN,
            showarrow=False, xanchor="left", align="left",
            font=dict(family=_FONT, size=11.5, color=style["color"]),
        )

    # Left and right margins match, because both carry a full team name --
    # a 64px left margin clipped every one of them against the paper edge.
    style_fig(fig, hovermode="closest", showlegend=False, height=460,
             margin=dict(t=8, b=8, l=190, r=190))
    fig.update_xaxes(title_text="WEEK", dtick=1, range=x_range)
    fig.update_yaxes(title_text="RANK", autorange="reversed",
                     dtick=1, range=[teams + 0.6, 0.4])
    return fig


def score_lines(scores_df, styles, logos=None):
    """
    Points per week per team, with the league average as a dashed reference.

    The race chart answers "who's ahead"; this answers "by how much" -- the
    two are complementary rather than one replacing the other, hence the
    toggle rather than a redesign of rank_curve. `logos` works as it does on
    rank_curve, drawn at each label's spread position so logo and name stay
    together even when the label was nudged off its line's true endpoint.
    """
    if scores_df.empty:
        return empty_fig()

    logos = logos or {}
    fig = go.Figure()
    weeks = sorted(scores_df["week"].unique())
    x_pad = 0.6
    x_range = [weeks[0] - x_pad, weeks[-1] + x_pad]
    league_avg = scores_df.groupby("week")["score"].mean().reindex(weeks)
    names = sorted(scores_df["team_name"].unique())

    # Unlike the race chart's evenly spaced ranks, final scores cluster: four
    # teams can finish a week within a point of each other, and their names
    # then land on top of one another. Work out where each label goes before
    # drawing any of them.
    finals = {}
    for name in names:
        rows = scores_df[scores_df["team_name"] == name].sort_values("week")
        if not rows.empty:
            finals[name] = rows.iloc[-1]["score"]
    span = (scores_df["score"].max() - scores_df["score"].min()) or 1.0
    # An 11.5px label wants ~17px of vertical room. The plot area comes out
    # around 360px tall once the height, margins and x-axis are accounted
    # for, so that many points of the score span is the gap to ask for.
    label_y = spread_labels(list(finals.items()), min_gap=span * 17 / 360)

    for name in names:
        rows = scores_df[scores_df["team_name"] == name].sort_values("week")
        style = styles.get(name, {"color": theme.INK_DIM, "dash": "solid"})
        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["score"], name=name, mode="lines+markers",
            line=dict(color=style["color"], width=3.2, dash=style["dash"]),
            marker=dict(size=5, color=style["color"]),
            showlegend=False,
            hovertemplate=f"<b>{name}</b><br>Week %{{x}} · %{{y:.1f}} pts<extra></extra>",
        ))
        last = rows.iloc[-1]
        label_pos = label_y.get(name, last["score"])
        logo = logos.get(name)
        if logo:
            _endpoint_logo(fig, logo, last["week"], label_pos, x_range,
                           sizey=span * 30 / 360)
        fig.add_annotation(
            x=last["week"], y=label_pos, text=name,
            xshift=_LABEL_SHIFT_LOGO if logo else _LABEL_SHIFT_PLAIN,
            showarrow=False, xanchor="left", align="left",
            font=dict(family=_FONT, size=11.5, color=style["color"]),
        )

    fig.add_trace(go.Scatter(
        x=weeks, y=league_avg.values, name="League average", mode="lines",
        line=dict(color=theme.INK_MUTE, width=1.6, dash="dot"),
        showlegend=False,
        hovertemplate="League avg<br>Week %{x} · %{y:.1f} pts<extra></extra>",
    ))

    style_fig(fig, hovermode="closest", showlegend=False, height=460,
             margin=dict(t=8, b=8, l=8, r=190))
    fig.update_xaxes(title_text="WEEK", dtick=1, range=x_range)
    fig.update_yaxes(title_text="POINTS")
    return fig


def total_lines(cum_df, styles, logos=None):
    """
    Running season totals: cumulative points for (solid) and against (dashed).

    The PA lines start hidden -- 2N always-on lines drowned the chart -- and
    are tagged meta="pa" so bind_hover_dim / set_highlight can reveal a
    team's PA line only while that team is hovered or highlighted. Both of a
    team's lines carry the same trace name, so the pair moves as one unit.
    The name and logo sit at the PF endpoint only; a revealed PA line is
    identified by its color and the hover text.
    """
    if cum_df.empty:
        return empty_fig()

    logos = logos or {}
    fig = go.Figure()
    weeks = sorted(cum_df["week"].unique())
    x_pad = 0.6
    x_range = [weeks[0] - x_pad, weeks[-1] + x_pad]
    names = sorted(cum_df["team_name"].unique())

    finals = {}
    for name in names:
        rows = cum_df[cum_df["team_name"] == name].sort_values("week")
        if not rows.empty:
            finals[name] = rows.iloc[-1]["cum_pf"]
    # Label spacing works off the full y-extent, PA lines included, because
    # they share the axis even though only PF endpoints get a name.
    y_min = min(cum_df["cum_pf"].min(), cum_df["cum_pa"].min())
    y_max = max(cum_df["cum_pf"].max(), cum_df["cum_pa"].max())
    span = (y_max - y_min) or 1.0
    label_y = spread_labels(list(finals.items()), min_gap=span * 17 / 360)

    for name in names:
        rows = cum_df[cum_df["team_name"] == name].sort_values("week")
        style = styles.get(name, {"color": theme.INK_DIM, "dash": "solid"})
        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["cum_pf"], name=name, mode="lines",
            line=dict(color=style["color"], width=3.2, dash=style["dash"]),
            showlegend=False,
            hovertemplate=(f"<b>{name}</b><br>Week %{{x}} · "
                           "%{y:.1f} PF<extra></extra>"),
        ))
        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["cum_pa"], name=name, mode="lines",
            line=dict(color=style["color"], width=1.6, dash="dash"),
            showlegend=False, visible=False, meta="pa",
            hovertemplate=(f"<b>{name}</b><br>Week %{{x}} · "
                           "%{y:.1f} PA<extra></extra>"),
        ))
        last = rows.iloc[-1]
        label_pos = label_y.get(name, last["cum_pf"])
        logo = logos.get(name)
        if logo:
            _endpoint_logo(fig, logo, last["week"], label_pos, x_range,
                           sizey=span * 30 / 360)
        fig.add_annotation(
            x=last["week"], y=label_pos, text=name,
            xshift=_LABEL_SHIFT_LOGO if logo else _LABEL_SHIFT_PLAIN,
            showarrow=False, xanchor="left", align="left",
            font=dict(family=_FONT, size=11.5, color=style["color"]),
        )

    style_fig(fig, hovermode="closest", showlegend=False, height=460,
             margin=dict(t=8, b=8, l=8, r=190))
    fig.update_xaxes(title_text="WEEK", dtick=1, range=x_range)
    fig.update_yaxes(title_text="TOTAL POINTS")
    return fig


def bind_hover_dim(widget):
    """
    Hovering a line dims every other line to ~20% opacity.

    Runs through the ipywidgets comm shinywidgets already opens for the
    FigureWidget, so this is plain Python -- no client-side JS to keep in
    step with the trace list.
    """
    def _dim(target_name):
        with widget.batch_update():
            for trace in widget.data:
                if trace.meta == "pa":
                    trace.visible = trace.name == target_name
                trace.opacity = 1.0 if trace.name == target_name else 0.2

    def _reset():
        with widget.batch_update():
            for trace in widget.data:
                if trace.meta == "pa":
                    trace.visible = False
                trace.opacity = 1.0

    for trace in widget.data:
        trace.on_hover(lambda t, p, s, name=trace.name: _dim(name))
        trace.on_unhover(lambda t, p, s: _reset())


def set_highlight(widget, team_name):
    """Dims every line but `team_name` -- the click-driven counterpart to
    bind_hover_dim, called from a reactive effect on the `team` value so
    picking a team pill elsewhere highlights its line here too."""
    if widget is None:
        return
    with widget.batch_update():
        for trace in widget.data:
            if trace.meta == "pa":
                trace.visible = team_name == trace.name
            trace.opacity = 1.0 if team_name in (None, trace.name) else 0.2


# ----------------------------------------------------------- luck and odds

def _color(styles, name):
    return styles.get(name, {}).get("color", theme.INK_DIM)


def _padded_range(values, frac=0.18):
    """An axis range with breathing room on both ends, so a marker or logo
    at the extreme is never clipped by the plot edge."""
    lo, hi = float(min(values)), float(max(values))
    span = (hi - lo) or max(abs(hi), 1.0)
    return [lo - span * frac, hi + span * frac]


def luck_quadrant(luck_df, styles, logos=None):
    """
    Points for against points against, one marker per team.

    Dashed lines at the league means split the plane into four: right of
    the vertical line a team scores more than most, above the horizontal
    one it faces more than most. The all-play luck number lives in the
    hover, since a chart of it would be a bar chart; what the scatter adds
    is *why* a team's luck reads the way it does. A team's logo stands in
    for its marker when one is supplied, the same way the race chart does
    at line endpoints.
    """
    if luck_df is None or luck_df.empty:
        return empty_fig("No games played yet.")

    logos = logos or {}
    fig = go.Figure()
    x_range = _padded_range(luck_df["points_for"])
    y_range = _padded_range(luck_df["points_against"])
    x_span = x_range[1] - x_range[0]
    y_span = y_range[1] - y_range[0]
    x_mean = float(luck_df["points_for"].mean())
    y_mean = float(luck_df["points_against"].mean())

    for _, row in luck_df.iterrows():
        name = row["team_name"]
        color = _color(styles, name)
        logo = logos.get(name)
        record = f"{int(row['wins'])}-{int(row['losses'])}"
        fig.add_trace(go.Scatter(
            x=[row["points_for"]], y=[row["points_against"]], name=name,
            mode="markers",
            # An invisible marker under a logo keeps the hover target where
            # the logo is; Plotly does not hover layout images.
            marker=dict(size=16, color=color, opacity=0 if logo else 1,
                        line=dict(width=1.5, color=theme.GROUND)),
            showlegend=False,
            hovertemplate=(
                f"<b>{name}</b> · {record}<br>"
                f"{row['points_for']:.0f} PF / {row['points_against']:.0f} PA<br>"
                f"Expected {row['expected_wins']:.1f} wins · "
                f"luck {row['luck']:+.1f}<extra></extra>"),
        ))
        if logo:
            fig.add_layout_image(
                source=logo, xref="x", yref="y",
                x=row["points_for"], y=row["points_against"],
                xanchor="center", yanchor="middle",
                sizex=x_span * 0.075, sizey=y_span * 0.10,
                sizing="contain", layer="above",
            )
        fig.add_annotation(
            x=row["points_for"], y=row["points_against"], text=name,
            yshift=-(22 if logo else 16), showarrow=False,
            font=dict(family=_FONT, size=10.5, color=color),
        )

    line = dict(color=theme.INK_MUTE, width=1, dash="dash")
    fig.add_shape(type="line", x0=x_mean, x1=x_mean, y0=y_range[0], y1=y_range[1], line=line)
    fig.add_shape(type="line", x0=x_range[0], x1=x_range[1], y0=y_mean, y1=y_mean, line=line)

    # Corner labels in paper coordinates, so they sit in the same place
    # whatever the data's spread.
    corners = [
        ("Good but unlucky", 0.99, 0.99, "right", "top"),
        ("Bad and unlucky", 0.01, 0.99, "left", "top"),
        ("Good and lucky", 0.99, 0.01, "right", "bottom"),
        ("Bad but lucky", 0.01, 0.01, "left", "bottom"),
    ]
    for text, x, y, xanchor, yanchor in corners:
        fig.add_annotation(
            xref="paper", yref="paper", x=x, y=y, text=text.upper(),
            xanchor=xanchor, yanchor=yanchor, showarrow=False,
            font=dict(family=_MONO, size=10, color=theme.INK_MUTE),
        )

    style_fig(fig, hovermode="closest", showlegend=False,
              margin=dict(t=8, b=8, l=8, r=8))
    fig.update_xaxes(title_text="POINTS FOR", range=x_range)
    fig.update_yaxes(title_text="POINTS AGAINST", range=y_range)
    return fig


def _bar_height(count):
    """Enough height for `count` horizontal bars to keep their labels
    legible: a 12-team league gets more room than a 4-team one."""
    return max(260, 32 * count + 60)


def playoff_odds_bars(odds_df, styles):
    """
    Playoff odds as horizontal bars in team colors, best odds at the top.

    Plotly stacks horizontal categories bottom-up, so the frame is sorted
    ascending to put the favourite on top. The x-axis runs past 100 so an
    outside label on a 100% bar has somewhere to go.
    """
    if odds_df is None or odds_df.empty:
        return empty_fig("No games played yet.")

    df = odds_df.sort_values(["playoff_odds", "avg_wins"], ascending=[True, True])
    names = df["team_name"].tolist()
    pct = (df["playoff_odds"] * 100).tolist()
    fig = go.Figure(go.Bar(
        x=pct, y=names, orientation="h",
        marker=dict(color=[_color(styles, n) for n in names]),
        text=[f"{p:.0f}%" for p in pct], textposition="outside",
        textfont=dict(family=_MONO, size=11, color=theme.INK),
        customdata=list(zip(df["avg_wins"], df["games_left"], df["top_seed_odds"] * 100)),
        hovertemplate=("<b>%{y}</b><br>%{x:.1f}% to make the playoffs<br>"
                       "%{customdata[2]:.1f}% for the top seed<br>"
                       "%{customdata[0]:.1f} projected wins · "
                       "%{customdata[1]} left<extra></extra>"),
        cliponaxis=False,
    ))
    style_fig(fig, showlegend=False, height=_bar_height(len(names)),
              margin=dict(t=8, b=8, l=8, r=48))
    fig.update_xaxes(title_text="PLAYOFF ODDS", range=[0, 112], ticksuffix="%",
                     showgrid=True)
    fig.update_yaxes(showgrid=False, tickfont=dict(family=_FONT, size=11.5,
                                                   color=theme.INK_DIM))
    return fig


def projection_bars(acc_df, styles):
    """
    Mean actual-minus-projected per team: a bar to the right for a team
    that beats its number, to the left for one that falls short, in the
    win and loss colors rather than team colors because the sign is the
    whole story. `styles` is accepted for signature parity with the other
    builders and unused.
    """
    if acc_df is None or acc_df.empty:
        return empty_fig("No projections collected yet.")

    df = acc_df.sort_values("mean_delta", ascending=True)
    names = df["team_name"].tolist()
    delta = df["mean_delta"].tolist()
    fig = go.Figure(go.Bar(
        x=delta, y=names, orientation="h",
        marker=dict(color=[theme.WIN if d >= 0 else theme.LOSS for d in delta]),
        text=[f"{d:+.1f}" for d in delta], textposition="outside",
        textfont=dict(family=_MONO, size=11, color=theme.INK),
        customdata=list(zip(df["beat_rate"] * 100, df["mae"], df["games"])),
        hovertemplate=("<b>%{y}</b><br>%{x:+.1f} vs projection on average<br>"
                       "over in %{customdata[0]:.0f}% of %{customdata[2]} weeks · "
                       "%{customdata[1]:.1f} pt typical miss<extra></extra>"),
        cliponaxis=False,
    ))
    reach = max(abs(d) for d in delta) or 1.0
    style_fig(fig, showlegend=False, height=_bar_height(len(names)),
              margin=dict(t=8, b=8, l=8, r=40))
    fig.update_xaxes(title_text="POINTS VS PROJECTION",
                     range=[-reach * 1.35, reach * 1.35], zeroline=True)
    fig.update_yaxes(showgrid=False, tickfont=dict(family=_FONT, size=11.5,
                                                   color=theme.INK_DIM))
    return fig


# ---------------------------------------------------------------- lineups

# One color per position, reused from the team palette because a stacked
# bar puts teams on the axis rather than in color: the hues are already
# proven to hold apart on GROUND, and a reader never sees both keys on one
# chart.
POSITION_COLORS = {
    "QB": "#EE6677",
    "RB": "#44BB77",
    "WR": "#6699DD",
    "TE": "#EE9944",
    "D/ST": "#CC66AA",
    "K": "#DDCC55",
}


def position_stack(contrib_df, styles, order=None):
    """
    Starting points by position, one stacked horizontal bar per team.

    Teams are labelled by `team_name` when the frame carries one (see
    position_contribution's `names`) and by id otherwise. `order` is the
    list of labels top to bottom; the default is by total, biggest first.
    `styles` is accepted for signature parity and unused, since the bars
    are colored by position.
    """
    if contrib_df is None or contrib_df.empty:
        return empty_fig("No lineups collected yet.")

    df = contrib_df.copy()
    label_col = "team_name" if "team_name" in df.columns else "team_id"
    df["label"] = df[label_col].astype(str)
    if order:
        wanted = [str(o) for o in order if str(o) in set(df["label"])]
        df = df.set_index("label").loc[wanted].reset_index()
    else:
        df = df.sort_values("total", ascending=False)
    # Bottom-up stacking: reverse so the first label lands on top.
    df = df.iloc[::-1]
    labels = df["label"].tolist()

    fig = go.Figure()
    positions = [p for p in POSITION_COLORS if p in df.columns]
    for pos in positions:
        share = df.get(f"share_{pos}")
        custom = (share * 100).tolist() if share is not None else [0] * len(df)
        fig.add_trace(go.Bar(
            x=df[pos], y=labels, name=pos, orientation="h",
            marker=dict(color=POSITION_COLORS[pos],
                        line=dict(width=0.5, color=theme.GROUND)),
            customdata=custom,
            hovertemplate=(f"<b>%{{y}}</b> · {pos}<br>%{{x:.1f}} pts · "
                           "%{customdata:.0f}% of starters<extra></extra>"),
        ))

    style_fig(fig, barmode="stack", height=_bar_height(len(labels)),
              margin=dict(t=32, b=8, l=8, r=8))
    fig.update_xaxes(title_text="STARTING POINTS")
    fig.update_yaxes(showgrid=False, tickfont=dict(family=_FONT, size=11.5,
                                                   color=theme.INK_DIM))
    return fig


# ------------------------------------------------------------------ draft

def draft_return_scatter(dr_df, styles):
    """
    Season points by draft slot, with the slot's expected return as a line.

    Markers take the drafting team's color, so a manager can find their own
    picks at a glance; the steals and busts draft_return tagged get the
    player's name, in win or loss color, since those five-and-five are the
    picks the chart exists to argue about.
    """
    if dr_df is None or dr_df.empty:
        return empty_fig("No draft picks recorded.")

    df = dr_df.sort_values("overall_pick")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["overall_pick"], y=df["expected"], name="Expected",
        mode="lines", line=dict(color=theme.INK_MUTE, width=1.6, dash="dot"),
        hovertemplate="Pick %{x} · %{y:.1f} expected<extra></extra>",
    ))

    for name, picks in df.groupby("team_name", sort=True):
        color = _color(styles, name)
        fig.add_trace(go.Scatter(
            x=picks["overall_pick"], y=picks["total_points"], name=name,
            mode="markers",
            marker=dict(size=9, color=color, line=dict(width=1, color=theme.GROUND)),
            customdata=list(zip(picks["player_name"], picks["position"].fillna(""),
                                picks["round_num"], picks["delta"])),
            hovertemplate=(f"<b>%{{customdata[0]}}</b> %{{customdata[1]}}<br>"
                           f"{name} · round %{{customdata[2]}}, pick %{{x}}<br>"
                           "%{y:.1f} pts · %{customdata[3]:+.1f} vs expected"
                           "<extra></extra>"),
        ))

    tagged = df[df["tag"] != ""]
    for _, pick in tagged.iterrows():
        steal = pick["tag"] == "steal"
        fig.add_annotation(
            x=pick["overall_pick"], y=pick["total_points"],
            text=pick["player_name"], showarrow=True, arrowhead=0,
            arrowcolor=theme.INK_MUTE, ax=0, ay=-22 if steal else 22,
            font=dict(family=_FONT, size=10.5,
                      color=theme.WIN if steal else theme.LOSS),
        )

    style_fig(fig, hovermode="closest", margin=dict(t=32, b=8, l=8, r=8))
    fig.update_xaxes(title_text="OVERALL PICK")
    fig.update_yaxes(title_text="SEASON POINTS")
    return fig
