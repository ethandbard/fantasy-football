"""
Tests for the dashboard's season arithmetic.

These cover the derivations the UI cannot check for you: a wrong streak or a
misattributed trophy looks perfectly plausible on screen. The fixture is a
four-team, four-week season with hand-worked expected values.
"""
import pandas as pd
import pytest

import gamedaybot.web.stats as stats


# Four teams, four weeks, every result chosen so the expected records,
# streaks and trophies can be read straight off the table:
#
#   wk1  Ravens 120.0 beat Bears  100.0     Colts  90.0 lost to Lions 110.0
#   wk2  Ravens 130.0 beat Colts   95.0     Bears  85.0 lost to Lions 140.0
#   wk3  Ravens 105.0 lost Lions  115.0     Bears 100.0 tied   Colts  100.0
#   wk4  Ravens  60.0 lost Bears   61.0     Colts 150.0 beat   Lions 149.0
FIXTURE = [
    # week, team_id, team_name, score, projected, opp_id, opp_name, is_home
    (1, 1, "Ravens", 120.0, 110.0, 2, "Bears", 1),
    (1, 2, "Bears", 100.0, 105.0, 1, "Ravens", 0),
    (1, 3, "Colts", 90.0, 100.0, 4, "Lions", 1),
    (1, 4, "Lions", 110.0, 100.0, 3, "Colts", 0),

    (2, 1, "Ravens", 130.0, 115.0, 3, "Colts", 1),
    (2, 3, "Colts", 95.0, 105.0, 1, "Ravens", 0),
    (2, 2, "Bears", 85.0, 100.0, 4, "Lions", 1),
    (2, 4, "Lions", 140.0, 120.0, 2, "Bears", 0),

    (3, 1, "Ravens", 105.0, 120.0, 4, "Lions", 1),
    (3, 4, "Lions", 115.0, 110.0, 1, "Ravens", 0),
    (3, 2, "Bears", 100.0, 95.0, 3, "Colts", 1),
    (3, 3, "Colts", 100.0, 95.0, 2, "Bears", 0),

    (4, 1, "Ravens", 60.0, 118.0, 2, "Bears", 1),
    (4, 2, "Bears", 61.0, 100.0, 1, "Ravens", 0),
    (4, 3, "Colts", 150.0, 100.0, 4, "Lions", 1),
    (4, 4, "Lions", 149.0, 130.0, 3, "Colts", 0),
]


@pytest.fixture
def scores():
    return pd.DataFrame(FIXTURE, columns=[
        "week", "team_id", "team_name", "score", "projected_score",
        "opponent_id", "opponent_name", "is_home",
    ]).assign(year=2025)


# Two teams, two regular weeks, then a two-week playoff round (matchup_period
# 3 spans weeks 3 and 4). Aces lose week 3 outright (50 vs 60) but win the
# round on the combined total (130 vs 100) -- the case that breaks anything
# still counting wins per week instead of per round.
PLAYOFF_FIXTURE = [
    # week, team_id, team_name, score, projected, matchup_period, matchup_score, opp_id, opp_name, is_home
    (1, 1, "Aces", 100.0, 95.0, 1, 100.0, 2, "Bees", 1),
    (1, 2, "Bees", 50.0, 95.0, 1, 50.0, 1, "Aces", 0),

    (2, 1, "Aces", 100.0, 95.0, 2, 100.0, 2, "Bees", 1),
    (2, 2, "Bees", 50.0, 95.0, 2, 50.0, 1, "Aces", 0),

    (3, 1, "Aces", 50.0, 95.0, 3, 130.0, 2, "Bees", 1),
    (3, 2, "Bees", 60.0, 95.0, 3, 100.0, 1, "Aces", 0),

    (4, 1, "Aces", 80.0, 95.0, 3, 130.0, 2, "Bees", 1),
    (4, 2, "Bees", 40.0, 95.0, 3, 100.0, 1, "Aces", 0),
]


