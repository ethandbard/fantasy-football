"""
ESPN player-pool fetch and parse.

The weekly collector stores team scores. This module pulls the same player
list the ESPN draft board uses -- ranks, ADP, bye, projected FPTS in *this
league's* scoring, and the counting-stat projections behind PC/PA/PY/etc.

Parsing is pure so tests can feed a fixture without standing up a League.
"""
import json
import logging

from espn_api.football.constant import PLAYER_STATS_MAP, POSITION_MAP, PRO_TEAM_MAP

logger = logging.getLogger(__name__)

# QB/RB/WR/TE/K/D/ST -- the slots a standard draft board shows. IDP leagues
# add their own via settings.position_slot_counts.
_DEFAULT_SLOTS = [0, 2, 4, 6, 16, 17]

# Receptions. 53 is "each reception" (PPR), 41 is the receptions counting
# stat. Either scoring at half a point or more is treated as a PPR board,
# because ESPN only publishes STANDARD and PPR draft-rank lists.
_RECEPTION_STAT_IDS = {41, 53}

PAGE_SIZE = 250


def scoring_rank_type(league):
    """Return ESPN's draft-rank list for this league: 'PPR' or 'STANDARD'."""
    for item in getattr(league.settings, "scoring_format", None) or []:
        if item.get("id") in _RECEPTION_STAT_IDS and (item.get("points") or 0) >= 0.5:
            return "PPR"
    return "STANDARD"


def bye_weeks_from_schedule(schedule):
    """
    Map NFL proTeamId -> bye week.

    `schedule` is league._get_all_pro_schedule(): {team_id: {scoringPeriod: [games]}}.
    An 18-week NFL season has 17 games, so the missing week in 1..18 is the bye.
    Returns {} when the schedule has not been published yet.
    """
    byes = {}
    regular = set(range(1, 19))
    for team_id, games_by_period in (schedule or {}).items():
        if not team_id:
            continue
        periods = set()
        for key, games in (games_by_period or {}).items():
            if not games:
                continue
            try:
                periods.add(int(key))
            except (TypeError, ValueError):
                continue
        missing = sorted(regular - periods)
        if len(missing) == 1:
            byes[int(team_id)] = missing[0]
    return byes


def slot_filter(league):
    """Lineup slot ids this league actually uses, falling back to the standard six."""
    counts = getattr(league.settings, "position_slot_counts", None) or {}
    slots = []
    for pos, count in counts.items():
        if not count:
            continue
        slot_id = POSITION_MAP.get(pos)
        if isinstance(slot_id, int) and slot_id not in (20, 21, 25):
            slots.append(slot_id)
    return slots or list(_DEFAULT_SLOTS)


def fetch_player_pool(league):
    """
    Return the raw ESPN player-pool list for this league.

    Uses view=kona_player_info so appliedTotal is this league's scoring, not
    a generic default. Pages until a short response, because ESPN silently
    caps a single request well below the full pool.
    """
    year = league.year
    rank_type = scoring_rank_type(league)
    slots = slot_filter(league)
    players = []
    offset = 0

    while True:
        filters = {
            "players": {
                "limit": PAGE_SIZE,
                "offset": offset,
                "filterSlotIds": {"value": slots},
                "sortDraftRanks": {
                    "sortPriority": 1,
                    "sortAsc": True,
                    "value": rank_type,
                },
                "sortPercOwned": {"sortPriority": 2, "sortAsc": False},
                "filterStatsForTopScoringPeriodIds": {
                    "value": 2,
                    "additionalValue": [
                        f"00{year}",
                        f"10{year}",
                        f"00{year - 1}",
                    ],
                },
            }
        }
        params = {"view": "kona_player_info", "scoringPeriodId": 0}
        headers = {
            "x-fantasy-filter": json.dumps(filters),
            "x-fantasy-source": "kona",
        }
        data = league.espn_request.league_get(params=params, headers=headers)
        page = data.get("players") or []
        players.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if offset > 4000:
            logger.warning("Player pool pagination hit 4000; stopping")
            break

    return players, rank_type


