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
"""Agents, teams, appointment types and resources (all read-only in the API)."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import TimeZestClient
from ._common import READ_ONLY, ClientFactory, Cursor, PageSize, Tql, find_by_id, list_page, run_tool


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def timezest_agents_list(pageSize: PageSize = None, filter: Tql = None, startingAfter: Cursor = None) -> str:
        """List agents (technicians): name, email, role, whether schedulable, URL slug.

        TQL attributes: agent.name, agent.email, agent.role, agent.schedulable, agent.two_factor_enabled.
        """

        async def action(client: TimeZestClient) -> dict:
            return await list_page(client, "agents", filter, startingAfter, pageSize)

        return await run_tool("timezest_agents_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_agents_get(agentId: Annotated[str, Field(description="Agent id (agnt_...).", min_length=1)]) -> str:
        """Get one agent by id."""

        async def action(client: TimeZestClient) -> Any:
            return await find_by_id(client, "agents", agentId, "Agent")

        return await run_tool("timezest_agents_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_teams_list(pageSize: PageSize = None, filter: Tql = None, startingAfter: Cursor = None) -> str:
        """List teams (pools of agents): internal / external name, team type, URL slug.

        TQL attributes: team.internal_name, team.external_name, team.url_slug.
        """

        async def action(client: TimeZestClient) -> dict:
            return await list_page(client, "teams", filter, startingAfter, pageSize)

        return await run_tool("timezest_teams_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_teams_get(teamId: Annotated[str, Field(description="Team id (team_...).", min_length=1)]) -> str:
        """Get one team by id."""

        async def action(client: TimeZestClient) -> Any:
            return await find_by_id(client, "teams", teamId, "Team")

        return await run_tool("timezest_teams_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_appointment_types_list(
        pageSize: PageSize = None, filter: Tql = None, startingAfter: Cursor = None
    ) -> str:
        """List appointment types: internal / external name, default duration in minutes, URL slug.

        TQL attributes: appointment_type.internal_name, .external_name, .duration_mins, .url_slug.
        """

        async def action(client: TimeZestClient) -> dict:
            return await list_page(client, "appointment_types", filter, startingAfter, pageSize)

        return await run_tool("timezest_appointment_types_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_appointment_types_get(
        appointmentTypeId: Annotated[str, Field(description="Appointment type id (apty_...).", min_length=1)],
    ) -> str:
        """Get one appointment type by id."""

        async def action(client: TimeZestClient) -> Any:
            return await find_by_id(client, "appointment_types", appointmentTypeId, "Appointment type")

        return await run_tool("timezest_appointment_types_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_resources_list(
        pageSize: PageSize = None,
        type: Annotated[Literal["agent", "team"] | None, Field(description="Only agents or only teams (filtered per page).")] = None,
        filter: Tql = None,
        startingAfter: Cursor = None,
    ) -> str:
        """List bookable resources (agents and teams together); their ids go into resourceIds.

        TQL attributes: resource.internal_name, resource.external_name, resource.url_slug.
        """

        async def action(client: TimeZestClient) -> dict:
            keep = (lambda r: r.get("object") == type) if type else None
            return await list_page(client, "resources", filter, startingAfter, pageSize, keep)

        return await run_tool("timezest_resources_list", client_factory, action)
