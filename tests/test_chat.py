"""
Tests for the data chat backend: the environment gate, the daily caps, the
tools' text over a seeded database, and the streaming answer with a fake
client. Nothing here talks to the Anthropic API.
"""
import asyncio
import importlib

import pytest

import gamedaybot.storage.db as db
import gamedaybot.web.chat as chat


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "chat.db"))
    importlib.reload(db)
    importlib.reload(chat)
    db.init_db()
    yield db
    importlib.reload(db)
    importlib.reload(chat)


TEAMS = {1: "Alpha Wolves", 2: "Bravo Bears", 3: "Charlie Cats", 4: "Delta Dogs"}


def _score(week, team_id, opp_id, score, is_home):
    return {
        "year": 2025, "week": week, "team_id": team_id, "team_name": TEAMS[team_id],
        "score": score, "projected_score": 100.0, "opponent_id": opp_id,
        "opponent_name": TEAMS[opp_id], "is_home": is_home,
        "matchup_period": week, "matchup_score": score,
    }


@pytest.fixture
def seeded(fresh_db):
    """Four teams, two weeks. Alpha wins both, Delta loses both."""
    fresh_db.upsert_teams([
        {"year": 2025, "team_id": tid, "team_name": name, "abbrev": name[:3].upper(),
         "logo_url": None, "owner": f"Owner {tid}"}
        for tid, name in TEAMS.items()
    ])
    fresh_db.upsert_weekly_scores([
        _score(1, 1, 2, 130.0, 1), _score(1, 2, 1, 110.0, 0),
        _score(1, 3, 4, 95.5, 1), _score(1, 4, 3, 90.0, 0),
        _score(2, 1, 3, 120.0, 0), _score(2, 3, 1, 100.0, 1),
        _score(2, 2, 4, 105.0, 0), _score(2, 4, 2, 80.0, 1),
    ])
    return fresh_db


# --------------------------------------------------------------------------
# Configuration and gating
# --------------------------------------------------------------------------

def test_enabled_is_false_without_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CHAT_PASSPHRASE", raising=False)
    ok, reason = chat.enabled()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in reason


