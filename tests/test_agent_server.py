"""
The HTTP API the Discord bot talks to, with a fake queue and clock. Covers
the routes the bot depends on: health, status, run, the ask flow with its
rate limits, approvals, and team claims.
"""
import asyncio
import importlib

import pytest
from aiohttp.test_utils import TestClient, TestServer

import gamedaybot.storage.db as db


class _Queue:
    def __init__(self):
        self.submitted = []
        self.current = None

    @property
    def size(self):
        return 0

    def submit(self, job, params=None, trigger="api"):
        import agent.store as store
        run_id = store.create_run(job, trigger=trigger, params=params, status="queued")
        self.submitted.append((job, params, trigger))
        return run_id


class _Clock:
    def next_fires(self):
        return [{"job": "tick", "next": "2026-09-18T00:00"}]

    async def canary(self, force_post=False):
        return True

    async def ensure_wakeups(self, force=False):
        import agent.store as store
        from datetime import datetime, timedelta, timezone
        self.forced = force
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        store.add_wakeup("pregame", run_at, params={"players": ["Gibbs"]}, label="Thu 08:15 PM ET kickoff")
        return [(run_at, "Thu 08:15 PM ET kickoff", {"players": ["Gibbs"]})]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("LEAGUE_ID", "1")
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AGENT_ASK_DAILY_LIMIT", "2")
    monkeypatch.setenv("AGENT_ASK_LEAGUE_DAILY_LIMIT", "3")
    importlib.reload(db)
    import agent.store as store
    importlib.reload(store)
    store.init()
    from agent import config, server
    cfg = config.from_env()
    queue = _Queue()
    clock = _Clock()
    app = server.build_app(cfg, queue, clock)
    app["clock"] = clock  # so a test can see what the route asked of it
    yield app, queue, store
    importlib.reload(db)


def _run(coro):
    return asyncio.run(coro)


def test_health_status_and_run(app_env):
    app, queue, store = app_env

    async def go():
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/health")
            assert r.status == 200 and (await r.json())["ok"] is True
            r = await client.post("/run/lineup", json={"params": {}, "trigger": "discord:ethan"})
            body = await r.json()
            assert r.status == 200 and body["run_id"]
            assert queue.submitted[0][0] == "lineup"
            r = await client.post("/run/nope")
            assert r.status == 404
            r = await client.post("/run/ask")
            assert r.status == 400
            r = await client.get("/status")
            data = await r.json()
            assert data["recent_runs"][0]["job"] == "lineup" and data["next_fires"]
            r = await client.get(f"/runs/{body['run_id']}")
            assert (await r.json())["status"] == "queued"
    _run(go())


def test_ask_requires_claim_and_enforces_limits(app_env):
    app, queue, store = app_env

    async def go():
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/ask", json={"question": "flex?", "discord_user_id": "u1"})
            assert r.status == 403
            r = await client.post("/users/u1", json={"team_id": 4, "display_name": "Yikes"})
            assert r.status == 200
            r = await client.get("/users/u1")
            assert (await r.json())["team_id"] == 4
            for _ in range(2):
                r = await client.post("/ask", json={"question": "flex?", "discord_user_id": "u1"})
                assert r.status == 200
            r = await client.post("/ask", json={"question": "again", "discord_user_id": "u1"})
            assert r.status == 429
            await client.post("/users/u2", json={"team_id": 5})
            r = await client.post("/ask", json={"question": "hi", "discord_user_id": "u2"})
            assert r.status == 200
            r = await client.post("/ask", json={"question": "hi", "discord_user_id": "u2"})
            assert r.status == 429  # league-wide budget of 3
            assert all(j == "ask" for j, _, _ in queue.submitted)
            assert queue.submitted[0][1]["asker_team_id"] == 4
    _run(go())


def test_ask_lifecycle_endpoints(app_env, monkeypatch):
    app, queue, store = app_env
    ask_id = store.create_ask("lineup", "swap", {"type": "ROSTER", "items": []}, reason="why")
    executed = {}

    def fake_execute(cfg, rules, ask):
        executed["id"] = ask["id"]
        store.resolve_ask(ask["id"], "executed", "ok")
        return True, "executed"

    from agent import approvals
    monkeypatch.setattr(approvals, "execute_ask", fake_execute)

    async def go():
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/asks/pending")
            assert [a["id"] for a in await r.json()] == [ask_id]
            r = await client.post(f"/asks/{ask_id}/posted", json={"message_id": "999"})
            assert r.status == 200
            r = await client.post(f"/asks/{ask_id}/approve", json={"by": "ethan"})
            assert r.status == 200 and (await r.json())["ok"] is True
            assert executed["id"] == ask_id
            r = await client.post(f"/asks/{ask_id}/approve", json={"by": "ethan"})
            assert r.status == 409
            r = await client.post("/asks/missing/reject", json={"by": "ethan"})
            assert r.status == 404
    _run(go())


def test_zero_limit_means_unlimited(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "u.db"))
    monkeypatch.setenv("LEAGUE_ID", "1")
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AGENT_ASK_DAILY_LIMIT", "0")
    monkeypatch.setenv("AGENT_ASK_LEAGUE_DAILY_LIMIT", "0")
    importlib.reload(db)
    import agent.store as store
    importlib.reload(store)
    store.init()
    from agent import config, server
    app = server.build_app(config.from_env(), _Queue(), _Clock())

    async def go():
        async with TestClient(TestServer(app)) as client:
            await client.post("/users/u9", json={"team_id": 2})
            for _ in range(6):
                r = await client.post("/ask", json={"question": "again?", "discord_user_id": "u9"})
                assert r.status == 200
    _run(go())
    importlib.reload(db)


def test_history_is_rendered_into_the_ask_params(app_env):
    app, queue, store = app_env
    from agent import server
    assert server.render_history(None) == ""
    assert server.render_history([{"role": "user", "text": ""}]) == ""
    text = server.render_history([{"role": "user", "text": "flex?"}, {"role": "analyst", "text": "Start Achane."}])
    assert text.startswith("Earlier in this thread") and "Them: flex?" in text and "You: Start Achane." in text

    async def go():
        async with TestClient(TestServer(app)) as client:
            await client.post("/users/u3", json={"team_id": 4})
            r = await client.post("/ask", json={"question": "why?", "discord_user_id": "u3",
                                                "history": [{"role": "user", "text": "flex?"}, {"role": "analyst", "text": "Start Achane."}]})
            assert r.status == 200
            assert "Start Achane." in queue.submitted[-1][1]["history"]
            r = await client.post("/ask", json={"question": "fresh", "discord_user_id": "u3"})
            assert r.status == 200 and queue.submitted[-1][1]["history"] == ""
    _run(go())


def test_wakeups_route_replans_by_force_and_returns_the_pending_list(app_env):
    app, queue, store = app_env

    async def go():
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/wakeups")
            data = await r.json()
            assert r.status == 200 and data["planned"] == 1
            assert data["wakeups"][0]["label"] == "Thu 08:15 PM ET kickoff"
            assert app["clock"].forced is True
            # Nothing queued: planning is not a model run.
            assert queue.submitted == []
    _run(go())
