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

    Both of a team's lines carry the same trace name, so bind_hover_dim and
    set_highlight treat the pair as one unit -- hovering either line keeps
    both bright, which is what makes 2N lines readable at all. The name and
    logo sit at the PF endpoint only; the dashed PA line is identified by its
    color and the hover text.
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
            showlegend=False,
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
                trace.opacity = 1.0 if trace.name == target_name else 0.2

    def _reset():
        with widget.batch_update():
            for trace in widget.data:
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
            trace.opacity = 1.0 if team_name in (None, trace.name) else 0.2
