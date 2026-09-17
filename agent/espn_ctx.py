"""
One run's view of the league: a cached espn_api League, parsed rosters, and
a write client whose dry-run flag is decided once, here.

Tools share an instance so a run reads the league once and re-reads only
when a write is about to happen.
"""
import json
import logging
from datetime import datetime, timezone

from espn_api.football import League

import gamedaybot.espn.roster as roster
from gamedaybot.espn.writes import WriteClient

logger = logging.getLogger(__name__)


def _dt(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="minutes")


class EspnContext:
    def __init__(self, cfg, rules, write_enabled=True):
        self.cfg = cfg
        self.rules = rules
        self._league = None
        self._rosters = None
        self._pool = None
        self.write_enabled = write_enabled and not cfg.dry_run
        self.writer = WriteClient(cfg.league_id, cfg.year, cfg.espn_s2, cfg.swid, cfg.team_id,
                                  dry_run=not self.write_enabled)

    # ------------------------------------------------------------ league

    def league(self, refresh=False):
        if self._league is None or refresh:
            self._league = League(league_id=self.cfg.league_id, year=self.cfg.year,
                                  espn_s2=self.cfg.espn_s2, swid=self.cfg.swid)
            self._rosters = None
        return self._league

    @property
    def scoring_period(self):
        return self.league().scoringPeriodId

    @property
    def week(self):
        return self.league().current_week

    def ownership(self):
        """
        (owns_team, owner_names): whether the configured SWID owns TEAM_ID.

        ESPN's write host answers AUTH_UNAUTHORIZED_FOR_TEAM for any other
        account, even one that reads the league fine, so this is the check
        that says whether writes can work at all.
        """
        raw = self.league().espn_request.league_get(params={"view": "mTeam"})
        members = {m["id"].upper(): m for m in raw.get("members", [])}
        for t in raw.get("teams", []):
            if int(t["id"]) != self.cfg.team_id:
                continue
            owners = [o.upper() for o in t.get("owners", [])]
            names = [f"{members.get(o, {}).get('firstName', '')} {members.get(o, {}).get('lastName', '')}".strip()
                     or o[:9] for o in owners]
            return self.cfg.swid.upper() in owners, names
        return False, []

    def team(self, team_id):
        for t in self.league().teams:
            if t.team_id == int(team_id):
                return t
        return None

    def team_name(self, team_id):
        t = self.team(team_id)
        return t.team_name if t else f"team {team_id}"

    def teams_summary(self):
        rows = []
        for t in sorted(self.league().teams, key=lambda t: t.standing):
            owners = ", ".join(
                f"{o.get('firstName', '')} {o.get('lastName', '')}".strip() if isinstance(o, dict) else str(o)
                for o in (t.owners or [])
            )
            rows.append({
                "team_id": t.team_id, "name": t.team_name, "owner": owners,
                "record": f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else ""),
                "points_for": round(t.points_for, 1), "points_against": round(t.points_against, 1),
                "standing": t.standing, "waiver_rank": t.waiver_rank, "streak": f"{t.streak_type} {t.streak_length}",
            })
        return rows

    # ------------------------------------------------------------ rosters

    def rosters(self, refresh=False):
        if self._rosters is None or refresh:
            self._rosters = roster.fetch_rosters(self.league(refresh=refresh))
        return self._rosters

    def my_roster(self, refresh=False):
        return self.rosters(refresh).get(self.cfg.team_id, [])

    def entry(self, player_id, team_id=None, refresh=False):
        team_id = self.cfg.team_id if team_id is None else int(team_id)
        for e in self.rosters(refresh).get(team_id, []):
            if e.player_id == int(player_id):
                return e
        return None

    def find_entry(self, player_id):
        """(team_id, entry) for a rostered player anywhere in the league."""
        for team_id, entries in self.rosters().items():
            for e in entries:
                if e.player_id == int(player_id):
                    return team_id, e
        return None, None

    def slot_counts(self):
        return roster.slot_counts(self.league())

    # --------------------------------------------------------- free agents

    def free_agents(self, position=None, size=20, sort="projected"):
        """Free agents and waiver-wire players with the roster entry fields plus status."""
        lg = self.league()
        period = lg.scoringPeriodId
        slot_ids = []
        if position:
            pos = position.upper().replace("DST", "D/ST").replace("FLEX", "RB/WR/TE")
            slot_id = roster.SLOT_ID_BY_NAME.get(pos)
            if slot_id is None:
                raise ValueError(f"unknown position {position}")
            slot_ids = [slot_id] if slot_id != 23 else [2, 4, 6]
        year = lg.year
        filters = {"players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "filterSlotIds": {"value": slot_ids},
            "limit": max(int(size) * 3, 60),
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "PPR"},
            # Season totals, season projection, this week's projection, last
            # week's actual (the "top" period), and last season's total.
            "filterStatsForTopScoringPeriodIds": {
                "value": 2,
                "additionalValue": [f"00{year}", f"10{year}", f"11{year}{period}", f"00{year - 1}"],
            },
        }}
        data = lg.espn_request.league_get(
            params={"view": "kona_player_info", "scoringPeriodId": period},
            headers={"x-fantasy-filter": json.dumps(filters)},
        )
        try:
            kickoffs = roster.kickoffs_for_period(lg._get_all_pro_schedule(), period)
        except Exception:
            kickoffs = {}
        out = []
        for item in data.get("players") or []:
            entry = roster.parse_entry({"playerId": item["id"], "lineupSlotId": 20, "playerPoolEntry": item},
                                       lg.year, period, kickoffs)
            d = entry.to_dict()
            d["status"] = item.get("status", "FREEAGENT")
            d["last_week"] = self._last_week_points(item, period)
            out.append(d)
        key = {"projected": lambda d: d.get("projected") or 0,
               "season_avg": lambda d: d.get("season_avg") or 0,
               "owned": lambda d: d.get("percent_owned") or 0,
               "last_week": lambda d: d.get("last_week") or 0}.get(sort, lambda d: d.get("projected") or 0)
        out.sort(key=key, reverse=True)
        return out[:int(size)]

    def _last_week_points(self, item, period):
        for stats in (item.get("player") or {}).get("stats") or []:
            if (stats.get("scoringPeriodId") == period - 1 and stats.get("statSourceId") == 0
                    and stats.get("seasonId") == self.cfg.year):
                return round(stats.get("appliedTotal", 0) or 0, 2)
        return None

    # ------------------------------------------------------ transactions

    def pending_transactions(self):
        """Pending claims and trade offers, with player names resolved."""
        lg = self.league()
        data = lg.espn_request.league_get(params={"view": "mPendingTransactions"})
        out = []
        for t in data.get("pendingTransactions") or []:
            items = []
            for i in t.get("items") or []:
                _, e = self.find_entry(i.get("playerId")) if i.get("playerId") else (None, None)
                name = e.name if e else self._pool_name(i.get("playerId"))
                items.append({
                    "type": i.get("type"), "player_id": i.get("playerId"), "player": name,
                    "from_team_id": i.get("fromTeamId"), "to_team_id": i.get("toTeamId"),
                })
            out.append({
                "id": t.get("id"), "type": t.get("type"), "status": t.get("status"),
                "team_id": t.get("teamId"), "team": self.team_name(t.get("teamId")),
                "scoring_period": t.get("scoringPeriodId"),
                "proposed": _dt(t.get("proposedDate")), "expires": _dt(t.get("expirationDate")),
                "bid": t.get("bidAmount"), "items": items,
                "involves_me": any(
                    i.get("from_team_id") == self.cfg.team_id or i.get("to_team_id") == self.cfg.team_id for i in items
                ) or t.get("teamId") == self.cfg.team_id,
            })
        return out

    def incoming_offers(self):
        return [t for t in self.pending_transactions()
                if t["type"] == "TRADE_PROPOSAL" and t["status"] == "PENDING"
                and t["team_id"] != self.cfg.team_id and t["involves_me"]]

    def _pool_name(self, player_id):
        if player_id is None:
            return None
        try:
            p = self.league().player_info(playerId=int(player_id))
            return p.name if p else str(player_id)
        except Exception:
            return str(player_id)

    def recent_activity(self, size=25):
        rows = []
        for act in self.league().recent_activity(size=size):
            when = _dt(act.date)
            for action in act.actions:
                team, verb, player = action[0], action[1], action[2]
                rows.append({
                    "when": when,
                    "team": getattr(team, "team_name", str(team)),
                    "action": verb,
                    "player": getattr(player, "name", str(player)),
                })
        return rows
