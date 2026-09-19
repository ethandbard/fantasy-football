"""
Tests for following a manager across seasons.

The fixture is the two failures the real league has: a manager who renames
the team and moves to a different team_id (Felix: "Old Name" id 5, then "New
Name" id 4), and a team_id handed to someone else (id 4 was Mona's in 2024).
Joining history on either team_id or team_name gets both wrong.
"""
import pandas as pd
import pytest

import gamedaybot.espn.managers as managers
import gamedaybot.web.stats as stats

TEAMS = [
    # year, team_id, team_name, owner, owner_id
    (2024, 1, "Steady", "ESPNfan1", "{G-JO}"),
    (2024, 4, "Dragons", "mona", "{G-MONA}"),
    (2024, 5, "Old Name", "ESPNfan5", "{G-FELIX}"),
    (2025, 1, "Steady", "ESPNfan1", "{G-JO}"),
    (2025, 4, "New Name", "ESPNfan5", "{G-FELIX}"),
    (2025, 9, "Rookies", "newbie", "{G-NEW}"),
]


def _game(year, week, a, a_name, a_score, b, b_name, b_score):
    base = {"year": year, "week": week, "projected_score": 100.0,
            "matchup_period": week}
    return [
        {**base, "team_id": a, "team_name": a_name, "score": a_score, "matchup_score": a_score,
         "opponent_id": b, "opponent_name": b_name, "is_home": 1},
        {**base, "team_id": b, "team_name": b_name, "score": b_score, "matchup_score": b_score,
         "opponent_id": a, "opponent_name": a_name, "is_home": 0},
    ]


# Felix beats Steady three straight across the rename; Mona (the other id 4)
# lost to Steady once in 2024. Week 16 of 2024 is past the 14-week season.
GAMES = (
    _game(2024, 2, 1, "Steady", 90.0, 5, "Old Name", 80.0)       # Steady wins
    + _game(2024, 3, 1, "Steady", 120.0, 4, "Dragons", 70.0)     # Steady beats Mona
    + _game(2024, 9, 1, "Steady", 95.0, 5, "Old Name", 100.0)    # Felix wins
    + _game(2024, 16, 5, "Old Name", 150.0, 1, "Steady", 90.0)   # Felix wins, postseason
    + _game(2025, 3, 4, "New Name", 111.0, 1, "Steady", 110.0)   # Felix wins as id 4
)


@pytest.fixture
def teams():
    return pd.DataFrame(TEAMS, columns=["year", "team_id", "team_name", "owner", "owner_id"])


@pytest.fixture
def scores():
    return pd.DataFrame(GAMES)


def test_manager_key_prefers_guid_then_casefolded_owner():
    assert stats.manager_key({"year": 2024, "team_id": 1, "owner_id": "{G}", "owner": "X"}) == "{G}"
    assert stats.manager_key({"year": 2024, "team_id": 1, "owner_id": None, "owner": "ESPNfan9"}) == "espnfan9"
    assert stats.manager_key({"year": 2024, "team_id": 1, "owner_id": float("nan"), "owner": None}) == "2024:1"


def test_managers_follow_the_person_not_the_id_or_name(scores, teams):
    index = stats.managers(scores, teams, {"{G-FELIX}": "Felix"}, as_of_year=2025)
    felix = index[index["manager"] == "Felix"]
    assert sorted(zip(felix["year"], felix["team_id"])) == [(2024, 5), (2025, 4)]
    assert set(felix["label"]) == {"New Name"}
    # The same team_id in 2024 is someone else, labelled by her last team.
    mona = index[(index["year"] == 2024) & (index["team_id"] == 4)].iloc[0]
    assert mona["mid"] != felix["mid"].iloc[0]
    assert mona["label"] == "Dragons"


def test_labels_follow_the_season_on_screen(scores, teams):
    index = stats.managers(scores, teams, as_of_year=2024)
    assert set(index[index["key"] == "{G-FELIX}"]["label"]) == {"Old Name"}


