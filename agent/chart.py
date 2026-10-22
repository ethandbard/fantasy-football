"""
The weekly rest-of-season trade value chart, fetched once and cached.

FantasyPros publishes one page per week at a URL that carries the publish
month, so the fetch tries this month and last month. The page holds one
HTML table per position, each introduced by a heading such as "Running
Back Fantasy Football Trade Value Chart", with Name, Team, Value, and Bye
columns. The parsed chart is saved under data/agent/chart/week-NN.json;
when this week's page is not up yet, the newest cached week is used and
the result says which week that is.
"""
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

URL = "https://www.fantasypros.com/{year}/{month:02d}/fantasy-football-trade-value-chart-week-{week}-{year}/"
HEADERS = {"User-Agent": "Mozilla/5.0 (fantasy-agent chart fetch)"}
POSITION_WORDS = {"quarterback": "QB", "running back": "RB", "wide receiver": "WR", "tight end": "TE",
                  "kicker": "K", "defense": "D/ST"}
_SUFFIXES = (" jr.", " jr", " sr.", " sr", " iii", " ii", " iv", " v")


def normalize(name):
    """Lowercase, no accents, no punctuation, no generational suffix: 'A.J. Brown Jr.' -> 'aj brown'."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def candidate_urls(year, week, now=None):
    now = now or datetime.now(timezone.utc)
    months = [(now.year, now.month)]
    prev = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    months.append(prev)
    return [URL.format(year=y, month=m, week=week) for y, m in months if y == year or y == year + 1]


def _cells(row_html):
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def _position_before(html, table_start):
    window = re.sub(r"<[^>]+>", " ", html[max(0, table_start - 1500):table_start]).lower()
    best, best_at = None, -1
    for word, pos in POSITION_WORDS.items():
        at = window.rfind(word)
        if at > best_at:
            best, best_at = pos, at
    return best


def parse(html):
    """[{name, team, position, value, bye}] from a chart page, in page order."""
    players = []
    order = ["QB", "RB", "WR", "TE", "K", "D/ST"]
    for n, m in enumerate(re.finditer(r"<table[^>]*>(.*?)</table>", html, re.S)):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)
        if len(rows) < 2:
            continue
        header = [h.lower() for h in _cells(rows[0])]
        if "name" not in header or "value" not in header:
            continue
        idx = {h: i for i, h in enumerate(header)}
        position = _position_before(html, m.start()) or (order[n] if n < len(order) else "?")
        for row in rows[1:]:
            cells = _cells(row)
            if len(cells) <= idx["value"]:
                continue
            try:
                value = float(cells[idx["value"]])
            except ValueError:
                continue
            bye = None
            if "bye" in idx and len(cells) > idx["bye"]:
                try:
                    bye = int(cells[idx["bye"]])
                except ValueError:
                    bye = None
            players.append({"name": cells[idx["name"]], "team": cells[idx["team"]] if "team" in idx else "",
                            "position": position, "value": value, "bye": bye})
    return players


def _path(data_dir, week):
    return Path(data_dir) / "chart" / f"week-{int(week):02d}.json"


def fetch(year, week, now=None, session=None):
    """The parsed chart for a week, or None when no page is up."""
    http = session or requests
    for url in candidate_urls(year, week, now):
        try:
            r = http.get(url, timeout=30, headers=HEADERS)
        except requests.RequestException as e:
            logger.warning("chart fetch failed for %s: %s", url, e)
            continue
        if r.status_code != 200:
            continue
        players = parse(r.text)
        if players:
            return {"week": int(week), "source": url, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                    "players": players}
    return None


def load(data_dir, year, week, fetch_missing=True, now=None, session=None):
    """
    This week's chart from the cache or the web, else the newest earlier
    week on disk, else None. The result's "week" says which it is.
    """
    path = _path(data_dir, week)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if fetch_missing:
        chart = fetch(year, week, now=now, session=session)
        if chart:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(chart, indent=1), encoding="utf-8")
            return chart
    folder = Path(data_dir) / "chart"
    if folder.exists():
        older = sorted(folder.glob("week-*.json"), reverse=True)
        for p in older:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return None


class Chart:
    """Name lookups over a parsed chart. Missing players look up as None."""

    def __init__(self, data):
        self.data = data or {}
        self.week = self.data.get("week")
        self.by_name = {}
        for p in self.data.get("players", []):
            self.by_name.setdefault(normalize(p["name"]), p)

    def __bool__(self):
        return bool(self.by_name)

    def value(self, name, position=None):
        p = self.by_name.get(normalize(name))
        if p is None:
            return None
        if position and p["position"] not in ("?", position) and not (position == "D/ST" and p["position"] == "D/ST"):
            return None
        return p["value"]

    def bye(self, name):
        p = self.by_name.get(normalize(name))
        return p["bye"] if p else None
