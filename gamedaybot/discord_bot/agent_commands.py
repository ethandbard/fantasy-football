"""
Slash commands and the approval flow for the agent service.

The bot never runs a model. It forwards to the agent's HTTP API
(AGENT_URL, default http://fantasy-agent:8010), returns at once, and lets
the agent post its brief through its own webhook. Two things the bot owns
because they need a gateway connection: posting "ask" messages with reaction
buttons and reading the owner's reaction, and replying to /ask in a thread.
"""
import asyncio
import logging
import os

import discord
from discord import app_commands
import requests

logger = logging.getLogger(__name__)

APPROVE, REJECT = "✅", "❌"
ASK_POLL_SECONDS = 30
RUN_POLL_SECONDS = 5
RUN_WAIT_SECONDS = 15 * 60


class AgentClient:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def _url(self, path):
        return self.base + path

    def get(self, path):
        r = requests.get(self._url(path), timeout=15)
        return r.status_code, _json(r)

    def post(self, path, body=None):
        r = requests.post(self._url(path), json=body or {}, timeout=30)
        return r.status_code, _json(r)


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {"error": r.text[:300]}


def register(tree, bot, agent_url, owner_id, ask_channel_id):
    client = AgentClient(agent_url)

    async def call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except requests.RequestException as e:
            logger.warning("agent service unreachable: %s", e)
            return None, {"error": "the agent service is not reachable right now"}

    def is_owner(user):
        return owner_id and str(user.id) == str(owner_id)

    # ------------------------------------------------------------ /agent

    agent = app_commands.Group(name="agent", description="Ethan's team agents")

    @agent.command(name="status", description="Next wakeups, recent runs, pending approvals")
    async def agent_status(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        status, data = await call(client.get, "/status")
        if status != 200:
            await interaction.followup.send(data.get("error", "status unavailable"))
            return
        lines = []
        canary = data.get("canary") or {}
        lines.append(f"Auth canary: {'ok' if canary.get('ok') else 'FAILED'} ({canary.get('at', 'never')})")
        lines.append(f"Mode: {'dry run' if data.get('dry_run') else 'live writes'}; Claude auth: {'yes' if data.get('claude_auth') else 'MISSING'}")
        if data.get("current"):
            lines.append(f"Running now: {data['current']} (+{data.get('queue', 0)} queued)")
        lines.append("\n**Next wakeups**")
        for w in (data.get("wakeups") or [])[:8]:
            lines.append(f"• {w['run_at']}  {w['job']}  {w.get('label') or ''}")
        for f in (data.get("next_fires") or [])[:6]:
            lines.append(f"• {f['next']}  {f['job']}")
        lines.append("\n**Recent runs**")
        for r in data.get("recent_runs") or []:
            cost = f" ${r['cost_usd']:.2f}" if r.get("cost_usd") else ""
            lines.append(f"• {r['id']} {r['job']} {r['status']} ({r.get('trigger')}){cost}"
                         + (f" — {r['error'][:80]}" if r.get("error") else ""))
        asks = data.get("pending_asks") or []
        if asks:
            lines.append("\n**Pending approvals**")
            for a in asks:
                lines.append(f"• `{a['id']}` {a['description']}")
        await interaction.followup.send("\n".join(lines)[:1990])

    @agent.command(name="research", description="Run the week review and research job now")
    async def agent_research(interaction: discord.Interaction):
        await _run_job(interaction, "research", {})

    @agent.command(name="plan", description="Run the roster plan job now")
    async def agent_plan(interaction: discord.Interaction):
        await _run_job(interaction, "plan", {})

    @agent.command(name="lineup", description="Run a lineup check now")
    async def agent_lineup(interaction: discord.Interaction):
        await _run_job(interaction, "lineup", {})

    @agent.command(name="trade", description="Ask the trade reviewer about an offer, in your own words")
    @app_commands.describe(offer="Describe the trade, e.g. 'my Rice for Chris's Bucky Irving'")
    async def agent_trade(interaction: discord.Interaction, offer: str):
        await _run_job(interaction, "trade_review", {"trade": offer[:1000]})

    async def _run_job(interaction, job, params):
        if not is_owner(interaction.user):
            await interaction.response.send_message("Only the team owner can run the agents.", ephemeral=True)
            return
        await interaction.response.defer()
        status, data = await call(client.post, f"/run/{job}", {"params": params, "trigger": f"discord:{interaction.user.name}"})
        if status != 200:
            await interaction.followup.send(data.get("error", "could not start the job"))
            return
        await interaction.followup.send(f"Started `{job}` as run `{data['run_id']}`. The brief posts here when it finishes.")

    @agent.command(name="approve", description="Approve a pending action by its id")
    async def agent_approve(interaction: discord.Interaction, ask_id: str):
        await _resolve(interaction, ask_id, "approve")

    @agent.command(name="reject", description="Reject a pending action by its id")
    async def agent_reject(interaction: discord.Interaction, ask_id: str):
        await _resolve(interaction, ask_id, "reject")

    async def _resolve(interaction, ask_id, verb):
        if not is_owner(interaction.user):
            await interaction.response.send_message("Only the team owner can approve or reject.", ephemeral=True)
            return
        await interaction.response.defer()
        status, data = await call(client.post, f"/asks/{ask_id}/{verb}", {"by": interaction.user.name})
        await interaction.followup.send(data.get("message") or data.get("error") or f"{verb}d {ask_id}")

    tree.add_command(agent)

    # -------------------------------------------------------------- /ask

    @tree.command(name="ask", description="Ask the league analyst a question about your team")
    @app_commands.describe(question="Your question, e.g. 'who should I start at flex this week?'")
    async def ask(interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        status, user = await call(client.get, f"/users/{interaction.user.id}")
        if status == 404:
            await interaction.followup.send("Claim your team first with `/claim-team`.")
            return
        if status != 200:
            await interaction.followup.send(user.get("error", "the analyst is unavailable"))
            return
        body = {"question": question, "discord_user_id": str(interaction.user.id),
                "display_name": interaction.user.display_name, "team_name": user.get("display_name")}
        status, data = await call(client.post, "/ask", body)
        if status != 200:
            await interaction.followup.send(data.get("error", "could not ask"))
            return
        msg = await interaction.followup.send(f"Working on it. (run `{data['run_id']}`)", wait=True)
        answer = await _wait_for_run(data["run_id"])
        await _reply_in_thread(interaction, msg, question, answer)

    async def _reply_in_thread(interaction, msg, question, answer):
        """
        Thread the answer under the "working on it" message. A deferred
        followup comes back as a webhook message with no guild attached, so
        the thread is created from the channel, not the message object.
        Anything that fails falls back to a plain followup.
        """
        try:
            channel = interaction.channel
            thread = await channel.create_thread(name=question[:90] or "analyst", message=discord.Object(id=msg.id))
            for i in range(0, len(answer), 1900):
                await thread.send(answer[i:i + 1900])
        except Exception:
            logger.exception("thread reply failed; falling back to a followup")
            for i in range(0, len(answer), 1900):
                await interaction.followup.send(answer[i:i + 1900])

    async def _wait_for_run(run_id):
        waited = 0
        while waited < RUN_WAIT_SECONDS:
            await asyncio.sleep(RUN_POLL_SECONDS)
            waited += RUN_POLL_SECONDS
            status, row = await call(client.get, f"/runs/{run_id}")
            if status != 200:
                continue
            if row.get("status") == "done":
                return row.get("result") or "(no answer)"
            if row.get("status") in ("failed", "aborted"):
                return f"The analyst hit an error: {row.get('error', 'unknown')}"
        return "The analyst took too long; try again later."

    @tree.command(name="claim-team", description="Tell the analyst which ESPN team is yours")
    async def claim_team(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        status, teams = await call(client.get, "/teams")
        if status != 200:
            await interaction.followup.send(teams.get("error", "could not load teams"))
            return
        view = _TeamPicker(client, teams, interaction.user)
        await interaction.followup.send("Pick your team:", view=view, ephemeral=True)

    # ---------------------------------------------------- approvals loop

    async def post_pending_asks():
        await bot.wait_until_ready()
        channel = bot.get_channel(int(ask_channel_id)) if ask_channel_id else None
        if channel is None:
            logger.warning("AGENT_CHANNEL_ID not set or not visible; approvals will only work through /agent approve")
        while not bot.is_closed():
            try:
                status, asks = await call(client.get, "/asks/pending")
                if status == 200 and channel is not None:
                    for a in asks:
                        if a.get("discord_message_id"):
                            continue
                        embed = discord.Embed(
                            title=f"Approval needed · {a['kind']}",
                            description=f"**{a['description']}**\n\n{a.get('reason') or ''}\n\n"
                                        f"React {APPROVE} to approve or {REJECT} to reject, or `/agent approve {a['id']}`. "
                                        f"Expires {a['expires_at']}.",
                            color=0xF1C40F,
                        )
                        embed.set_footer(text=f"ask {a['id']}")
                        msg = await channel.send(embed=embed)
                        await msg.add_reaction(APPROVE)
                        await msg.add_reaction(REJECT)
                        await call(client.post, f"/asks/{a['id']}/posted", {"message_id": str(msg.id)})
            except Exception:
                logger.exception("approval poster failed")
            await asyncio.sleep(ASK_POLL_SECONDS)

    @bot.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.user_id == bot.user.id or not is_owner_id(payload.user_id):
            return
        emoji = str(payload.emoji)
        if emoji not in (APPROVE, REJECT):
            return
        status, asks = await call(client.get, "/asks/pending")
        if status != 200:
            return
        ask = next((a for a in asks if str(a.get("discord_message_id")) == str(payload.message_id)), None)
        if ask is None:
            return
        verb = "approve" if emoji == APPROVE else "reject"
        status, data = await call(client.post, f"/asks/{ask['id']}/{verb}", {"by": f"reaction:{payload.user_id}"})
        channel = bot.get_channel(payload.channel_id)
        if channel is not None:
            await channel.send(data.get("message") or data.get("error") or f"{verb}d {ask['id']}")

    def is_owner_id(user_id):
        return owner_id and str(user_id) == str(owner_id)

    return post_pending_asks


class _TeamPicker(discord.ui.View):
    def __init__(self, client, teams, user):
        super().__init__(timeout=120)
        self.client = client
        self.user = user
        options = [discord.SelectOption(label=t["name"][:100], value=str(t["team_id"]),
                                        description=(t.get("owner") or "")[:100] or None) for t in teams[:25]]
        select = discord.ui.Select(placeholder="Your team", options=options)
        select.callback = self.pick
        self.add_item(select)
        self.select = select

    async def pick(self, interaction: discord.Interaction):
        team_id = int(self.select.values[0])
        label = next((o.label for o in self.select.options if o.value == str(team_id)), str(team_id))
        await asyncio.to_thread(self.client.post, f"/users/{self.user.id}", {"team_id": team_id, "display_name": label})
        await interaction.response.edit_message(content=f"Claimed **{label}**. You can use `/ask` now.", view=None)
