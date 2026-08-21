"""
Visual tokens for the dashboard: the team palette, the color assignment that
keeps a team the same color across every tab, and the inline sparkline used in
the stat tiles.

Deliberately free of Shiny and Plotly imports so both the chart builders and
the HTML-producing parts of the app can pull from one source of truth. The
matching CSS custom properties live in www/dashboard.css.
"""

# Chrome colors, mirrored from :root in www/dashboard.css. Duplicated rather
# than parsed out of the stylesheet because Plotly needs literal hex -- it
# renders to canvas and never sees the page's CSS variables.
GROUND = "#0E1116"
SURFACE = "#161A21"
RAISED = "#1E242D"
LINE = "#2A313B"
INK = "#F2F5F8"
INK_DIM = "#98A4B3"
INK_MUTE = "#5E6A78"
ACCENT = "#F0631E"
WIN = "#4CB782"
LOSS = "#E4574C"

# Derived from Paul Tol's "bright" qualitative scheme -- chosen over Plotly's
# Dark24 because half of Dark24 is near-black and vanishes on GROUND. The
# darker entries are lightened here to hold contrast against a dark ground;
# lightening preserves hue, so the scheme stays colorblind-safe.
TEAM_COLORS = [
    "#6699DD",  # blue
    "#EE6677",  # rose
    "#44BB77",  # green
    "#DDCC55",  # sand
    "#66CCEE",  # cyan
    "#CC66AA",  # purple
    "#EE9944",  # amber
    "#AAAAAA",  # grey
]

# Leagues bigger than the palette wrap around to the same hues, so the second
# time through we vary the line dash instead. Eight distinct hues is already
# at the limit of what a reader can tell apart on crossing lines; a 12-team
# league gets hue + dash rather than four more colors nobody can name.
DASH_CYCLE = ["solid", "dash", "dot", "dashdot"]

def team_styles(teams):
    """
    Maps team name -> {"color", "dash"}, assigned by team id.

    Keyed on id rather than name because a mid-season team rename would
    otherwise reshuffle every color on the board: the previous implementation
    sorted names and assigned by position, so one team renaming itself moved
    everyone that sorted after it.

    `teams` is an iterable of (team_id, team_name) pairs.
    """
    ordered = sorted({(int(tid), name) for tid, name in teams})
    styles = {}
    for i, (_, name) in enumerate(ordered):
        styles[name] = {
            "color": TEAM_COLORS[i % len(TEAM_COLORS)],
            "dash": DASH_CYCLE[(i // len(TEAM_COLORS)) % len(DASH_CYCLE)],
        }
    return styles


def team_colors(teams):
    """Just the color half of team_styles, shaped for Plotly's color_discrete_map."""
    return {name: style["color"] for name, style in team_styles(teams).items()}


def sparkline(values, width=116, height=26, color=None):
    """
    A bare inline SVG sparkline for the stat tiles.

    Inline SVG rather than a fifth Plotly figure: these are 100px decorations
    that redraw on every poll, and a widget each would cost far more than the
    string concatenation below.

    Returns an empty string for fewer than two points, so callers can drop it
    into a template unconditionally.
    """
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return ""

    lo, hi = min(pts), max(pts)
    span = hi - lo or 1.0
    pad = 3
    step = (width - 2 * pad) / (len(pts) - 1)

    coords = " ".join(
        f"{pad + i * step:.1f},{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(pts)
    )
    last_x = pad + (len(pts) - 1) * step
    last_y = height - pad - (pts[-1] - lo) / span * (height - 2 * pad)
    stroke = color or ACCENT

    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" aria-hidden="true" preserveAspectRatio="none">'
        f'<polyline points="{coords}" fill="none" stroke="{stroke}" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.2" fill="{stroke}"/>'
        f"</svg>"
    )
