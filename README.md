# TimeZest MCP Server

An [MCP](https://modelcontextprotocol.io) server for the [TimeZest](https://www.timezest.com)
API, the appointment-scheduling service MSPs use so their customers can book time with
technicians from a PSA ticket. It lets an AI agent read agents, teams, appointment types,
bookable resources and scheduling requests and, on explicit request, create a scheduling
request (optionally linked to a PSA ticket).

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: the API key arrives on every request as an HTTP header and is
  never stored; every tool is listed from the first `tools/list`
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-Timezest-Api-Token` | Yes | string | none | - | TimeZest API key (a leading `Bearer ` is accepted and ignored) | `your-api-token` |

Where to get it:

- A TimeZest administrator creates an API key on the **API Keys** page of the TimeZest web app
  (`app.timezest.com` -> Settings -> API Keys).
- A **read-only** key is enough for every tool except `timezest_scheduling_create_request`,
  which needs a **read/write** key.
- TimeZest allows 180 requests per minute per account (all keys combined).

How the key is sent to TimeZest: `Authorization: Bearer <key>` on every call to
`https://api.timezest.com/v1/...`.

A request to `/mcp` without the header gets HTTP 401 naming it.

## Tools

All tools return a JSON string. TimeZest returns lists newest first in fixed pages of 20; list
tools return `{items, count, has_more, next_cursor}` and take `startingAfter` (the previous
`next_cursor`), `pageSize` (return at most this many of the page) and `filter`, a
[TimeZest Query Language](https://developer.timezest.com/tql/) expression such as
`scheduling_request.end_user_email LIKE @example.com`. Timestamps are returned as ISO 8601 UTC.

| Tool | Purpose | Parameters (required in **bold**) |
|---|---|---|
| `timezest_status` | Check the connection (the API key works) and list the tool groups | - |
| `timezest_navigate` | Describe one tool group and its tool names (local, no API call; kept for compatibility) | **domain** (`agents`, `teams`, `appointment_types`, `resources`, `scheduling`) |
| `timezest_back` | Says all tools are always available (local, no API call; kept for compatibility) | - |
| `timezest_agents_list` | List agents (technicians): name, email, role, whether schedulable | pageSize, filter, startingAfter |
| `timezest_agents_get` | One agent | **agentId** |
| `timezest_teams_list` | List teams (pools of agents) and their type | pageSize, filter, startingAfter |
| `timezest_teams_get` | One team | **teamId** |
| `timezest_appointment_types_list` | List appointment types with default duration | pageSize, filter, startingAfter |
| `timezest_appointment_types_get` | One appointment type | **appointmentTypeId** |
| `timezest_resources_list` | List bookable resources (agents and teams); their ids go into `resourceIds` | pageSize, type (`agent`, `team`), filter, startingAfter |
| `timezest_scheduling_list` | List scheduling requests: end user, type, status, chosen time, URL | pageSize, status (`new`, `sent`, `scheduled`, `cancelled`, `closed`; also `pending`, `booked`, `completed`), filter, startingAfter |
| `timezest_scheduling_get` | One scheduling request with resources, booked agents and booking URL | **requestId** |
| `timezest_scheduling_create_request` | Create a scheduling request; TimeZest emails the customer (**write**) | appointmentTypeId (needed at runtime), **triggerMode** (`pod`, `generate_url`), **endUser** {**name**, email, company}, timeRange {earliestDate, earliestTime, latestDate, latestTime}, resourceIds, associatedEntities [{**type**, id, number}], durationMins, guests, notes (ignored) |
| `timezest_scheduling_cancel` | Explain how to cancel a request (the API cannot cancel); returns its status and URL | **requestId**, reason |

`associatedEntities[].type` is one of `autotask/company`, `autotask/contact`, `autotask/ticket`,
`connectwise_psa/company`, `connectwise_psa/contact`, `connectwise_psa/project_ticket`,
`connectwise_psa/service_ticket`, `halo_psa/user`, `halo_psa/ticket`, `service_now/incident`,
`service_now/problem`, `service_now/change_request`, `service_now/sn_customerservice_case`,
`service_now/contact`. When a PSA contact or ticket is linked, TimeZest can take the end
user's name and email from it.

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "unauthorized", "message": "TimeZest returned HTTP 401 for agents: Unauthorized. No valid API key provided", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`. Reads are retried with backoff on 429, 5xx and network
errors; the create call is retried only on 429, so a request is never created twice.

## Run locally

```bash
uv sync
uv run python -m timezest_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t timezest-mcp:dev .
docker run --rm -p 8080:8080 timezest-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`). The key is never read from the
environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Timezest-Api-Token: your-api-token" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# bookings waiting for the customer to pick a time
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Timezest-Api-Token: your-api-token" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"timezest_scheduling_list","arguments":{"status":"sent"}}}'
```

Unit tests (no network access needed):

```bash
uv run pytest
```

## Delivery notes

- **Tool count (14).** Kept as is because the tool names are an existing contract.
  `timezest_navigate` and `timezest_back` come from earlier versions, where tools had to be
  unlocked per domain; they are purely local (no TimeZest call, `openWorldHint` false) and only
  describe the tool groups, since every tool is always listed.
- **Cancelling.** The TimeZest API has no cancel operation, so `timezest_scheduling_cancel`
  changes nothing: it returns the request's status and `scheduling_url` (the customer's
  reschedule / cancel page) and tells the agent to have it cancelled in TimeZest or the PSA.
- **Create request.** TimeZest needs `appointmentTypeId` (older versions silently picked the
  first type). It is optional in the schema for compatibility with earlier versions; a call
  without it returns `invalid_argument` listing the available appointment types (id and name)
  instead of guessing. `resource_ids` is always sent as an array. Associated entities use
  TimeZest's own type names (for example `connectwise_psa/service_ticket`) and times are sent as
  `HH:MM:SS`. `notes` is accepted for compatibility but not sent (the TimeZest API does not
  accept it; the result says `notes_ignored`); end-user `phone` and time zone are not sent either.
- **Contract test.** `tests/contract_baseline.json` holds the deployed and upstream tool schemas;
  `tests/test_contract.py` checks every tool still exists, accepts every parameter of both and
  requires nothing the deployed version did not.
- **Lookups by id.** TimeZest has no get-by-id for agents, teams or appointment types, so the
  `_get` tools walk the list pages (at most 50 pages) to find the id.
- **No interactive prompts.** Older versions asked the user to pick an appointment type or
  confirm a cancel mid-call, which a stateless gateway cannot relay.

## License

Apache License 2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