@pytest.fixture
def playoff_scores():
    return pd.DataFrame(PLAYOFF_FIXTURE, columns=[
        "week", "team_id", "team_name", "score", "projected_score",
        "matchup_period", "matchup_score", "opponent_id", "opponent_name", "is_home",
    ]).assign(year=2025)


@pytest.fixture
def records(scores):
    return stats.derive_records(scores).set_index("team_name")


def test_game_log_pairs_every_team_with_its_opponent(scores):
    log = stats.game_log(scores)

    assert len(log) == len(scores)
    assert log["opponent_score"].notna().all()

    week1 = log[(log["week"] == 1) & (log["team_name"] == "Ravens")].iloc[0]
    assert week1["opponent_score"] == 100.0
    assert week1["margin"] == 20.0
    assert week1["result"] == "W"


def test_game_log_marks_a_tie_but_not_a_missing_opponent(scores):
    log = stats.game_log(scores)

    tie = log[(log["week"] == 3) & (log["team_name"] == "Bears")].iloc[0]
    assert tie["result"] == "T"

    # A row whose opponent was never collected has no result at all -- it must
    # not be counted as a tie just because the margin is not positive.
    orphan = scores[scores["week"] == 1].copy()
    orphan.loc[orphan["team_name"] == "Ravens", "opponent_id"] = 99
    missing = stats.game_log(orphan)
    assert (missing[missing["team_name"] == "Ravens"]["result"] == "").all()


def test_records_match_the_hand_worked_season(records):
    assert records.loc["Ravens", "record"] == "2-2"
    assert records.loc["Lions", "record"] == "3-1"
    # Bears and Colts each drew in week 3, so their record carries the tie.
    assert records.loc["Bears", "record"] == "1-2-1"
    assert records.loc["Colts", "record"] == "1-2-1"


def test_win_pct_counts_a_tie_as_half(records):
    assert records.loc["Lions", "win_pct"] == pytest.approx(0.75)
    assert records.loc["Bears", "win_pct"] == pytest.approx(0.375)


def test_points_are_whole_numbers_so_columns_align(scores, records):
    assert records.loc["Ravens", "points_for"] == 415       # 120+130+105+60
    assert records.loc["Ravens", "points_against"] == 371   # 100+95+115+61
    assert records.loc["Ravens", "diff"] == 44

    # Integer dtype, not floats that happen to be round: a float column
    # renders 371.0 next to 415, which is the ragged decimal problem the
    # rounding was introduced to fix.
    table = stats.derive_records(scores)
    for column in ("points_for", "points_against", "diff"):
        assert pd.api.types.is_integer_dtype(table[column])


def test_streak_reads_the_trailing_run_only(records):
    assert records.loc["Ravens", "streak"] == "L2"   # W W L L
    assert records.loc["Lions", "streak"] == "L1"    # W W W L
    assert records.loc["Colts", "streak"] == "W1"    # L L T W


def test_form_keeps_chronological_order(records):
    assert records.loc["Ravens", "form"] == "W W L L"
    assert records.loc["Bears", "form"] == "L L T W"


def test_form_is_capped_at_the_requested_length(scores):
    short = stats.derive_records(scores, last_n=2).set_index("team_name")
    assert short.loc["Ravens", "form"] == "L L"


def test_records_are_ordered_by_wins_then_points(scores):
    order = stats.derive_records(scores)["team_name"].tolist()
    assert order[0] == "Lions"    # 3 wins
    assert order[1] == "Ravens"   # 2 wins
    # Bears and Colts both sit at one win, so points for breaks the tie.
    assert order[2:] == ["Colts", "Bears"]


def test_regular_season_weeks_comes_from_games_played(scores):
    standings = pd.DataFrame([{"wins": 2, "losses": 1, "ties": 0}])
    assert stats.regular_season_weeks(standings, scores) == 3

    # No standings collected yet: every collected week counts.
    assert stats.regular_season_weeks(pd.DataFrame(), scores) == 4


