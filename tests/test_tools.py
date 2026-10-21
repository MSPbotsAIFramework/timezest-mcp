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
"""tools/list snapshot and error-envelope mapping. No network access."""

import asyncio
import json

import pytest

from timezest_mcp.api_client import TimeZestError
from timezest_mcp.server import INSTRUCTIONS, create_mcp_server

# Tool names are a public contract: existing agents and tenant connections
# depend on them. Changing this snapshot is a breaking change.
EXPECTED_TOOLS: dict[str, set[str]] = {
    "timezest_status": set(),
    "timezest_navigate": {"domain"},
    "timezest_back": set(),
    "timezest_agents_list": set(),
    "timezest_agents_get": {"agentId"},
    "timezest_teams_list": set(),
    "timezest_teams_get": {"teamId"},
    "timezest_appointment_types_list": set(),
    "timezest_appointment_types_get": {"appointmentTypeId"},
    "timezest_resources_list": set(),
    "timezest_scheduling_list": set(),
    "timezest_scheduling_get": {"requestId"},
    # appointmentTypeId is checked at runtime (earlier versions did not require it).
    "timezest_scheduling_create_request": {"triggerMode", "endUser"},
    "timezest_scheduling_cancel": {"requestId"},
}
WRITE_TOOLS = {"timezest_scheduling_create_request"}


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_tool_names_are_stable(tools):
    assert set(tools) == set(EXPECTED_TOOLS)


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_required_params(tools, name):
    assert set(tools[name].inputSchema.get("required", [])) == EXPECTED_TOOLS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_description_budget(tools, name):
    description = (tools[name].description or "").strip()
    assert description, f"{name} has no description"
    assert len(description) <= 500
    assert len(description.splitlines()[0]) <= 100


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_annotations_match_side_effects(tools, name):
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is (name not in WRITE_TOOLS)
    assert not annotations.destructiveHint


@pytest.mark.parametrize("name", ["timezest_navigate", "timezest_back"])
def test_navigation_tools_are_local(tools, name):
    annotations = tools[name].annotations
    assert annotations.readOnlyHint is True and annotations.openWorldHint is False


def test_navigate_domains_match_tool_groups(tools):
    from timezest_mcp.tools.status import NAVIGATION_TOOLS, TOOL_GROUPS

    assert set(tools["timezest_navigate"].inputSchema["properties"]["domain"]["enum"]) == set(TOOL_GROUPS)
    listed = {t for _, group in TOOL_GROUPS.values() for t in group} | set(NAVIGATION_TOOLS)
    assert listed == set(tools)


def test_create_says_when_to_use_it(tools):
    assert "explicitly asks" in tools["timezest_scheduling_create_request"].description


def test_cancel_does_not_claim_to_cancel(tools):
    assert "cannot cancel" in tools["timezest_scheduling_cancel"].description


def test_end_user_name_is_required(tools):
    schema = tools["timezest_scheduling_create_request"].inputSchema
    end_user = schema["$defs"]["EndUser"]
    assert end_user["required"] == ["name"]


def test_instructions_budget():
    assert len(INSTRUCTIONS) <= 1500


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (0, "upstream_error", True),
    ],
)
def test_error_envelope(status, code, retryable):
    envelope = json.loads(TimeZestError(status, "boom").to_envelope())
    assert envelope == {"error": {"code": code, "message": "boom", "retryable": retryable}}


def test_tool_without_credentials_returns_not_configured():
    content, _ = asyncio.run(create_mcp_server().call_tool("timezest_status", {}))
    assert json.loads(content[0].text)["error"]["code"] == "not_configured"
