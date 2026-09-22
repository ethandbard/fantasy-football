"""
In-process MCP tools the model calls. Each job picks a subset by name.

Names are what the model sees as mcp__espn__<name>. Keep them stable: the
prompts under agent/prompts refer to them.
"""
from claude_agent_sdk import create_sdk_mcp_server

from agent.tools import history, read, site, state, write

SERVER_NAME = "espn"


def build_server(ctx, run):
    tools = (read.build(ctx, run) + history.build(ctx, run) + write.build(ctx, run)
             + state.build(ctx, run) + site.build(ctx, run))
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)


def tool_names(group):
    """
    Tool names for a group: 'read' (with league history), 'write', 'state',
    'site' (dashboard prose), 'analyst' (read minus private state), or
    'owner' (just the private state, for the analyst when the owner asks).
    """
    groups = {
        "read": read.NAMES + history.NAMES,
        "write": write.NAMES,
        "state": state.NAMES,
        "site": site.NAMES,
        "analyst": [n for n in read.NAMES if n not in read.PRIVATE] + history.NAMES,
        "owner": list(read.PRIVATE),
    }
    return [f"mcp__{SERVER_NAME}__{n}" for n in groups[group]]


def groups_for(job, spec, owner):
    """
    The tool groups one run gets. The analyst answers the whole league with
    public tools; when the person asking owns the managed team, the private
    record (research, state, season log, briefs, rules) opens up as well.
    No other job changes with who triggered it.
    """
    groups = list(spec.tool_groups)
    if job == "ask" and owner:
        groups.append("owner")
    return groups
