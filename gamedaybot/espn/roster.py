"""
Roster reads with the detail the write side needs: numeric slot ids, lock
flags, eligibility, this week's projection, and kickoff times.

espn_api's Player object names the slot but drops the id and the lock flags,
and a lineup write needs both. This module reads the raw mRoster view and
the pro schedule directly and keeps everything else in plain dataclasses so
the agent tools can serialize them without knowing espn_api.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from espn_api.football.constant import POSITION_MAP, PRO_TEAM_MAP

BENCH_SLOT = 20
IR_SLOT = 21
FLEX_SLOT = 23

# Slots a lineup can start players in, in the order ESPN lists them.
STARTING_SLOT_ORDER = [0, 2, 4, 6, 23, 16, 17]

# ESPN's defaultPositionId is a different numbering from lineup slots.
POSITION_BY_DEFAULT_ID = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

# Slot name -> slot id, built from the int keys so flex names resolve too.
SLOT_ID_BY_NAME = {name: slot_id for slot_id, name in POSITION_MAP.items() if isinstance(slot_id, int)}


def slot_name(slot_id):
    return POSITION_MAP.get(slot_id, str(slot_id))


def player_position(player):
    pos = POSITION_BY_DEFAULT_ID.get(player.get("defaultPositionId"))
    if pos:
        return pos
    for slot_id in player.get("eligibleSlots") or []:
        name = POSITION_MAP.get(slot_id, "")
        if name and "/" not in name and slot_id not in (BENCH_SLOT, IR_SLOT, 25):
            return name
    return ""


@dataclass
class RosterEntry:
    player_id: int
    name: str
    position: str
    pro_team: str
    slot_id: int
    slot: str
    eligible_slot_ids: List[int]
    eligible_slots: List[str]
    injury_status: str
    injured: bool
    lineup_locked: bool
    roster_locked: bool
    trade_locked: bool
    droppable: bool
    projected: Optional[float] = None
    actual: Optional[float] = None
    season_points: Optional[float] = None
    season_avg: Optional[float] = None
    percent_owned: Optional[float] = None
    opponent: Optional[str] = None
    kickoff: Optional[str] = None
    bye: bool = False

    @property
    def is_starter(self):
        return self.slot_id not in (BENCH_SLOT, IR_SLOT)

    def to_dict(self):
        data = asdict(self)
        data["is_starter"] = self.is_starter
        return data


def kickoffs_for_period(pro_schedule, scoring_period):
    """
    {proTeamId: (opponentProTeamId, kickoff datetime in UTC)} for one week.

    pro_schedule is league._get_all_pro_schedule(): {proTeamId: {"period": [game, ...]}}.
    Teams with no game that week are absent, which is how a bye shows up.
    """
    out = {}
    key = str(scoring_period)
    for team_id, by_period in (pro_schedule or {}).items():
        games = by_period.get(key) or []
        if not games:
            continue
        game = games[0]
        opponent = game["homeProTeamId"] if game["awayProTeamId"] == team_id else game["awayProTeamId"]
        when = datetime.fromtimestamp(game["date"] / 1000.0, tz=timezone.utc)
        out[team_id] = (opponent, when)
    return out


def _week_stats(player, scoring_period, year):
    projected = actual = None
    for stats in player.get("stats") or []:
        if stats.get("seasonId") != year or stats.get("scoringPeriodId") != scoring_period:
            continue
        if stats.get("statSplitTypeId") not in (None, 1):
            continue
        if stats.get("statSourceId") == 1:
            projected = round(stats.get("appliedTotal", 0) or 0, 2)
        elif stats.get("statSourceId") == 0:
            actual = round(stats.get("appliedTotal", 0) or 0, 2)
    return projected, actual


def _season_stats(player, year):
    points = avg = None
    for stats in player.get("stats") or []:
        if stats.get("seasonId") != year or stats.get("scoringPeriodId") != 0:
            continue
        if stats.get("statSplitTypeId") not in (None, 0):
            continue
        if stats.get("statSourceId") == 0:
            points = round(stats.get("appliedTotal", 0) or 0, 2)
            avg = round(stats.get("appliedAverage", 0) or 0, 2)
    return points, avg


def parse_entry(entry, year, scoring_period, kickoffs):
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or {}
    pro_team_id = player.get("proTeamId")
    eligible = [s for s in (player.get("eligibleSlots") or [])]
    position = player_position(player)
    projected, actual = _week_stats(player, scoring_period, year)
    season_points, season_avg = _season_stats(player, year)
    game = kickoffs.get(pro_team_id)
    opponent = kickoff = None
    bye = game is None
    if game:
        opponent = PRO_TEAM_MAP.get(game[0], str(game[0]))
        kickoff = game[1].isoformat()
    return RosterEntry(
        player_id=int(entry["playerId"]),
        name=player.get("fullName", str(entry["playerId"])),
        position=position,
        pro_team=PRO_TEAM_MAP.get(pro_team_id, str(pro_team_id)),
        slot_id=int(entry.get("lineupSlotId", BENCH_SLOT)),
        slot=slot_name(int(entry.get("lineupSlotId", BENCH_SLOT))),
        eligible_slot_ids=eligible,
        eligible_slots=[slot_name(s) for s in eligible],
        injury_status=player.get("injuryStatus") or entry.get("injuryStatus") or "ACTIVE",
        injured=bool(player.get("injured", False)),
        lineup_locked=bool(pool.get("lineupLocked", False)),
        roster_locked=bool(pool.get("rosterLocked", False)),
        trade_locked=bool(pool.get("tradeLocked", False)),
        droppable=bool(player.get("droppable", True)),
        projected=projected,
        actual=actual,
        season_points=season_points,
        season_avg=season_avg,
        percent_owned=round((player.get("ownership") or {}).get("percentOwned", 0) or 0, 1),
        opponent=opponent,
        kickoff=kickoff,
        bye=bye,
    )


def fetch_rosters(league, scoring_period=None):
    """{team_id: [RosterEntry]} for every team, from the raw mRoster view."""
    period = scoring_period or league.scoringPeriodId
    raw = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": period})
    try:
        kickoffs = kickoffs_for_period(league._get_all_pro_schedule(), period)
    except Exception:
        kickoffs = {}
    rosters = {}
    for team in raw.get("teams") or []:
        entries = (team.get("roster") or {}).get("entries") or []
        rosters[int(team["id"])] = [parse_entry(e, league.year, period, kickoffs) for e in entries]
    return rosters


def fetch_roster(league, team_id, scoring_period=None):
    return fetch_rosters(league, scoring_period).get(int(team_id), [])


def slot_counts(league):
    """{slot_id: count} of starting slots this league uses."""
    counts = getattr(league.settings, "position_slot_counts", None) or {}
    out = {}
    for name, count in counts.items():
        slot_id = SLOT_ID_BY_NAME.get(name)
        if isinstance(slot_id, int) and count and slot_id not in (BENCH_SLOT, IR_SLOT):
            out[slot_id] = int(count)
    return out


def lineup_problems(entries, counts):
    """
    Plain-language reasons a lineup is illegal, or an empty list.

    Checks slot eligibility and slot counts. It does not know about
    injured-reserve rules beyond "IR needs an injury tag"; ESPN enforces the
    rest and the write result reports it.
    """
    problems = []
    used = {}
    for e in entries:
        if e.slot_id in (BENCH_SLOT,):
            continue
        if e.slot_id == IR_SLOT:
            if e.injury_status in ("ACTIVE", "QUESTIONABLE", "PROBABLE"):
                problems.append(f"{e.name} is in IR without an IR-eligible tag ({e.injury_status})")
            continue
        if e.slot_id not in e.eligible_slot_ids:
            problems.append(f"{e.name} is not eligible for {e.slot}")
        used[e.slot_id] = used.get(e.slot_id, 0) + 1
    for slot_id, n in used.items():
        allowed = counts.get(slot_id, 0)
        if n > allowed:
            problems.append(f"{n} players in {slot_name(slot_id)} but only {allowed} allowed")
    return problems


def distinct_kickoffs(entries, include_bench=True):
    """Sorted list of (kickoff datetime, [player names]) across a roster."""
    by_time = {}
    for e in entries:
        if not e.kickoff:
            continue
        if not include_bench and not e.is_starter:
            continue
        when = datetime.fromisoformat(e.kickoff)
        by_time.setdefault(when, []).append(e.name)
    return sorted(by_time.items())


def to_table(entries):
    """Compact text table, one line per player, for prompts and Discord."""
    order = {s: i for i, s in enumerate(STARTING_SLOT_ORDER + [BENCH_SLOT, IR_SLOT])}
    rows = sorted(entries, key=lambda e: (order.get(e.slot_id, 99), -(e.projected or 0)))
    lines = []
    for e in rows:
        flags = []
        if e.injury_status not in ("ACTIVE", None):
            flags.append(e.injury_status)
        if e.lineup_locked:
            flags.append("LOCKED")
        if e.bye:
            flags.append("BYE")
        when = ""
        if e.kickoff:
            when = datetime.fromisoformat(e.kickoff).astimezone(_eastern()).strftime("%a %I:%M%p").replace(" 0", " ")
        lines.append(
            f"{e.slot:<8} {e.name:<24} {e.position:<4} {e.pro_team:<4} "
            f"{'vs ' + e.opponent if e.opponent else 'bye':<8} {when:<11} "
            f"proj {e.projected if e.projected is not None else '-':<6} "
            f"avg {e.season_avg if e.season_avg is not None else '-':<6} "
            f"id {e.player_id} {' '.join(flags)}".rstrip()
        )
    return "\n".join(lines)


def _eastern():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        return timezone(timedelta(hours=-4))
