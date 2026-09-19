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
    'site' (dashboard prose), or 'analyst' (read minus private state).
    """
    groups = {
        "read": read.NAMES + history.NAMES,
        "write": write.NAMES,
        "state": state.NAMES,
        "site": site.NAMES,
        "analyst": [n for n in read.NAMES if n not in read.PRIVATE] + history.NAMES,
    }
    return [f"mcp__{SERVER_NAME}__{n}" for n in groups[group]]
