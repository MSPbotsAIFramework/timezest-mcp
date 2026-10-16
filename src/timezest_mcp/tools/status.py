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
"""Connection check and tool overview."""

from mcp.server.fastmcp import FastMCP

from ..api_client import TimeZestClient
from ._common import READ_ONLY, ClientFactory, rows, run_tool

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
            }

        return await run_tool("timezest_status", client_factory, action)
