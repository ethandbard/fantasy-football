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


def _line_width(team, focus):
    if focus is None:
        return 2.4
    return 3.2 if team == focus else 1.4


def _opacity(team, focus):
    if focus is None or team == focus:
        return 1.0
    return theme.MUTED_OPACITY


def score_trend(df, styles, focus=None, highlight_week=None):
    """
    Week-by-week score, one line per team.

    Built trace-by-trace rather than through px.line so each team's opacity
    and width can answer the focus selection, and so the hover text reads as
    a sentence instead of the raw column names px emits.

    `highlight_week` marks the week a trophy card sent the reader to, so the
    jump lands somewhere visible instead of leaving them to find week 13 by
    eye.
    """
    fig = go.Figure()
    for name in sorted(df["team_name"].unique()):
        rows = df[df["team_name"] == name].sort_values("week")
        style = styles.get(name, {"color": theme.INK_DIM, "dash": "solid"})
        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["score"], name=name, mode="lines+markers",
            line=dict(color=style["color"], width=_line_width(name, focus),
                      dash=style["dash"], shape="spline", smoothing=0.4),
            marker=dict(size=6, color=style["color"]),
            opacity=_opacity(name, focus),
            hovertemplate=f"<b>{name}</b><br>Week %{{x}} · %{{y:.1f}} pts<extra></extra>",
        ))

    if highlight_week is not None:
        fig.add_vline(
            x=highlight_week, line=dict(color=theme.ACCENT, width=1, dash="dot"),
            annotation_text=f"WK {highlight_week}",
            annotation_position="top",
            annotation_font=dict(family=_MONO, size=10, color=theme.ACCENT),
        )

    style_fig(fig, hovermode="closest")
    fig.update_xaxes(title_text="WEEK", dtick=1)
    fig.update_yaxes(title_text="POINTS")
    return fig


def spread_box(df, styles, order, focus=None):
    """
    Score distribution per team, horizontal.

    Horizontal because the vertical version could not fit a team name under a
    box -- which is why the old build stripped emoji off the axis labels and
    ended up calling the same team two different things on two tabs. Laid on
    its side there is room for the full name, so one display name serves
    everywhere.

    The boxes themselves skip hover: pointing at one used to raise seven
    separate tags (min, lower fence, q1, median, q3, upper fence, max) spread
    across its neighbours. The five-number summary belongs in the table beside
    the chart, where it can be read and compared; the tooltip is left to the
    individual weeks.
    """
    fig = go.Figure()
    for name in order:
        rows = df[df["team_name"] == name]
        style = styles.get(name, {"color": theme.INK_DIM})
        fig.add_trace(go.Box(
            x=rows["score"], name=name, boxpoints="all", jitter=0.5, pointpos=0,
            fillcolor=style["color"], opacity=_opacity(name, focus),
            line=dict(color=style["color"], width=1.4),
            marker=dict(color=theme.INK, size=5, opacity=0.55),
            hoveron="points",
            customdata=rows["week"],
            hovertemplate=f"<b>{name}</b><br>Week %{{customdata}} · %{{x:.1f}} pts<extra></extra>",
        ))

    style_fig(fig, showlegend=False, boxgap=0.35)
    fig.update_xaxes(title_text="POINTS")
    fig.update_yaxes(
        title_text="", autorange="reversed",
        categoryorder="array", categoryarray=list(order),
    )
    return fig


def cumulative_points(df, styles, focus=None):
    """Season points as a race -- who is actually pulling away."""
    fig = go.Figure()
    for name in sorted(df["team_name"].unique()):
        rows = df[df["team_name"] == name].sort_values("week")
        style = styles.get(name, {"color": theme.INK_DIM, "dash": "solid"})
        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["score"].cumsum(), name=name, mode="lines",
            line=dict(color=style["color"], width=_line_width(name, focus), dash=style["dash"]),
            opacity=_opacity(name, focus),
            hovertemplate=f"<b>{name}</b><br>Week %{{x}} · %{{y:,.0f}} total<extra></extra>",
        ))

    style_fig(fig, hovermode="closest")
    fig.update_xaxes(title_text="WEEK", dtick=1)
    fig.update_yaxes(title_text="POINTS, RUNNING TOTAL")
    return fig


