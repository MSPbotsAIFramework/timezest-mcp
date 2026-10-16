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
"""Scheduling requests: list, get, create, and how to cancel one."""

import re
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..api_client import TimeZestClient
from ._common import READ_ONLY, WRITES, ClientFactory, Cursor, PageSize, Tql, compact, list_page, readable, run_tool

STATUSES = ("new", "sent", "scheduled", "cancelled", "closed")
# Older names some callers still use.
STATUS_ALIASES = {"pending": "sent", "booked": "scheduled", "completed": "closed"}
Status = Literal["new", "sent", "scheduled", "cancelled", "closed", "pending", "booked", "completed"]

ENTITY_TYPES = (
    "autotask/company",
    "autotask/contact",
    "autotask/ticket",
    "connectwise_psa/company",
    "connectwise_psa/contact",
    "connectwise_psa/project_ticket",
    "connectwise_psa/service_ticket",
    "halo_psa/user",
    "halo_psa/ticket",
    "service_now/incident",
    "service_now/problem",
    "service_now/change_request",
    "service_now/sn_customerservice_case",
    "service_now/contact",
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
RequestId = Annotated[str, Field(description="Scheduling request id (sreq_...).", min_length=1)]


class EndUser(BaseModel):
    name: str = Field(description="Customer name.")
    email: str | None = Field(default=None, description="Customer email (needed unless a PSA contact / ticket supplies it).")
    company: str | None = Field(default=None, description="Customer company.")


class TimeRange(BaseModel):
    earliestDate: str | None = Field(default=None, description="Earliest date, YYYY-MM-DD.")
    earliestTime: str | None = Field(default=None, description="Earliest time of day, HH:MM.")
    latestDate: str | None = Field(default=None, description="Latest date, YYYY-MM-DD.")
    latestTime: str | None = Field(default=None, description="Latest time of day, HH:MM.")


class AssociatedEntity(BaseModel):
    type: str = Field(description="Entity type, e.g. connectwise_psa/service_ticket, autotask/ticket, halo_psa/ticket.")
    id: str | int | None = Field(default=None, description="The entity's id in the PSA API (not the ticket number).")
    number: str | None = Field(default=None, description="Ticket number as shown in the PSA, e.g. #1234.")


def status_clause(status: str | None) -> str | None:
    if not status:
        return None
    value = STATUS_ALIASES.get(status.strip().lower(), status.strip().lower())
    return f"scheduling_request.status EQ {value}"


def _date(value: str | None, name: str) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not _DATE.match(text):
        raise ValueError(f"{name} must be YYYY-MM-DD (got {value!r}).")
    return text


def _time(value: str | None, name: str) -> str | None:
    if not value:
        return None
    match = _TIME.match(value.strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"{name} must be HH:MM (got {value!r}).")
    return f"{int(match.group(1)):02d}:{match.group(2)}:{match.group(3) or '00'}"


def _entity(entity: AssociatedEntity) -> dict:
    kind = entity.type.strip()
    if kind not in ENTITY_TYPES:
        raise ValueError(f"Unknown associated entity type {kind!r}; use one of: {', '.join(ENTITY_TYPES)}.")
    if entity.id is None and not entity.number:
        raise ValueError(f"Associated entity {kind} needs an id or a number.")
    ident: Any = entity.id
    if isinstance(ident, str):
        ident = int(ident) if ident.strip().isdigit() else ident.strip()
    return compact({"type": kind, "id": ident, "number": entity.number})


def build_request_body(
    appointment_type_id: str,
    trigger_mode: str,
    end_user: EndUser,
    time_range: TimeRange | None,
    resource_ids: list[str] | None,
    associated_entities: list[AssociatedEntity] | None,
    duration_mins: int | None,
    guests: list[str] | None,
) -> dict:
    window = time_range or TimeRange()
    return compact(
        {
            "appointment_type_id": appointment_type_id.strip(),
            "trigger_mode": trigger_mode,
            # TimeZest requires resource_ids to be an array, even an empty one.
            "resource_ids": [r.strip() for r in resource_ids or [] if r and r.strip()],
            "end_user_name": end_user.name.strip() or None,
            "end_user_email": (end_user.email or "").strip() or None,
            "end_user_company": (end_user.company or "").strip() or None,
            "earliest_date": _date(window.earliestDate, "timeRange.earliestDate"),
            "earliest_time": _time(window.earliestTime, "timeRange.earliestTime"),
            "latest_date": _date(window.latestDate, "timeRange.latestDate"),
            "latest_time": _time(window.latestTime, "timeRange.latestTime"),
            "associated_entities": [_entity(e) for e in associated_entities] if associated_entities else None,
            "duration_mins": duration_mins,
            "guests_list": [g.strip() for g in guests if g.strip()] if guests else None,
        }
    )


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def timezest_scheduling_list(
        pageSize: PageSize = None,
        status: Annotated[
            Status | None,
            Field(description="Only this status (pending = sent, booked = scheduled, completed = closed)."),
        ] = None,
        filter: Tql = None,
        startingAfter: Cursor = None,
    ) -> str:
        """List scheduling requests, newest first: end user, appointment type, status, chosen time, URL.

        TQL attributes include scheduling_request.end_user_email, .end_user_name, .status,
        .selected_start_time, .created_at, .agent_id, .team_id, .connectwise_psa_ticket_number,
        .autotask_ticket_number, .halo_psa_ticket_number.
        """

        async def action(client: TimeZestClient) -> dict:
            clauses = [c for c in ((filter or "").strip(), status_clause(status)) if c]
            return await list_page(client, "scheduling_requests", " AND ".join(clauses) or None, startingAfter, pageSize)

        return await run_tool("timezest_scheduling_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_scheduling_get(requestId: RequestId) -> str:
        """Get one scheduling request: status, end user, resources, booked agents, chosen time and booking URL."""

        async def action(client: TimeZestClient) -> Any:
            return readable(await client.get(f"scheduling_requests/{requestId.strip()}"))

        return await run_tool("timezest_scheduling_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def timezest_scheduling_create_request(
        appointmentTypeId: Annotated[str, Field(description="Appointment type id (apty_...).", min_length=1)],
        triggerMode: Annotated[
            Literal["pod", "generate_url"],
            Field(description="pod runs the normal scheduling workflow (emails the end user); generate_url runs the Generate URL workflow."),
        ],
        endUser: Annotated[EndUser, Field(description="The customer who picks the time.")],
        timeRange: Annotated[TimeRange | None, Field(description="Window the customer may pick from.")] = None,
        resourceIds: Annotated[list[str] | None, Field(description="Agent / team ids to book (from timezest_resources_list).")] = None,
        associatedEntities: Annotated[
            list[AssociatedEntity] | None, Field(description="PSA records to link, e.g. the service ticket.")
        ] = None,
        durationMins: Annotated[int | None, Field(description="Override the appointment type's duration.", ge=5, le=1440)] = None,
        guests: Annotated[list[str] | None, Field(description="Extra guest email addresses.")] = None,
    ) -> str:
        """Create a scheduling request; TimeZest emails the customer and can update the PSA ticket.

        Only when the user explicitly asks.
        """

        async def action(client: TimeZestClient) -> Any:
            body = build_request_body(
                appointmentTypeId, triggerMode, endUser, timeRange, resourceIds, associatedEntities, durationMins, guests
            )
            created = readable(await client.post("scheduling_requests", body))
            if isinstance(created, dict) and created.get("scheduling_url"):
                created["note"] = "Share scheduling_url with the customer if TimeZest does not email it."
            return created

        return await run_tool("timezest_scheduling_create_request", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def timezest_scheduling_cancel(
        requestId: RequestId,
        reason: Annotated[str | None, Field(description="Why it should be cancelled (for the person who cancels it).")] = None,
    ) -> str:
        """Explain how to cancel a scheduling request: the TimeZest API cannot cancel one.

        Returns the request's status and its scheduling URL, where the customer can cancel, so the
        user can cancel it there or in TimeZest. Nothing is changed.
        """

        async def action(client: TimeZestClient) -> dict:
            current = readable(await client.get(f"scheduling_requests/{requestId.strip()}"))
            current = current if isinstance(current, dict) else {}
            return compact(
                {
                    "cancelled": False,
                    "request_id": requestId.strip(),
                    "status": current.get("status"),
                    "scheduling_url": current.get("scheduling_url"),
                    "reason": reason,
                    "message": (
                        "The TimeZest API has no cancel operation. Cancel it in the TimeZest app, from the PSA "
                        "ticket's TimeZest pod, or with the scheduling_url (the customer's reschedule / cancel page). "
                        "Do not tell the user it was cancelled."
                    ),
                }
            )

        return await run_tool("timezest_scheduling_cancel", client_factory, action)