def test_rank_by_week_tracks_the_table_over_time(scores):
    ranked = stats.rank_by_week(scores)

    week1 = ranked[ranked["week"] == 1].set_index("team_name")["rank"]
    # Everyone is 1-0 or 0-1 after week 1, so points for separates the winners.
    assert week1["Ravens"] == 1
    assert week1["Lions"] == 2

    final = ranked[ranked["week"] == 4].set_index("team_name")["rank"]
    assert final["Lions"] == 1
    assert final["Ravens"] == 2
    assert sorted(final.tolist()) == [1, 2, 3, 4]


def test_head_to_head_reads_row_against_column(scores):
    records, margins = stats.head_to_head(scores)

    assert records.at["Ravens", "Bears"] == "1-1"
    assert records.at["Lions", "Ravens"] == "1-0"
    # Nobody plays themselves.
    assert records.at["Ravens", "Ravens"] == ""
    assert pd.isna(margins.at["Ravens", "Ravens"])
    # Ravens won by 20 then lost by 1: mean margin +9.5.
    assert margins.at["Ravens", "Bears"] == pytest.approx(9.5)


def test_consistency_ranks_steadiest_first(scores):
    con = stats.consistency(scores).set_index("team_name")

    assert con.loc["Ravens", "ceiling"] == 130.0
    assert con.loc["Ravens", "floor"] == 60.0

    # Bears (std 18.4) edge out Lions (18.9); Ravens swing hardest at 31.0.
    ordering = stats.consistency(scores)["team_name"].tolist()
    assert ordering[0] == "Bears"
    assert ordering[-1] == "Ravens"


def test_consistency_cv_normalises_for_scoring_level(scores):
    con = stats.consistency(scores).set_index("team_name")

    # Bears and Lions have nearly the same absolute spread, but Bears score
    # far less, so the same swing is a much bigger share of their average --
    # which is the whole reason CV is in the table alongside Std.
    assert con.loc["Bears", "std"] == pytest.approx(con.loc["Lions", "std"], abs=1.0)
    assert con.loc["Bears", "cv"] > con.loc["Lions", "cv"]


def test_vs_projection_ignores_weeks_without_one(scores):
    blind = scores.copy()
    blind.loc[blind["week"] == 1, "projected_score"] = None

    full = stats.vs_projection(scores).set_index("team_name")
    partial = stats.vs_projection(blind).set_index("team_name")

    assert full.loc["Ravens", "weeks"] == 4
    assert partial.loc["Ravens", "weeks"] == 3
    # Ravens: +10, +15, -15, -58 over four weeks -> -12.0 average.
    assert full.loc["Ravens", "vs_proj"] == pytest.approx(-12.0)


def test_trophies_carry_units_and_a_scoreline(scores):
    awards = {a["title"]: a for a in stats.trophies(scores)}

    assert awards["Highest Score"]["team"] == "Colts"
    assert awards["Highest Score"]["detail"] == "150.0 pts"
    assert awards["Lowest Score"]["team"] == "Ravens"

    # The week-3 tie is the closest a matchup can be, and the detail names
    # the unit and the scoreline rather than leaving a bare number.
    closest = awards["Closest Matchup"]
    assert "0.0 pt margin" in closest["detail"]
    assert "Bears 100.0 – 100.0 Colts" in closest["detail"]

    blowout = awards["Biggest Blowout"]
    assert "55.0 pt margin" in blowout["detail"]   # Lions 140 - Bears 85


def test_trophies_find_the_unlucky_and_lucky_results(scores):
    awards = {a["title"]: a for a in stats.trophies(scores)}

    # Highest score that still lost: Lions put up 149 and lost by one.
    assert awards["Highest-Scoring Loss"]["team"] == "Lions"
    assert awards["Highest-Scoring Loss"]["week"] == 4
    assert "still lost" in awards["Highest-Scoring Loss"]["detail"]

    # Lowest score that still won: Bears 61 over Ravens 60 in week 4.
    assert awards["Lowest-Scoring Win"]["team"] == "Bears"
    assert awards["Lowest-Scoring Win"]["week"] == 4


def test_longest_streak_trophies_scan_the_whole_range_not_just_the_tail(scores):
    awards = {a["title"]: a for a in stats.trophies(scores)}

    # Lions went W W W L: the trailing streak is a single loss, but the
    # longest run anywhere in the range is the three wins that opened it.
    win_streak = awards["Longest Win Streak"]
    assert win_streak["team"] == "Lions"
    assert win_streak["week"] is None
    assert "3 straight wins" in win_streak["detail"]
    assert "weeks 1–3" in win_streak["detail"]

    # Ravens and Bears both have a two-loss run (weeks 3-4 and 1-2), so this
    # only pins down the length and span, not which team wins the tie.
    loss_streak = awards["Longest Losing Streak"]
    assert loss_streak["team"] in ("Ravens", "Bears")
    assert loss_streak["week"] is None
    assert "2 straight losses" in loss_streak["detail"]


def test_matchup_trophies_expose_a_single_team_to_focus(scores):
    awards = {a["title"]: a for a in stats.trophies(scores)}

    blowout = awards["Biggest Blowout"]
    assert " vs " in blowout["team"]
    # `focus` has to be a real team name -- "A vs B" filters nothing.
    assert blowout["focus"] in set(scores["team_name"])


def test_every_trophy_is_clickable_and_labelled(scores):
    for award in stats.trophies(scores):
        assert award["focus"] in set(scores["team_name"])
        assert award["detail"]
        assert award["icon"]
        assert award["week"] is None or 1 <= award["week"] <= 4


def test_matchup_log_collapses_a_two_week_round_into_one_game(playoff_scores):
    log = stats.matchup_log(playoff_scores)

    aces = log[log["team_name"] == "Aces"]
    assert len(aces) == 3  # two regular weeks + one playoff round, not four

    round3 = aces[aces["matchup_period"] == 3].iloc[0]
    assert round3["score"] == 130.0
    assert round3["opponent_score"] == 100.0
    assert round3["result"] == "W"


def test_derive_records_counts_a_playoff_round_as_one_game_not_two(playoff_scores):
    records = stats.derive_records(playoff_scores).set_index("team_name")

    assert records.loc["Aces", "record"] == "3-0"
    assert records.loc["Bees", "record"] == "0-3"
    # Points still add up over every real week played, since the round total
    # already equals the sum of its two weeks.
    assert records.loc["Aces", "points_for"] == 330  # 100 + 100 + 50 + 80


def test_rank_by_week_only_credits_a_win_at_the_end_of_a_round(playoff_scores):
    ranked = stats.rank_by_week(playoff_scores)
    aces = ranked[ranked["team_name"] == "Aces"].set_index("week")

    # Aces are behind in week 3 of the round (50 vs Bees' 60), so the round
    # win must not land until the round finishes at week 4.
    assert aces.loc[3, "cum_wins"] == 2.0
    assert aces.loc[4, "cum_wins"] == 3.0


def test_trophies_credit_wins_and_losses_by_round(playoff_scores):
    awards = {a["title"]: a for a in stats.trophies(playoff_scores)}

    # Bees' week-3 score of 60 beat Aces' week-3 score outright, but the
    # round Bees actually lost -- so it reads as their highest-scoring loss,
    # not a win.
    assert awards["Highest-Scoring Loss"]["team"] == "Bees"
    assert awards["Highest-Scoring Loss"]["week"] == 3

    # Aces' week-3 score of 50 was their worst week, but the round it
    # belongs to was won on the combined total.
    assert awards["Lowest-Scoring Win"]["team"] == "Aces"
    assert awards["Lowest-Scoring Win"]["week"] == 3


def test_empty_season_returns_empty_shapes_not_errors():
    empty = pd.DataFrame(columns=[
        "week", "team_id", "team_name", "score", "projected_score",
        "opponent_id", "opponent_name", "is_home", "year",
    ])

    assert stats.derive_records(empty).empty
    assert stats.consistency(empty).empty
    assert stats.vs_projection(empty).empty
    assert stats.trophies(empty) == []
    assert stats.regular_season_weeks(pd.DataFrame(), empty) == 0

    records, margins = stats.head_to_head(empty)
    assert records.empty and margins.empty