def rank_curve(ranked, styles, focus=None):
    """
    Standing after every week, first place at the top.

    The y-axis is reversed because a rank of 1 is the good end, and a chart
    where the leader sits at the bottom reads backwards no matter how it is
    labelled.
    """
    fig = go.Figure()
    teams = int(ranked["rank"].max()) if not ranked.empty else 1

    for name in sorted(ranked["team_name"].unique()):
        rows = ranked[ranked["team_name"] == name].sort_values("week")
        style = styles.get(name, {"color": theme.INK_DIM, "dash": "solid"})
        fig.add_trace(go.Scatter(
            x=rows["week"], y=rows["rank"], name=name, mode="lines+markers",
            line=dict(color=style["color"], width=_line_width(name, focus),
                      dash=style["dash"], shape="hv"),
            marker=dict(size=6, color=style["color"]),
            opacity=_opacity(name, focus),
            hovertemplate=f"<b>{name}</b><br>Week %{{x}} · rank %{{y}}<extra></extra>",
        ))

    style_fig(fig, hovermode="closest")
    fig.update_xaxes(title_text="WEEK", dtick=1)
    fig.update_yaxes(title_text="RANK", autorange="reversed",
                     dtick=1, range=[teams + 0.5, 0.5])
    return fig


def projection_bars(proj, styles, focus=None):
    """
    Average points over or under projection, one diverging bar per team.

    Per team rather than per team-week: a bar for every one of a hundred-odd
    team-weeks is a texture, not a chart. The season average answers the
    question the tab actually raises -- who beats their projection.
    """
    proj = proj.sort_values("vs_proj")
    colors = [theme.WIN if v >= 0 else theme.LOSS for v in proj["vs_proj"]]
    opacities = [_opacity(n, focus) for n in proj["team_name"]]

    fig = go.Figure(go.Bar(
        x=proj["vs_proj"], y=proj["team_name"], orientation="h",
        marker=dict(color=colors, opacity=opacities,
                    line=dict(width=0)),
        customdata=proj["weeks"],
        hovertemplate="<b>%{y}</b><br>%{x:+.1f} pts vs projection"
                      "<br>over %{customdata} weeks<extra></extra>",
    ))

    style_fig(fig, showlegend=False)
    fig.update_xaxes(title_text="AVERAGE POINTS VS PROJECTION", zeroline=True,
                     zerolinecolor=theme.INK_MUTE, zerolinewidth=1)
    fig.update_yaxes(title_text="")
    return fig


def h2h_grid(records, margins):
    """
    Every pairing's record, tinted by average margin.

    Read row-versus-column: the cell where a row team meets a column team
    holds the row team's record against them. The diagonal is blank because a
    team does not play itself.
    """
    teams = list(records.index)
    text = [[records.at[r, c] if r != c else "" for c in teams] for r in teams]

    # The diagonal is NaN -- nobody plays themselves -- and JSON has no way to
    # spell NaN, so the widget fails to serialise unless the gaps are None.
    # NaN is the only value that compares unequal to itself.
    z = [[None if v != v else float(v) for v in row] for row in margins.values]

    fig = go.Figure(go.Heatmap(
        z=z, x=teams, y=teams, text=text,
        texttemplate="%{text}", textfont=dict(family=_MONO, size=12),
        colorscale=[[0, theme.LOSS], [0.5, theme.SURFACE], [1, theme.WIN]],
        zmid=0, xgap=2, ygap=2,
        colorbar=dict(
            title=dict(text="AVG MARGIN", font=dict(size=10, family=_MONO)),
            tickfont=dict(size=10, family=_MONO, color=theme.INK_MUTE),
            outlinewidth=0, thickness=10,
        ),
        hovertemplate="<b>%{y}</b> vs %{x}<br>%{text} · %{z:+.1f} avg margin<extra></extra>",
    ))

    style_fig(fig, height=520)
    fig.update_xaxes(side="top", tickangle=-35, title_text="")
    fig.update_yaxes(autorange="reversed", title_text="")
    return fig
