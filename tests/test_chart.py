"""
The trade value chart: parsing FantasyPros' page, the URL candidates, the
cache with its fallback to an older week, and name lookups.
"""
import json
from datetime import datetime, timezone

from agent import chart

PAGE = """
<html><body>
<p>Quarterback Fantasy Football Trade Value Chart</p>
<table><tbody>
<tr><td>Name</td><td>Team</td><td>Value</td><td>Change</td><td>2QB Value</td><td>Bye</td><td>Week 7</td></tr>
<tr><td>Josh Allen</td><td>BUF</td><td>29.1</td><td>0</td><td>69.5</td><td>7</td><td>bye</td></tr>
<tr><td>Lamar Jackson</td><td>BAL</td><td>28.6</td><td>0</td><td>56.2</td><td>13</td><td>CIN</td></tr>
</tbody></table>
<img src="x.jpg"/> Running Back Fantasy Football Trade Value Chart
<table><tbody>
<tr><td>Name</td><td>Team</td><td>Value</td><td>Change</td><td>Bye</td><td>Week 7</td></tr>
<tr><td>Jahmyr Gibbs</td><td>DET</td><td>75.1</td><td>0</td><td>6</td><td>GB</td></tr>
<tr><td>A.J. Brown Jr.</td><td>NE</td><td>31.4</td><td>-1</td><td>11</td><td>CHI</td></tr>
</tbody></table>
<table><tbody><tr><td>Not</td><td>a chart</td></tr><tr><td>x</td><td>y</td></tr></tbody></table>
</body></html>
"""


def test_parse_reads_each_position_table_with_values_and_byes():
    players = chart.parse(PAGE)
    assert [p["name"] for p in players] == ["Josh Allen", "Lamar Jackson", "Jahmyr Gibbs", "A.J. Brown Jr."]
    assert players[0]["position"] == "QB" and players[2]["position"] == "RB"
    assert players[2]["value"] == 75.1 and players[2]["bye"] == 6 and players[2]["team"] == "DET"


def test_candidate_urls_try_this_month_then_last():
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    urls = chart.candidate_urls(2026, 9, now)
    assert urls[0].endswith("/2026/11/fantasy-football-trade-value-chart-week-9-2026/")
    assert urls[1].endswith("/2026/10/fantasy-football-trade-value-chart-week-9-2026/")
    assert chart.candidate_urls(2026, 18, datetime(2027, 1, 5, tzinfo=timezone.utc))[1].endswith("/2026/12/fantasy-football-trade-value-chart-week-18-2026/")


class _Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, timeout=0, headers=None):
        self.calls.append(url)
        body = self.pages.get(url)
        class R:
            status_code = 200 if body else 404
            text = body or ""
        return R()


def test_load_caches_the_fetch_and_falls_back_to_the_newest_older_week(tmp_path):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    url = chart.candidate_urls(2026, 8, now)[0]
    session = _Session({url: PAGE})
    first = chart.load(tmp_path, 2026, 8, now=now, session=session)
    assert first["week"] == 8 and len(first["players"]) == 4 and (tmp_path / "chart" / "week-08.json").exists()
    again = chart.load(tmp_path, 2026, 8, now=now, session=_Session({}))
    assert again["players"] == first["players"]
    # Week 9's page is not up: the week 8 chart comes back and says so.
    older = chart.load(tmp_path, 2026, 9, now=now, session=_Session({}))
    assert older["week"] == 8
    assert chart.load(tmp_path / "empty", 2026, 9, now=now, session=_Session({})) is None


def test_chart_lookup_normalizes_names_and_knows_byes():
    ch = chart.Chart({"week": 7, "players": chart.parse(PAGE)})
    assert ch.value("AJ Brown") == 31.4 and ch.value("A.J. Brown") == 31.4
    assert ch.value("Jahmyr Gibbs", "RB") == 75.1 and ch.value("Jahmyr Gibbs", "WR") is None
    assert ch.value("Nobody") is None and ch.bye("Josh Allen") == 7
    assert bool(ch) and not bool(chart.Chart(None))
    assert chart.normalize("Kenneth Walker III") == "kenneth walker" and chart.normalize("Ja'Marr Chase") == "jamarr chase"