def parse_player_entry(entry, year, bye_by_team, rank_type="STANDARD"):
    """
    Flatten one ESPN player-pool entry into a players-table row.

    `bye_by_team` is proTeamId -> bye week. Missing ranks, stats, or ownership
    become None rather than ESPN's -1 sentinel.
    """
    player = entry.get("player") or entry
    player_id = player.get("id") or entry.get("id")
    if player_id is None:
        return None

    pro_team_id = player.get("proTeamId") or 0
    projected, last_year = _season_stats(player.get("stats") or [], year)

    ownership = player.get("ownership") or {}
    ranks = player.get("draftRanksByRankType") or {}
    rank_info = ranks.get(rank_type) or ranks.get("STANDARD") or ranks.get("PPR") or {}

    percent_owned = ownership.get("percentOwned")
    if percent_owned is not None and percent_owned < 0:
        percent_owned = None
    percent_started = ownership.get("percentStarted")
    if percent_started is not None and percent_started < 0:
        percent_started = None

    adp = ownership.get("averageDraftPosition")
    if adp is not None and adp < 0:
        adp = None

    injury = player.get("injuryStatus") or "ACTIVE"
    if injury in ("NORMAL", ""):
        injury = "ACTIVE"

    return {
        "year": year,
        "player_id": int(player_id),
        "name": player.get("fullName") or "",
        "position": _main_position(player),
        "pro_team": PRO_TEAM_MAP.get(pro_team_id, "FA"),
        "bye_week": bye_by_team.get(int(pro_team_id)) if pro_team_id else None,
        "injury_status": injury,
        "injured": 1 if player.get("injured") else 0,
        "percent_owned": _round(percent_owned, 1),
        "percent_started": _round(percent_started, 1),
        "adp": _round(adp, 1),
        "auction_value": rank_info.get("auctionValue"),
        "draft_rank": rank_info.get("rank") or player.get("positionalRanking") or None,
        "pos_rank": player.get("positionalRanking") or None,
        "projected_points": _round(projected.get("points"), 1),
        "projected_avg": _round(projected.get("avg"), 1),
        "last_year_points": _round(last_year.get("points"), 1),
        "projected_stats": json.dumps(projected.get("breakdown") or {}, sort_keys=True),
        "last_year_stats": json.dumps(last_year.get("breakdown") or {}, sort_keys=True),
        "on_team_id": entry.get("onTeamId") or player.get("onTeamId") or 0,
    }


def parse_player_pool(entries, year, bye_by_team, rank_type="STANDARD"):
    """Parse a list of ESPN entries, dropping any that have no player id."""
    rows = []
    seen = set()
    for entry in entries:
        row = parse_player_entry(entry, year, bye_by_team, rank_type)
        if row is None or row["player_id"] in seen:
            continue
        seen.add(row["player_id"])
        rows.append(row)
    return rows


# defaultPositionId uses a different numbering than lineup slot ids.
# POSITION_MAP[1] is TQB, which is the slot, not the player's position.
_DEFAULT_POSITION = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}


def _main_position(player):
    """First non-flex eligible slot, matching espn_api.football.Player."""
    for pos in player.get("eligibleSlots") or []:
        mapped = POSITION_MAP.get(pos)
        if mapped is None:
            continue
        if (pos != 25 and "/" not in mapped) or "/" in (player.get("fullName") or ""):
            return mapped
    return _DEFAULT_POSITION.get(player.get("defaultPositionId"), "??")


def _season_stats(stats_list, year):
    """
    Split ESPN's stats array into this year's projection and last year's actual.

    statSourceId 0 = actual, 1 = projected. scoringPeriodId 0 = season total.
    statSplitTypeId 2 is a trailing-window split espn_api already skips.
    """
    projected = {}
    last_year = {}
    for stats in stats_list:
        if stats.get("statSplitTypeId") == 2:
            continue
        if stats.get("scoringPeriodId") not in (0, None):
            continue
        season = stats.get("seasonId")
        source = stats.get("statSourceId")
        bucket = {
            "points": stats.get("appliedTotal"),
            "avg": stats.get("appliedAverage"),
            "breakdown": _named_breakdown(stats.get("stats") or {}),
        }
        if season == year and source == 1:
            projected = bucket
        elif season == year - 1 and source == 0:
            last_year = bucket
        elif season == year and source == 0 and not last_year:
            # Preseason has no last-year block for rookies; in-season this
            # branch is the actual season-to-date, which the board can show
            # in last_year_points until a dedicated actuals column exists.
            pass
    return projected, last_year


# PLAYER_STATS_MAP reuses passingYards/rushingYards/receivingYards for both
# the season total and the per-game stat. Later keys would overwrite the
# season total (Josh Allen's PY becoming ~232 instead of ~4,000). These ids
# get their own names so the board columns read the totals.
_STAT_ALIASES = {
    22: "passingYardsPerGame",
    40: "rushingYardsPerGame",
    53: "receivingReceptions",
    61: "receivingYardsPerGame",
}


def _named_breakdown(raw):
    named = {}
    for key, value in raw.items():
        try:
            stat_id = int(key)
        except (TypeError, ValueError):
            continue
        name = _STAT_ALIASES.get(stat_id) or PLAYER_STATS_MAP.get(stat_id)
        if name is None or name in named:
            continue
        named[name] = value
    return named


def _round(value, digits):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
