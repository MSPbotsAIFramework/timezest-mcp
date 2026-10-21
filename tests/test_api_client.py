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
"""Request shapes, paging, lookups and retries against a mocked TimeZest API."""

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from timezest_mcp import api_client
from timezest_mcp.api_client import TimeZestClient, TimeZestCredentials, TimeZestError
from timezest_mcp.server import _credentials_var, create_mcp_server

CREDS = TimeZestCredentials(api_token="token-123")
BASE = "https://api.timezest.com/v1"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


def page(items, next_page=None):
    return {"object": "list", "next_page": next_page, "previous_page": None, "data": items}


class Recorder:
    """Mock transport: replies by path below /v1/."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else page([])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1/")
        reply = self.routes.get(f"{request.method} {path}", self.routes.get(path, self.default))
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)


def body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(api_client, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


def _client(recorder: Recorder) -> TimeZestClient:
    return TimeZestClient(CREDS, httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


# ---- client ----------------------------------------------------------------


async def test_bearer_token_and_url():
    rec = Recorder()
    await _client(rec).get("agents", {"filter": "agent.name LIKE smith", "starting_after": None})
    request = rec.requests[0]
    assert request.headers["Authorization"] == "Bearer token-123"
    assert request.url.path == "/v1/agents"
    assert request.url.params["filter"] == "agent.name LIKE smith"
    assert "starting_after" not in request.url.params


async def test_error_message_from_api():
    rec = Recorder({"agents": lambda r: httpx.Response(400, json={"error": "Invalid TQL: unknown attribute."})})
    with pytest.raises(TimeZestError) as err:
        await _client(rec).get("agents")
    assert err.value.status_code == 400
    assert "Invalid TQL" in err.value.message


async def test_get_retries_429_then_succeeds():
    replies = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json=page([]))])
    rec = Recorder({"agents": lambda r: next(replies)})
    assert await _client(rec).get("agents") == page([])
    assert len(rec.requests) == 2


async def test_post_is_not_retried_on_5xx():
    rec = Recorder({"POST scheduling_requests": lambda r: httpx.Response(502, json={"error": "bad gateway"})})
    with pytest.raises(TimeZestError):
        await _client(rec).post("scheduling_requests", {})
    assert len(rec.requests) == 1


async def test_bad_token_is_unauthorized(monkeypatch):
    rec = Recorder({"appointment_types": lambda r: httpx.Response(401, json={"error": "Unauthorized. No valid API key provided"})})
    result = await _call(monkeypatch, rec, "timezest_status", {})
    assert result["error"]["code"] == "unauthorized"
    assert "token-123" not in json.dumps(result)


# ---- lists and lookups -----------------------------------------------------


async def test_list_returns_cursor_and_iso_timestamps(monkeypatch):
    agents = [{"id": "agnt_1", "created_at": 1692172402}, {"id": "agnt_2", "created_at": 1692172402}]
    rec = Recorder({"agents": page(agents, next_page=f"{BASE}/agents?starting_after=agnt_2")})
    result = await _call(monkeypatch, rec, "timezest_agents_list", {"filter": "agent.schedulable EQ true"})
    assert result["items"][0]["created_at"] == "2023-08-16T07:53:22Z"
    assert (result["count"], result["has_more"], result["next_cursor"]) == (2, True, "agnt_2")


async def test_page_size_truncates_and_cursor_follows_last_returned(monkeypatch):
    rec = Recorder({"agents": page([{"id": f"agnt_{i}"} for i in range(5)])})
    result = await _call(monkeypatch, rec, "timezest_agents_list", {"pageSize": 2, "startingAfter": "agnt_x"})
    assert rec.requests[0].url.params["starting_after"] == "agnt_x"
    assert [a["id"] for a in result["items"]] == ["agnt_0", "agnt_1"]
    assert result["next_cursor"] == "agnt_1"


async def test_last_page_has_no_cursor(monkeypatch):
    rec = Recorder({"teams": page([{"id": "team_1"}])})
    result = await _call(monkeypatch, rec, "timezest_teams_list", {})
    assert result["has_more"] is False and "next_cursor" not in result


async def test_resources_type_filter(monkeypatch):
    rec = Recorder({"resources": page([{"id": "agnt_1", "object": "agent"}, {"id": "team_1", "object": "team"}])})
    result = await _call(monkeypatch, rec, "timezest_resources_list", {"type": "team"})
    assert [r["id"] for r in result["items"]] == ["team_1"]


async def test_get_walks_pages_until_found(monkeypatch):
    def agents(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("starting_after") == "agnt_2":
            return httpx.Response(200, json=page([{"id": "agnt_3", "name": "Teresa"}]))
        return httpx.Response(200, json=page([{"id": "agnt_1"}, {"id": "agnt_2"}], next_page=f"{BASE}/agents?starting_after=agnt_2"))

    rec = Recorder({"agents": agents})
    result = await _call(monkeypatch, rec, "timezest_agents_get", {"agentId": "agnt_3"})
    assert result == {"id": "agnt_3", "name": "Teresa"}
    assert len(rec.requests) == 2


async def test_get_missing_is_not_found(monkeypatch):
    rec = Recorder({"appointment_types": page([{"id": "apty_1"}])})
    result = await _call(monkeypatch, rec, "timezest_appointment_types_get", {"appointmentTypeId": "apty_9"})
    assert result["error"]["code"] == "not_found"


# ---- scheduling ------------------------------------------------------------


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"status": "booked"}, "scheduling_request.status EQ scheduled"),
        ({"status": "sent", "filter": "scheduling_request.end_user_email LIKE @example.com"},
         "scheduling_request.end_user_email LIKE @example.com AND scheduling_request.status EQ sent"),
    ],
)
async def test_scheduling_list_status_becomes_tql(monkeypatch, args, expected):
    rec = Recorder()
    await _call(monkeypatch, rec, "timezest_scheduling_list", args)
    assert rec.requests[0].url.params["filter"] == expected


async def test_scheduling_get(monkeypatch):
    rec = Recorder({"scheduling_requests/sreq_1": {"id": "sreq_1", "status": "scheduled", "selected_start_time": 1692363445}})
    result = await _call(monkeypatch, rec, "timezest_scheduling_get", {"requestId": "sreq_1"})
    assert result["selected_start_time"] == "2023-08-18T12:57:25Z"


async def test_create_request_body(monkeypatch):
    rec = Recorder({"POST scheduling_requests": {"id": "sreq_9", "scheduling_url": "https://example.timezest.com/schedule/x"}})
    args = {
        "appointmentTypeId": "apty_1",
        "triggerMode": "pod",
        "endUser": {"name": "Rodney Smith", "email": "rodney@example.com", "phone": "555"},
        "timeRange": {"earliestDate": "2026-06-01", "earliestTime": "9:00", "latestDate": "2026-06-05", "latestTime": "16:30", "timezone": "UTC"},
        "resourceIds": ["agnt_1", " team_2 "],
        "associatedEntities": [{"type": "connectwise_psa/service_ticket", "id": "1234", "number": "#1234"}],
        "durationMins": 45,
        "guests": ["boss@example.com"],
    }
    result = await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    assert body(rec.requests[0]) == {
        "appointment_type_id": "apty_1",
        "trigger_mode": "pod",
        "resource_ids": ["agnt_1", "team_2"],
        "end_user_name": "Rodney Smith",
        "end_user_email": "rodney@example.com",
        "earliest_date": "2026-06-01",
        "earliest_time": "09:00:00",
        "latest_date": "2026-06-05",
        "latest_time": "16:30:00",
        "associated_entities": [{"type": "connectwise_psa/service_ticket", "id": 1234, "number": "#1234"}],
        "duration_mins": 45,
        "guests_list": ["boss@example.com"],
    }
    assert result["id"] == "sreq_9" and "note" in result


async def test_create_always_sends_resource_ids_array(monkeypatch):
    rec = Recorder({"POST scheduling_requests": {"id": "sreq_9"}})
    await _call(monkeypatch, rec, "timezest_scheduling_create_request", {"appointmentTypeId": "apty_1", "triggerMode": "generate_url", "endUser": {"name": "A"}})
    assert body(rec.requests[0])["resource_ids"] == []


@pytest.mark.parametrize(
    "extra",
    [
        {"associatedEntities": [{"type": "connectwise", "id": "1"}]},
        {"associatedEntities": [{"type": "autotask/ticket"}]},
        {"timeRange": {"earliestDate": "01/06/2026"}},
        {"timeRange": {"latestTime": "25:00"}},
    ],
)
async def test_create_rejects_bad_values_before_calling(monkeypatch, extra):
    rec = Recorder()
    args = {"appointmentTypeId": "apty_1", "triggerMode": "pod", "endUser": {"name": "A"}} | extra
    result = await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_create_without_appointment_type_lists_the_types(monkeypatch):
    types = page([{"id": "apty_1", "name": "Remote session"}, {"id": "apty_2", "name": "Onsite visit"}])
    rec = Recorder({"appointment_types": types})
    args = {"triggerMode": "pod", "endUser": {"name": "A"}}
    result = await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    assert result["error"]["code"] == "invalid_argument"
    message = result["error"]["message"]
    assert "appointmentTypeId" in message
    assert "apty_1 (Remote session)" in message and "apty_2 (Onsite visit)" in message
    assert [r.method for r in rec.requests] == ["GET"]


async def test_create_accepts_and_ignores_notes(monkeypatch):
    rec = Recorder({"POST scheduling_requests": {"id": "sreq_9"}})
    args = {"appointmentTypeId": "apty_1", "triggerMode": "pod", "endUser": {"name": "A"}, "notes": "Bring the laptop"}
    result = await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    assert "notes" not in body(rec.requests[0]) and "Bring the laptop" not in rec.requests[0].content.decode()
    assert result["id"] == "sreq_9" and "notes_ignored" in result


async def _call_local(tool: str, args: dict):
    content, _ = await create_mcp_server().call_tool(tool, args)
    return json.loads(content[0].text)


async def test_navigate_is_local_and_lists_domain_tools():
    result = await _call_local("timezest_navigate", {"domain": "scheduling"})
    assert result["domain"] == "scheduling"
    assert "timezest_scheduling_create_request" in result["tools"]
    assert result["summary"]


async def test_navigate_rejects_unknown_domain():
    with pytest.raises(ToolError):
        await _call_local("timezest_navigate", {"domain": "billing"})


async def test_back_says_all_tools_are_available():
    result = await _call_local("timezest_back", {})
    assert "always available" in result["message"]


async def test_cancel_changes_nothing_and_points_to_the_url(monkeypatch):
    rec = Recorder({"scheduling_requests/sreq_1": {"id": "sreq_1", "status": "sent", "scheduling_url": "https://example.timezest.com/schedule/x"}})
    result = await _call(monkeypatch, rec, "timezest_scheduling_cancel", {"requestId": "sreq_1", "reason": "duplicate"})
    assert result["cancelled"] is False
    assert result["scheduling_url"] == "https://example.timezest.com/schedule/x"
    assert [r.method for r in rec.requests] == ["GET"]


# ---- compatibility: object or JSON-text arguments -------------------------------------------


@pytest.mark.parametrize(
    "end_user,time_range",
    [
        ({"name": "Rodney Smith", "email": "rodney@example.com"}, {"earliestDate": "2026-06-01", "latestTime": "16:30"}),
        ('{"name": "Rodney Smith", "email": "rodney@example.com"}', '{"earliestDate": "2026-06-01", "latestTime": "16:30"}'),
    ],
)
async def test_create_accepts_objects_or_json_text(monkeypatch, end_user, time_range):
    rec = Recorder({"POST scheduling_requests": {"id": "sreq_9"}})
    args = {"appointmentTypeId": "apty_1", "triggerMode": "pod", "endUser": end_user, "timeRange": time_range}
    await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    sent = body(rec.requests[0])
    assert sent["end_user_name"] == "Rodney Smith" and sent["end_user_email"] == "rodney@example.com"
    assert sent["earliest_date"] == "2026-06-01" and sent["latest_time"] == "16:30:00"


async def test_create_accepts_plain_end_user_name(monkeypatch):
    rec = Recorder({"POST scheduling_requests": {"id": "sreq_9"}})
    args = {"appointmentTypeId": "apty_1", "triggerMode": "pod", "endUser": " Rodney Smith "}
    await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    assert body(rec.requests[0])["end_user_name"] == "Rodney Smith"


@pytest.mark.parametrize("extra", [{"timeRange": "next week"}, {"timeRange": "{not json"}])
async def test_create_rejects_unreadable_text_before_calling(monkeypatch, extra):
    rec = Recorder()
    args = {"appointmentTypeId": "apty_1", "triggerMode": "pod", "endUser": {"name": "A"}} | extra
    result = await _call(monkeypatch, rec, "timezest_scheduling_create_request", args)
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []
