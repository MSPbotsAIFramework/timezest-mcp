# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Connection check, tool overview and the (local) domain navigation tools of earlier versions."""

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import TimeZestClient
from ._common import LOCAL, READ_ONLY, ClientFactory, rows, run_tool

TOOL_GROUPS = {
    "agents": ("Technicians who can be booked.", ["timezest_agents_list", "timezest_agents_get"]),
    "teams": ("Pools of agents booked round-robin.", ["timezest_teams_list", "timezest_teams_get"]),
    "appointment_types": (
        "What can be booked and how long it takes.",
        ["timezest_appointment_types_list", "timezest_appointment_types_get"],
    ),
    "resources": ("Agents and teams together: the ids a booking uses.", ["timezest_resources_list"]),
    "scheduling": (
        "Scheduling requests (bookings).",
        [
            "timezest_scheduling_list",
            "timezest_scheduling_get",
            "timezest_scheduling_create_request",
            "timezest_scheduling_cancel",
        ],
    ),
}
# Kept for callers of earlier versions, which had to "enter" a domain first. Every tool is
# always available now, so these only describe the tools; they never call TimeZest.
NAVIGATION_TOOLS = ["timezest_status", "timezest_navigate", "timezest_back"]
Domain = Literal["agents", "teams", "appointment_types", "resources", "scheduling"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def timezest_status() -> str:
        """Check the TimeZest connection (the API key works) and list the tool groups."""

        async def action(client: TimeZestClient) -> dict:
            types = rows(await client.get("appointment_types"))
            return {
                "connected": True,
                "appointment_types_visible": len(types),
                "tool_groups": {name: {"summary": s, "tools": t} for name, (s, t) in TOOL_GROUPS.items()},
                "navigation_tools": NAVIGATION_TOOLS,
            }

        return await run_tool("timezest_status", client_factory, action)

    @mcp.tool(annotations=LOCAL)
    async def timezest_navigate(
        domain: Annotated[Domain, Field(description="Tool group to describe.")],
    ) -> str:
        """Describe one tool group (domain) and its tool names. No API call.

        Kept for compatibility: every tool is always available, so navigating is optional.
        """
        summary, tools = TOOL_GROUPS[domain]
        return dump_json_capped(
            {
                "domain": domain,
                "summary": summary,
                "tools": tools,
                "note": "All TimeZest tools are always available; call these tools directly.",
            }
        )

    @mcp.tool(annotations=LOCAL)
    async def timezest_back() -> str:
        """Leave a tool group (domain). No API call; kept for compatibility, all tools stay available."""
        return dump_json_capped(
            {
                "message": "There is nothing to go back from: all TimeZest tools are always available.",
                "domains": list(TOOL_GROUPS),
            }
        )
