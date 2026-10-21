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
"""Shared argument types, list / lookup helpers and the error-handling wrapper."""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, TypeVar

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .._json import dump_json_capped, error_envelope
from ..api_client import TimeZestClient, TimeZestError
from ..config import API_PAGE_SIZE, MAX_LOOKUP_PAGES

logger = logging.getLogger("timezest_mcp.tools")

ClientFactory = Callable[[], TimeZestClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

NOT_CONFIGURED = error_envelope(
    "not_configured", "TimeZest credentials are missing for this request (X-Timezest-Api-Token).", False
)

PageSize = Annotated[
    int | None,
    Field(description=f"Return at most this many records (TimeZest pages hold {API_PAGE_SIZE}).", ge=1, le=100),
]
Tql = Annotated[str | None, Field(description='TimeZest TQL filter, e.g. "agent.name LIKE smith".')]
Cursor = Annotated[str | None, Field(description="next_cursor from the previous page.")]

_TIMESTAMP_FIELDS = {"created_at", "updated_at", "scheduled_at", "selected_start_time"}


ModelT = TypeVar("ModelT", bound=BaseModel)


def as_model(value: Any, model: type[ModelT], name: str, plain_field: str | None = None) -> ModelT | None:
    """Accept an object, its JSON text, or (with ``plain_field``) a bare string for that one field.

    Older callers sent some arguments as JSON text; this keeps both forms working.
    """
    if value is None or isinstance(value, model):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f"{name} is not valid JSON: {e.msg}.") from None
        elif plain_field:
            value = {plain_field: text}
        else:
            raise ValueError(f"{name} must be an object or its JSON text.")
    return model.model_validate(value)


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def readable(record: Any) -> Any:
    """Turn TimeZest's Unix-second timestamps into ISO 8601 UTC strings."""
    if isinstance(record, list):
        return [readable(r) for r in record]
    if not isinstance(record, dict):
        return record
    out = {}
    for key, value in record.items():
        if key in _TIMESTAMP_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            out[key] = value
    return out


def rows(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return []


async def list_page(
    client: TimeZestClient,
    path: str,
    tql: str | None,
    starting_after: str | None,
    page_size: int | None,
    keep: Callable[[dict], bool] | None = None,
) -> dict:
    """One TimeZest page: {items, count, has_more, next_cursor}."""
    payload = await client.get(path, compact({"filter": (tql or "").strip() or None, "starting_after": starting_after}))
    page = [r for r in rows(payload) if isinstance(r, dict)]
    items = [r for r in page if keep is None or keep(r)]
    truncated = page_size is not None and len(items) > page_size
    items = items[:page_size] if page_size is not None else items
    has_next = bool(isinstance(payload, dict) and payload.get("next_page"))
    cursor = None
    if truncated and items:
        cursor = items[-1].get("id")
    elif has_next and page:
        cursor = page[-1].get("id")
    return compact(
        {
            "items": readable(items),
            "count": len(items),
            "has_more": bool(cursor),
            "next_cursor": cursor,
        }
    )


async def find_by_id(client: TimeZestClient, path: str, record_id: str, label: str) -> dict:
    """Walk the list endpoint for one id (TimeZest has no get-by-id for this object)."""
    wanted = record_id.strip()
    cursor = None
    seen: set[str] = set()
    for _ in range(MAX_LOOKUP_PAGES):
        payload = await client.get(path, compact({"starting_after": cursor}))
        page = [r for r in rows(payload) if isinstance(r, dict)]
        match = next((r for r in page if r.get("id") == wanted), None)
        if match:
            return readable(match)
        last = page[-1].get("id") if page else None
        if not (isinstance(payload, dict) and payload.get("next_page")) or not last or last in seen:
            break
        seen.add(last)
        cursor = last
    raise TimeZestError(404, f"{label} {wanted} not found; list them first to get a valid id.")


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[TimeZestClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    started = time.monotonic()
    try:
        result = await action(client)
    except TimeZestError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if result is None:
        result = {"result": None, "note": "TimeZest returned an empty response."}
    return result if isinstance(result, str) else dump_json_capped(result)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