def test_all_time_head_to_head_joins_renamed_seasons(scores, teams):
    records, _ = stats.head_to_head_all_time(scores, teams, as_of_year=2025)
    assert records.at["New Name", "Steady"] == "3-1"
    assert records.at["Steady", "Dragons"] == "1-0"
    assert "Old Name" not in records.index
    # Name-keyed, the old behaviour, splits the same rivalry in two.
    by_name, _ = stats.head_to_head_all_time(scores)
    assert by_name.at["New Name", "Steady"] == "1-0"


def test_rivalry_counts_a_streak_across_seasons(scores, teams):
    r = stats.rivalry(scores, teams, 2025, 4, 1, {"{G-FELIX}": "Felix"}, reg_weeks={2024: 14, 2025: 14})
    assert (r["wins_a"], r["wins_b"]) == (3, 1)
    assert r["a"]["manager"] == "Felix" and r["a"]["first_year"] == 2024
    assert r["streak"] == {"side": "a", "length": 3, "since_year": 2024, "since_week": 9}
    assert [m["postseason"] for m in r["meetings"]] == [False, False, True, False]
    assert r["meetings"][0]["a_name"] == "Old Name"
    assert (r["postseason_a"], r["postseason_b"]) == (1, 0)
    assert r["biggest"]["margin"] == 60.0 and r["closest"]["margin"] == 1.0

    notes = stats.rivalry_notes(r)
    assert notes[0] == "New Name leads the all-time series 3-1 over 2 seasons."
    assert "won the last 3 meetings, a run going back to 2024 week 9" in notes[1]
    assert notes[2] == "Last met in 2025 week 3: New Name won 111.0–110.0."
    assert any("Biggest margin: New Name (then Old Name) by 60.0 in 2024 week 16" in n for n in notes)


def test_rivalry_before_leaves_the_week_itself_out(scores, teams):
    r = stats.rivalry(scores, teams, 2025, 4, 1, before=(2025, 3))
    assert len(r["meetings"]) == 3
    assert r["streak"]["length"] == 2


def test_never_beaten_replaces_the_streak_line(scores, teams):
    sweep = pd.DataFrame(_game(2024, 1, 1, "Steady", 100.0, 5, "Old Name", 90.0)
                         + _game(2024, 8, 1, "Steady", 100.0, 5, "Old Name", 90.0)
                         + _game(2025, 2, 1, "Steady", 100.0, 4, "New Name", 90.0))
    notes = stats.rivalry_notes(stats.rivalry(sweep, teams, 2025, 1, 4))
    assert "New Name has never beaten Steady in 3 tries." in notes


def test_first_meeting_names_the_newcomer(scores, teams):
    r = stats.rivalry(scores, teams, 2025, 1, 9)
    assert r["meetings"] == []
    assert stats.rivalry_notes(r) == ["First meeting: Rookies is new to the league this season."]


def test_a_team_id_that_changed_hands_is_not_a_meeting(scores, teams):
    # 2025's id 4 (Felix) against 2025's id 9: Mona's 2024 games at id 4 must not leak in.
    assert stats.rivalry(scores, teams, 2025, 4, 9)["meetings"] == []


def test_parse_names_reads_years_and_the_last_dash():
    text = "2024\nRun - DMC - Dan\n\n2025\nYikes (3) - Ed\nnot a line\n"
    assert managers.parse_names(text) == [(2024, "Run - DMC", "Dan"), (2025, "Yikes (3)", "Ed")]


def test_resolve_matches_loosely_and_pairs_the_one_left_over(teams):
    entries = [(2024, "steady", "Jo"), (2024, "DRAGONS!", "Mona"), (2024, "Renamed Since", "Felix")]
    names, problems = managers.resolve(entries, teams.to_dict("records"))
    assert names == {"{G-JO}": "Jo", "{G-MONA}": "Mona", "{G-FELIX}": "Felix"}
    assert len(problems) == 1 and "Old Name" in problems[0]


def test_resolve_does_not_guess_between_two_leftovers(teams):
    names, problems = managers.resolve([(2024, "Nope", "A"), (2024, "Nada", "B")], teams.to_dict("records"))
    assert names == {} and len(problems) == 2