def test_enabled_needs_passphrase_too(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CHAT_PASSPHRASE", raising=False)
    ok, reason = chat.enabled()
    assert ok is False
    assert "passphrase" in reason.lower()


def test_enabled_is_true_with_both(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CHAT_PASSPHRASE", "go-league")
    assert chat.enabled() == (True, "")


def test_check_passphrase(monkeypatch):
    monkeypatch.setenv("CHAT_PASSPHRASE", "go-league")
    assert chat.check_passphrase("go-league")
    assert chat.check_passphrase("  go-league \n")
    assert not chat.check_passphrase("GO-LEAGUE")
    assert not chat.check_passphrase("")
    assert not chat.check_passphrase(None)
    monkeypatch.delenv("CHAT_PASSPHRASE")
    assert not chat.check_passphrase("go-league")


def test_allow_enforces_viewer_cap(fresh_db, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "2")
    monkeypatch.setenv("CHAT_LEAGUE_DAILY_LIMIT", "100")
    assert chat.allow("a") == (True, "")
    fresh_db.log_chat("a", "q1")
    fresh_db.log_chat("a", "q2")
    ok, reason = chat.allow("a")
    assert ok is False and "2" in reason
    # Another viewer is unaffected by a's cap.
    assert chat.allow("b") == (True, "")


def test_allow_enforces_league_cap(fresh_db, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "100")
    monkeypatch.setenv("CHAT_LEAGUE_DAILY_LIMIT", "3")
    for viewer in ("a", "b", "c"):
        fresh_db.log_chat(viewer, "q")
    ok, reason = chat.allow("z")
    assert ok is False and "league" in reason.lower()


def test_allow_defaults_when_env_unset(fresh_db, monkeypatch):
    monkeypatch.delenv("CHAT_DAILY_LIMIT", raising=False)
    monkeypatch.delenv("CHAT_LEAGUE_DAILY_LIMIT", raising=False)
    assert chat.allow("a") == (True, "")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def test_list_seasons_names_the_seeded_year(seeded):
    out = chat.list_seasons()
    assert "2025" in out
    assert "Newest season: 2025" in out
    assert "| 2025 | 4 | 2 | 1-2 |" in out


def test_list_seasons_on_empty_db(fresh_db):
    assert "No seasons" in chat.list_seasons()


def test_standings_names_the_leader(seeded):
    out = chat.standings(2025)
    lines = [ln for ln in out.splitlines() if ln.startswith("| 1 |")]
    assert lines and "Alpha Wolves" in lines[0]
    assert "2-0" in lines[0]
    assert "Delta Dogs" in out and "0-2" in out


def test_standings_unknown_year_is_helpful(seeded):
    out = chat.standings(1999)
    assert "No data for 1999" in out and "2025" in out


def test_standings_accepts_year_as_string_and_defaults_to_newest(seeded):
    assert "Alpha Wolves" in chat.standings("2025")
    assert "2025 standings" in chat.standings(None)


def test_team_summary_matches_case_insensitive_substring(seeded):
    out = chat.team_summary(2025, "alpha")
    assert "Alpha Wolves (2025)" in out
    assert "Record 2-0" in out
    assert "Owner 1" in out
    assert "| 1 | Bravo Bears | 130.0 | 110.0 |" in out


def test_team_summary_matches_abbrev_and_owner(seeded):
    assert "Bravo Bears" in chat.team_summary(2025, "BRA")
    assert "Charlie Cats" in chat.team_summary(2025, "owner 3")


def test_unknown_team_gives_helpful_message(seeded):
    out = chat.team_summary(2025, "Zeta")
    assert "No team matching 'Zeta'" in out
    for name in TEAMS.values():
        assert name in out


def test_ambiguous_team_lists_candidates(seeded):
    # Every seeded owner is "Owner N", so "owner" alone matches all four.
    out = chat.team_summary(2025, "owner")
    assert "several teams" in out


def test_head_to_head_shows_a_record(seeded):
    out = chat.head_to_head("2025")
    row = next(ln for ln in out.splitlines() if ln.startswith("| Alpha Wolves |"))
    # Columns follow sorted team order: Alpha, Bravo, Charlie, Delta.
    assert row == "| Alpha Wolves | — | 1-0 | 1-0 | 0-0 |"


def test_head_to_head_all_time(seeded):
    out = chat.head_to_head("all")
    assert "All-time" in out
    assert "| Delta Dogs |" in out


def test_week_results(seeded):
    out = chat.week_results(2025, 1)
    assert "| Alpha Wolves | 130.0 | 100.0 | Bravo Bears | 110.0 | +20.0 | W |" in out
    assert "No scores for 2025 week 9" in chat.week_results(2025, 9)


def test_records_and_all_time_records(seeded):
    season = chat.records(2025)
    assert "Highest Score" in season and "Alpha Wolves" in season
    all_time = chat.all_time_records()
    assert "Highest Single-Week Score" in all_time and "2025" in all_time


def test_trades_and_moves_and_draft_when_empty(seeded):
    assert "No trades" in chat.trades(2025)
    assert "No activity" in chat.recent_moves(2025)
    assert "No draft" in chat.draft(2025)
    assert "No lineup data" in chat.player_leaderboard(2025)
    assert "No schedule" in chat.schedule(2025, "alpha")


def test_trades_groups_legs_by_date(seeded):
    seeded.insert_new_trades([
        {"year": 2025, "trade_date": 1_700_000_000_000, "player_id": 10, "player_name": "Player A",
         "position": "RB", "from_team_id": 1, "from_team_name": "Alpha Wolves",
         "to_team_id": 2, "to_team_name": "Bravo Bears"},
        {"year": 2025, "trade_date": 1_700_000_000_000, "player_id": 11, "player_name": "Player B",
         "position": "WR", "from_team_id": 2, "from_team_name": "Bravo Bears",
         "to_team_id": 1, "to_team_name": "Alpha Wolves"},
    ])
    out = chat.trades(2025)
    assert "1 trade(s)" in out
    assert "Alpha Wolves sent Player A (RB) to Bravo Bears" in out
    assert "Bravo Bears sent Player B (WR) to Alpha Wolves" in out
    assert "2023-11-14" in out


def test_recent_moves_respects_limit(seeded):
    seeded.insert_new_activity([
        {"year": 2025, "date": 1_700_000_000_000 + i, "team_id": 1, "team_name": "Alpha Wolves",
         "action": "ADDED", "player_id": 100 + i, "player_name": f"Pickup {i}",
         "position": "WR", "bid_amount": 5 if i == 0 else None}
        for i in range(5)
    ])
    out = chat.recent_moves(2025, limit=2)
    assert "Last 2 moves" in out
    assert out.count("Pickup") == 2
    assert "$5" in chat.recent_moves(2025)


def test_draft_filters_by_team(seeded):
    seeded.replace_draft_picks(2025, [
        {"year": 2025, "overall_pick": 1, "round_num": 1, "round_pick": 1, "team_id": 1,
         "team_name": "Alpha Wolves", "player_id": 1, "player_name": "First Pick",
         "bid_amount": None, "keeper": 0},
        {"year": 2025, "overall_pick": 2, "round_num": 1, "round_pick": 2, "team_id": 2,
         "team_name": "Bravo Bears", "player_id": 2, "player_name": "Second Pick",
         "bid_amount": None, "keeper": 1},
    ])
    whole = chat.draft(2025)
    assert "First Pick" in whole and "Second Pick" in whole and "keeper" in whole
    mine = chat.draft(2025, team="bravo")
    assert "Second Pick" in mine and "First Pick" not in mine


def test_player_leaderboard_counts_starters_only(seeded):
    seeded.replace_lineup_week(2025, 1, [
        {"year": 2025, "week": 1, "team_id": 1, "player_id": 1, "player_name": "Star RB",
         "position": "RB", "pro_team": "X", "slot": "RB", "eligible_slots": ["RB"],
         "projected": 15.0, "points": 30.0, "injury_status": None},
        {"year": 2025, "week": 1, "team_id": 1, "player_id": 2, "player_name": "Bench RB",
         "position": "RB", "pro_team": "X", "slot": "BE", "eligible_slots": ["RB"],
         "projected": 10.0, "points": 40.0, "injury_status": None},
        {"year": 2025, "week": 1, "team_id": 2, "player_id": 3, "player_name": "Good WR",
         "position": "WR", "pro_team": "X", "slot": "WR", "eligible_slots": ["WR"],
         "projected": 12.0, "points": 20.0, "injury_status": None},
    ])
    seeded.replace_lineup_week(2025, 2, [
        {"year": 2025, "week": 2, "team_id": 1, "player_id": 1, "player_name": "Star RB",
         "position": "RB", "pro_team": "X", "slot": "RB/WR/TE", "eligible_slots": ["RB"],
         "projected": 15.0, "points": 12.0, "injury_status": None},
    ])
    out = chat.player_leaderboard(2025)
    assert "| 1 | Star RB | RB | 42.0 | 2 | 21.0 | Alpha Wolves |" in out
    assert "Bench RB" not in out
    wr = chat.player_leaderboard(2025, position="wr")
    assert "Good WR" in wr and "Star RB" not in wr
    assert "No starters at QB" in chat.player_leaderboard(2025, position="QB")


def test_schedule_lists_only_unplayed_weeks(seeded):
    seeded.upsert_schedule([
        {"year": 2025, "week": w, "matchup_period": w, "team_id": 1,
         "opponent_id": 2 if w % 2 else 4, "is_home": w % 2}
        for w in (1, 2, 3, 4)
    ])
    out = chat.schedule(2025, "alpha")
    assert "through week 2" in out
    assert "| 3 | Bravo Bears | home |" in out
    assert "| 4 | Delta Dogs | away |" in out
    assert "| 1 |" not in out


def test_tools_never_raise(seeded, monkeypatch):
    def boom():
        raise RuntimeError("db exploded")
    monkeypatch.setattr(chat.db, "get_all_weekly_scores", boom)
    out = chat.standings(2025)
    assert out.startswith("Error in standings:")
    assert "db exploded" in out


def test_tool_output_is_capped(monkeypatch):
    long = "x" * 10_000
    assert len(chat._cap(long)) <= chat.MAX_TOOL_CHARS + len("\n... (truncated)")
    assert chat._cap(long).endswith("(truncated)")


# --------------------------------------------------------------------------
# Client and answer()
# --------------------------------------------------------------------------

def test_make_client_registers_every_tool(fresh_db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("CHAT_MODEL", "claude-haiku-4-5-20251001")
    client = chat.make_client(today="Monday, September 1, 2025")
    names = {t.name for t in client.get_tools()}
    assert names == {t.__name__ for t in chat.TOOLS}
    assert "Monday, September 1, 2025" in (client.system_prompt or "")


def test_system_prompt_mentions_the_rules():
    prompt = chat.system_prompt(today="Sunday, October 5, 2025")
    assert "8-team" in prompt
    assert "Sunday, October 5, 2025" in prompt
    assert "/ask" in prompt


class _Turn:
    def __init__(self, role, tokens=None):
        self.role = role
        self.tokens = tokens


class _FakeClient:
    """Just enough of chatlas.Chat: stream_async as a plain async generator
    (not awaitable) and get_turns growing as a real chat's would."""

    def __init__(self, chunks, tokens=None, fail_after=None):
        self.chunks = chunks
        self.tokens = tokens
        self.fail_after = fail_after
        self.turns = []
        self.questions = []

    def get_turns(self):
        return list(self.turns)

    async def stream_async(self, question):
        self.questions.append(question)
        self.turns.append(_Turn("user"))
        for i, c in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("stream broke")
            yield c
        self.turns.append(_Turn("assistant", self.tokens))


class _AwaitableFakeClient(_FakeClient):
    """chatlas' real shape: stream_async is a coroutine returning the generator."""

    async def stream_async(self, question):  # type: ignore[override]
        return super().stream_async(question)


def _collect(gen):
    async def run():
        return [c async for c in gen]
    return asyncio.run(run())


def test_answer_streams_chunks_and_logs_tokens(fresh_db):
    client = _FakeClient(["The leader ", "is Alpha."], tokens=(120, 30, 0))
    chunks = _collect(chat.answer(client, "viewer-1", "Who leads?"))
    assert chunks == ["The leader ", "is Alpha."]
    assert client.questions == ["Who leads?"]
    assert fresh_db.chats_today("viewer-1") == 1
    with fresh_db.get_connection() as conn:
        row = conn.execute("SELECT * FROM chat_log").fetchone()
    assert row["question"] == "Who leads?"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 30


def test_answer_handles_awaitable_stream_async(fresh_db):
    client = _AwaitableFakeClient(["a", "b"], tokens=(1, 2, 0))
    assert _collect(chat.answer(client, "v", "q")) == ["a", "b"]
    assert fresh_db.chats_today("v") == 1


def test_answer_logs_none_tokens_when_not_exposed(fresh_db):
    client = _FakeClient(["hi"], tokens=None)
    _collect(chat.answer(client, "v", "q"))
    with fresh_db.get_connection() as conn:
        row = conn.execute("SELECT input_tokens, output_tokens FROM chat_log").fetchone()
    assert row["input_tokens"] is None and row["output_tokens"] is None


def test_answer_sums_tokens_across_tool_round_trips(fresh_db):
    class _ToolClient(_FakeClient):
        async def stream_async(self, question):
            self.turns.append(_Turn("user"))
            self.turns.append(_Turn("assistant", (100, 10, 0)))   # tool request
            self.turns.append(_Turn("user"))                       # tool result
            yield "answer"
            self.turns.append(_Turn("assistant", (200, 40, 50)))   # final

    client = _ToolClient([])
    client.turns.append(_Turn("user"))                  # an earlier question
    client.turns.append(_Turn("assistant", (999, 999, 0)))
    _collect(chat.answer(client, "v", "q"))
    with fresh_db.get_connection() as conn:
        row = conn.execute("SELECT input_tokens, output_tokens FROM chat_log").fetchone()
    assert (row["input_tokens"], row["output_tokens"]) == (300, 50)


def test_answer_still_logs_when_the_stream_fails(fresh_db):
    client = _FakeClient(["ok", "boom"], fail_after=1)
    with pytest.raises(RuntimeError):
        _collect(chat.answer(client, "v", "q"))
    assert fresh_db.chats_today("v") == 1


def test_rivalry_follows_a_manager_through_a_rename(seeded):
    # 2024: the manager of 2025's Alpha Wolves ran "Old Alphas" under another
    # team id, and lost to the manager of Bravo Bears.
    seeded.upsert_teams([
        {"year": 2024, "team_id": 7, "team_name": "Old Alphas", "abbrev": "OLD", "logo_url": None,
         "owner": "Owner 1"},
        {"year": 2024, "team_id": 2, "team_name": "Bravo Bears", "abbrev": "BRA", "logo_url": None,
         "owner": "Owner 2"},
    ])
    old = {"year": 2024, "week": 1, "projected_score": 100.0, "matchup_period": 1}
    seeded.upsert_weekly_scores([
        {**old, "team_id": 7, "team_name": "Old Alphas", "score": 90.0, "matchup_score": 90.0,
         "opponent_id": 2, "opponent_name": "Bravo Bears", "is_home": 1},
        {**old, "team_id": 2, "team_name": "Bravo Bears", "score": 99.0, "matchup_score": 99.0,
         "opponent_id": 7, "opponent_name": "Old Alphas", "is_home": 0},
    ])
    seeded.upsert_managers({"owner 1": "Ann"})

    out = chat.rivalry(2025, "ann", "bravo")
    assert "Alpha Wolves (Ann) vs Bravo Bears" in out
    assert "All-time series tied 1-1 over 2 seasons." in out
    assert "| 2024 | 1 | Old Alphas | 90.0 | 99.0 | Bravo Bears |" in out

    grid = chat.head_to_head("all")
    assert "Old Alphas" not in grid
    assert next(ln for ln in grid.splitlines() if ln.startswith("| Alpha Wolves |")).split("|")[3].strip() == "1-1"
