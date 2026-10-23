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
"""FastMCP factory and the per-request credential middleware."""

import contextvars
import re
import uuid
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ._arguments import EnvelopeFastMCP
from .api_client import TimeZestClient, TimeZestCredentials
from .config import Settings

HEADER_API_TOKEN = "X-Timezest-Api-Token"

_PLACEHOLDER = re.compile(r"^\$\{.*\}$")
_BEARER = re.compile(r"^bearer(\s+|$)", re.IGNORECASE)

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level.
_credentials_var: contextvars.ContextVar[TimeZestCredentials | None] = contextvars.ContextVar(
    "timezest_credentials", default=None
)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("timezest_request_id", default="-")

INSTRUCTIONS = (
    "TimeZest books appointments between an MSP's technicians and the MSP's "
    "customers: it emails the end user a link, they pick a time, and the "
    "appointment lands on the technician's calendar and the PSA ticket.\n"
    "Concepts: agents (technicians), teams (pools of agents), resources "
    "(agents + teams, the ids a booking uses), appointment types (what is "
    "booked and its duration), scheduling requests (one booking, with status "
    "new / sent / scheduled / cancelled / closed).\n"
    "Usage:\n"
    "- To book: timezest_appointment_types_list -> timezest_resources_list "
    "-> timezest_scheduling_create_request (appointmentTypeId, triggerMode, "
    "endUser, resourceIds, optional PSA ticket in associatedEntities).\n"
    "- To check bookings: timezest_scheduling_list(status or filter) -> "
    "timezest_scheduling_get.\n"
    "- Lists come newest first in pages of 20; pass next_cursor as "
    "startingAfter for the next page. filter takes TimeZest TQL, e.g. "
    "scheduling_request.end_user_email LIKE @example.com.\n"
    "- Timestamps are returned as ISO 8601 UTC.\n"
    "Creating a scheduling request emails the customer and can update the PSA "
    "ticket: call it only when the user explicitly asks."
)


def get_client_from_context() -> TimeZestClient | None:
    """The client for the current request, or None outside a request."""
    credentials = _credentials_var.get()
    if credentials is None:
        return None
    return TimeZestClient(credentials)


def _clean_token(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    text = _BEARER.sub("", text).strip()
    return text or None


class GatewayCredentialsMiddleware:
    """Reads X-Timezest-Api-Token on /mcp requests into the request context; 401 without it."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = _clean_token(request.headers.get(HEADER_API_TOKEN))
        if not token:
            await JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": f"Send the TimeZest API key in the {HEADER_API_TOKEN} header",
                    "required_headers": [HEADER_API_TOKEN],
                },
                status_code=401,
            )(scope, receive, send)
            return

        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds_token = _credentials_var.set(TimeZestCredentials(api_token=token))
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    mcp = EnvelopeFastMCP(
        name="timezest-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], TimeZestClient | None] = get_client_from_context

    from .tools import directory, scheduling, status

    for module in (status, directory, scheduling):
        module.register(mcp, client_factory)

    return mcp