# A second season, reusing week numbers 1-4 and the same team_ids as `scores`,
# so any all-time derivation that forgets to key on year will silently merge
# 2024's week 3 into 2025's week 3.
FIXTURE_2024 = [
    (1, 1, "Ravens", 200.0, 150.0, 2, "Bears", 1),
    (1, 2, "Bears", 40.0, 150.0, 1, "Ravens", 0),

    (2, 1, "Ravens", 85.0, 100.0, 3, "Colts", 1),
    (2, 3, "Colts", 88.0, 95.0, 1, "Ravens", 0),

    (3, 1, "Ravens", 70.0, 90.0, 4, "Lions", 1),
    (3, 4, "Lions", 71.0, 90.0, 1, "Ravens", 0),

    (4, 1, "Ravens", 95.0, 90.0, 2, "Bears", 1),
    (4, 2, "Bears", 94.0, 90.0, 1, "Ravens", 0),
]


@pytest.fixture
def multi_season_scores(scores):
    prior = pd.DataFrame(FIXTURE_2024, columns=[
        "week", "team_id", "team_name", "score", "projected_score",
        "opponent_id", "opponent_name", "is_home",
    ]).assign(year=2024)
    return pd.concat([scores, prior], ignore_index=True)


def test_all_time_trophies_keys_on_year_not_just_week(multi_season_scores):
    awards = {a["title"]: a for a in stats.all_time_trophies(multi_season_scores)}

    # 200.0 (Ravens, 2024 week 1) beats every 2025 score.
    highest = awards["Highest Single-Week Score"]
    assert highest["team"] == "Ravens"
    assert highest["year"] == 2024
    assert highest["week"] == 1
    assert "200.0" in highest["detail"]

    # 40.0 (Bears, 2024 week 1) is the lowest across both seasons.
    lowest = awards["Lowest Single-Week Score"]
    assert lowest["team"] == "Bears"
    assert lowest["year"] == 2024


def test_all_time_trophies_scopes_season_records_and_points(multi_season_scores):
    awards = {a["title"]: a for a in stats.all_time_trophies(multi_season_scores)}

    # Colts and Lions each only appear in one 2024 fixture row and go 1-0,
    # edging out every full-season record including Lions' real 3-1 in 2025 --
    # the point of this assertion is that the search runs season by season
    # rather than crowning a single global winner some other way.
    best = awards["Best Season Record"]
    assert best["team"] in ("Colts", "Lions")
    assert best["year"] == 2024

    # Most points in a season: Lions' 2025 total (110+140+115+149=514) beats
    # every other team-season, including Ravens' inflated 2024 total.
    top_points = awards["Most Points in a Season"]
    assert top_points["team"] == "Lions"
    assert top_points["year"] == 2025


def test_all_time_trophies_streaks_do_not_bridge_seasons(multi_season_scores):
    awards = {a["title"]: a for a in stats.all_time_trophies(multi_season_scores)}

    # Ravens' 2024 form is W L L W (no run over 1); their 2025 form is
    # W W L L. If year offsets leaked into one continuous timeline, the
    # trailing 2024 win and the leading 2025 wins could misread as one run.
    win_streak = awards["Longest Win Streak"]
    assert win_streak["detail"].count("straight wins") == 1
    assert win_streak["team"] in ("Ravens", "Lions")


def test_all_time_trophies_finds_the_dominant_rivalry(multi_season_scores):
    awards = {a["title"]: a for a in stats.all_time_trophies(multi_season_scores)}

    # Lions beat Ravens in week 3 of both seasons -- a clean 2-0, and a
    # better win pct than Ravens' 3-1 against Bears over the same two years.
    rivalry = awards["Most Dominant Rivalry"]
    assert rivalry["team"] == "Lions vs Ravens"
    assert rivalry["detail"].startswith("2-0")


def test_all_time_trophies_empty_season_returns_empty_list():
    empty = pd.DataFrame(columns=[
        "week", "team_id", "team_name", "score", "projected_score",
        "opponent_id", "opponent_name", "is_home", "year",
    ])
    assert stats.all_time_trophies(empty) == []
