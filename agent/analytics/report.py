"""
The PDF report: a Quarto document assembled from the analyst's narrative,
the charts, and the tables, rendered to PDF through Typst.

The .qmd stays next to the PDF, so the report can be re-knit by hand with
`quarto render report.qmd --to typst` after an edit. Without Quarto on the
PATH, render() reports why and the caller posts the charts instead.
"""
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# The narrative sections the analyst writes, in report order, with headings.
SECTIONS = [
    ("summary", "Summary"),
    ("matchup_notes", "This week's matchup"),
    ("roster_notes", "My roster"),
    ("trend_notes", "Usage trends"),
    ("waiver_notes", "Waiver wire"),
    ("trade_notes", "Trade market"),
    ("divergence_notes", "Where the model disagrees with ESPN"),
]

_NON_BMP = re.compile("[\U00010000-\U0010FFFF]")


def clean(text):
    """Drop emoji and other characters the report fonts cannot draw; team names carry plenty."""
    return _NON_BMP.sub("", str(text or "")).strip()


def _fmt(v, digits=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    if isinstance(v, (list, tuple)):
        return "; ".join(clean(x) for x in v) or "–"
    return clean(v).replace("|", "/")


def table(df, columns, headers=None, pct=(), signed=()):
    """A markdown pipe table from chosen columns; pct columns render as whole percents."""
    if df is None or df.empty:
        return "_None this week._\n"
    headers = headers or columns
    # Pandoc sizes a wide pipe table's columns by the dashes under each header,
    # so names get room and numbers stay narrow; text left, numbers right.
    numeric = [c in pct or c in signed or pd.api.types.is_numeric_dtype(df[c]) if c in df.columns else False
               for c in columns]
    widths = [24 if i == 0 else (6 if num else 14) for i, num in enumerate(numeric)]
    rule = ["-" * (w - 1) + ":" if num else ":" + "-" * (w - 1) for w, num in zip(widths, numeric)]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(rule) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in columns:
            v = r.get(c)
            if c in pct and v is not None and not pd.isna(v):
                cells.append(f"{float(v) * 100:.0f}%")
            elif c in signed and v is not None and not pd.isna(v):
                cells.append(f"{float(v):+.1f}")
            else:
                cells.append(_fmt(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _figure(charts, key, caption):
    path = charts.get(key)
    if not path:
        return ""
    return f"![{caption}]({Path(path).name}){{width=100%}}\n"


def method_section(meta):
    coefs = meta.get("coefs") or {}
    lines = [
        f"Data: nflverse weekly player stats and snap counts for NFL weeks "
        f"{', '.join(str(w) for w in meta.get('data_weeks') or []) or 'none'} of {meta.get('year')}, "
        f"last season's weekly stats as a prior, and the schedule with Vegas lines. ESPN supplies rosters, "
        f"projections, injury tags, and the free-agent pool. {meta.get('players_modelled', 0)} players modelled.",
        "",
        "Expected points (xFP) are fitted by least squares on this season and last, per position, from "
        "opportunity only:",
        "",
    ]
    for pos in ("QB", "RB", "WR", "TE"):
        c = coefs.get(pos) or {}
        terms = ", ".join(f"{k.replace('_', ' ')} {v:.3f}" for k, v in c.items() if k not in ("const", "r2", "n"))
        fit = f" (R² {c['r2']:.2f}, {c['n']} player-weeks)" if "r2" in c else " (default weights)"
        lines.append(f"- {pos}: {terms}{fit}")
    lines += [
        "",
        "A forecast is a recency-weighted xFP (half-life two weeks), blended with last season's xFP per game "
        "as two games of evidence, plus points over expectation shrunk by six games toward zero. It is scaled "
        "by the Vegas implied team total against the week's average and by the opponent's points allowed to "
        "the position, shrunk by four games, within 0.75 to 1.3. Ranges are the 10th and 90th percentiles of "
        "a lognormal using each position's week-to-week spread "
        f"({', '.join(f'{k} {v:.2f}' for k, v in (meta.get('cv') or {}).items())}). Players tagged Out, IR, "
        "or Suspended, or on bye, forecast zero; Doubtful keeps a quarter. Kickers and D/STs use ESPN's "
        "projection. Rest-of-season runs through NFL week "
        f"{meta.get('end_week')}; value over replacement is measured against the second-best free agent at "
        "each position.",
    ]
    if meta.get("missing_sources"):
        lines += ["", f"Missing this run: {', '.join(meta['missing_sources'])}."]
    return "\n".join(lines)


def build_qmd(analysis, narrative):
    meta, t, ch, m = analysis.meta, analysis.tables, analysis.charts, analysis.matchup
    team = clean(meta.get("team"))
    week = meta.get("target_week")
    parts = [
        "---",
        f'title: "{team}: week {week} analytics"',
        f'subtitle: "In-house model, data through NFL week {meta.get("data_through") or "?"}"',
        f'date: "{meta.get("generated_at", "")[:10]}"',
        "format:",
        "  typst:",
        "    papersize: us-letter",
        "    margin:",
        "      x: 0.75in",
        "      y: 0.7in",
        "    fontsize: 9.5pt",
        "---",
        "",
    ]
    n = {k: clean(narrative.get(k)) for k, _ in SECTIONS}
    n["caveats"] = clean(narrative.get("caveats"))

    parts += ["## Summary", "", n["summary"] or "_No summary written._", ""]

    parts += ["## This week's matchup", "", n["matchup_notes"], ""]
    parts.append(_figure(ch, "matchup", "Team score distributions"))
    if m.get("win_prob") is not None:
        parts.append(f"Win probability as set: **{m['win_prob'] * 100:.0f}%**; with the model's best lineup: "
                     f"**{m['win_prob_best'] * 100:.0f}%**.")
        if m.get("swaps_in"):
            parts.append(f" The model would start {', '.join(m['swaps_in'])} over {', '.join(m['swaps_out'])}.")
        parts.append("\n")
    if not t["opponent"].empty:
        parts += [f"**{clean(m.get('opponent'))} starters**", "",
                  table(t["opponent"], ["name", "slot", "espn_proj", "mean", "p10", "p90", "injury"],
                        ["Player", "Slot", "ESPN", "Model", "P10", "P90", "Tag"]), ""]

    parts += ["## My roster", "", n["roster_notes"], "", _figure(ch, "roster_ranges", "Roster forecasts"), "",
              table(t["my_roster"], ["name", "slot", "espn_proj", "mean", "p10", "p90", "ros_pg", "xfp_pg", "fpoe_pg"],
                    ["Player", "Slot", "ESPN", "Model", "P10", "P90", "ROS/g", "xFP/g", "Over xFP"],
                    signed=("fpoe_pg",)), ""]
    flagged = t["my_roster"][t["my_roster"]["flags"].map(bool)]
    if not flagged.empty:
        parts += ["**Signals**", ""] + [f"- {clean(r['name'])}: {_fmt(r['flags'])}" for _, r in flagged.iterrows()] + [""]

    parts += ["## Usage trends", "", n["trend_notes"], "", _figure(ch, "usage_trends", "Opportunity share by week"), ""]

    parts += ["## Waiver wire", "", n["waiver_notes"], "", _figure(ch, "waiver_ranges", "Free-agent forecasts"), "",
              table(t["waiver"], ["name", "position", "status", "espn_proj", "mean", "ros_pg", "vor_ros", "share"],
                    ["Player", "Pos", "Status", "ESPN", "Model", "ROS/g", "VOR", "Share"], pct=("share",)), ""]

    parts += ["## Trade market", "", n["trade_notes"], "", _figure(ch, "luck_scatter", "Usage against production"), "",
              "**Targets the market may be underpricing**", "",
              table(t["trade_targets"], ["name", "position", "fantasy_team", "espn_avg", "ros_pg", "market_gap", "vor_ros"],
                    ["Player", "Pos", "Team", "PPG so far", "ROS/g", "Gap", "VOR"], signed=("market_gap",)), "",
              "**My players the market may be overpricing**", "",
              table(t["sell_candidates"], ["name", "position", "espn_avg", "ros_pg", "market_gap", "fpoe_pg"],
                    ["Player", "Pos", "PPG so far", "ROS/g", "Gap", "Over xFP"], signed=("market_gap", "fpoe_pg")), ""]

    parts += ["## Where the model disagrees with ESPN", "", n["divergence_notes"], "",
              _figure(ch, "divergence", "Model minus ESPN"), "",
              table(t["divergences"], ["name", "position", "fantasy_team", "espn_proj", "mean", "edge", "opponent", "implied"],
                    ["Player", "Pos", "Team", "ESPN", "Model", "Diff", "Opp", "Implied"], signed=("edge",)), ""]

    parts += ["## Method and caveats", "", method_section(meta), ""]
    if n["caveats"]:
        parts += [n["caveats"], ""]
    return "\n".join(p for p in parts if p is not None)


def quarto_bin():
    return os.environ.get("QUARTO_PATH") or shutil.which("quarto")


def render(analysis, narrative, out_dir, timeout=240):
    """Write report.qmd and render report.pdf. Returns (pdf path or None, error or None)."""
    out_dir = Path(out_dir)
    qmd = out_dir / "report.qmd"
    qmd.write_text(build_qmd(analysis, narrative), encoding="utf-8")
    exe = quarto_bin()
    if not exe:
        return None, "Quarto is not installed here"
    try:
        r = subprocess.run([exe, "render", qmd.name, "--to", "typst"], cwd=out_dir, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"quarto render failed: {e}"
    pdf = out_dir / "report.pdf"
    if r.returncode != 0 or not pdf.exists():
        tail = (r.stderr or r.stdout or "")[-800:]
        logger.error("quarto render failed: %s", tail)
        return None, f"quarto render exited {r.returncode}: {tail}"
    return pdf, None
