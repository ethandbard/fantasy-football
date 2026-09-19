"""
Reading the league's own list of who ran which team.

ESPN knows a manager's GUID and legal first name; the league knows them as
Connor and Ed. The list that closes the gap is written the way a person
remembers it -- a season, then "Team name - Person" lines:

    2025
    First Down Syndrome - Josh
    Yikes (3) - Ed

Each line is resolved through that season's teams rows to the manager key, so
one line anywhere in a manager's history names them in every season.
"""
import re

from gamedaybot.web import stats


def parse_names(text):
    """[(year, team name, person)] from the list format above. Blank lines
    and lines that are neither a year nor "team - person" are skipped."""
    entries, year = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if re.fullmatch(r"(19|20)\d\d", line):
            year = int(line)
            continue
        # Split on the last " - ": a team name may carry a dash of its own.
        team, sep, person = line.rpartition(" - ")
        if year is None or not sep or not team.strip() or not person.strip():
            continue
        entries.append((year, team.strip(), person.strip()))
    return entries


def _squash(name):
    """Letters and digits only, so "Yikes (2)" finds "Yikes(2)" and an emoji
    or a trailing space cannot stop a match."""
    return re.sub(r"[^a-z0-9]", "", str(name).casefold())


def resolve(entries, team_rows):
    """
    ({manager key: person}, [problem strings]) from parsed entries and the
    teams table's rows.

    A team is matched by name within its season. When a season is left with
    exactly one unmatched line and one unmatched team, they are paired: a
    team renamed since the list was written is the usual cause, and with one
    of each there is nothing else it could be.
    """
    names, problems = {}, []
    for year in sorted({y for y, _, _ in entries}):
        season = [t for t in team_rows if int(t["year"]) == year]
        by_name = {_squash(t["team_name"]): t for t in season}
        matched, loose = set(), []
        for _, team, person in (e for e in entries if e[0] == year):
            row = by_name.get(_squash(team))
            if row is None:
                loose.append((team, person))
                continue
            matched.add(int(row["team_id"]))
            names[stats.manager_key(row)] = person
        spare = [t for t in season if int(t["team_id"]) not in matched]
        if len(loose) == 1 and len(spare) == 1:
            team, person = loose[0]
            names[stats.manager_key(spare[0])] = person
            problems.append(f"{year}: no team named '{team}'; paired {person} with the "
                            f"one team left over, '{spare[0]['team_name']}'")
        else:
            problems += [f"{year}: no team named '{team}' ({person} not set)"
                         for team, person in loose]
    return names, problems
