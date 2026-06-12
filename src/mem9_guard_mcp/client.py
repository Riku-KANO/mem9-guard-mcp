"""Thin client for the mem9 v1alpha2 REST API.

mem9 (https://github.com/mem9-ai/mem9) endpoints:

    POST   /v1alpha2/mem9s/memories        create
    GET    /v1alpha2/mem9s/memories        search/list (paginated, limit<=200)
    GET    /v1alpha2/mem9s/memories/{id}   get
    PUT    /v1alpha2/mem9s/memories/{id}   update
    DELETE /v1alpha2/mem9s/memories/{id}   delete

Authentication is the X-API-Key header; agent identity is X-Mnemo-Agent-Id.

The schema follows docs/api/openapi.json (OpenAPI) in the mem9 repository:
  - Creation takes either content or messages. For KV-store usage we use
    content + memory_type="pinned" (synchronous, stored verbatim). The
    messages mode runs fact extraction over conversations, so it is not used
    to persist values the guard has already screened.
  - The list response is {memories, total, limit, offset}.
"""
from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.mem9.ai"
MEMORIES_PATH = "/v1alpha2/mem9s/memories"


class Mem9Error(RuntimeError):
    """A mem9 API call failed."""


class Mem9Client:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        agent_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        headers = {"X-API-Key": api_key}
        if agent_id:
            headers["X-Mnemo-Agent-Id"] = agent_id
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    def close(self) -> None:
        self._http.close()

    def list_memories(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._request(
                "GET", MEMORIES_PATH, params={"limit": 200, "offset": offset}
            )
            if not isinstance(data, dict) or not isinstance(data.get("memories"), list):
                raise Mem9Error(f"Unexpected list response shape: {type(data).__name__}")
            page = data["memories"]
            records.extend(page)
            offset += len(page)
            if not page or offset >= int(data.get("total", offset)):
                return records

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        return self._request("GET", f"{MEMORIES_PATH}/{memory_id}")

    def create_memory(self, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            MEMORIES_PATH,
            json={"content": content, "metadata": metadata, "memory_type": "pinned"},
        )

    def update_memory(
        self, memory_id: str, content: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"{MEMORIES_PATH}/{memory_id}",
            json={"content": content, "metadata": metadata},
        )

    def delete_memory(self, memory_id: str) -> None:
        self._request("DELETE", f"{MEMORIES_PATH}/{memory_id}", expect_json=False)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        try:
            resp = self._http.request(method, path, json=json, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise Mem9Error(
                f"mem9 API error {exc.response.status_code} on {method} {path}: "
                f"{exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise Mem9Error(f"mem9 API request failed on {method} {path}: {exc}") from exc
        if not expect_json:
            return None
        return resp.json() if resp.content else None


def record_id(record: dict[str, Any]) -> str:
    for field in ("id", "memory_id", "uid"):
        if record.get(field):
            return str(record[field])
    raise Mem9Error(f"mem9 record has no id field: {sorted(record)}")
