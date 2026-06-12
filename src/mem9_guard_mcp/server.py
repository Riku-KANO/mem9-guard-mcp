"""MCP server (stdio) that exposes mem9 behind agent-memory-guard.

Every memory operation passes through MemoryGuard. Agents (MCP clients) never
touch the raw mem9 API, so prompt injection, secrets, and memory poisoning are
blocked / quarantined / redacted according to policy.

Configuration (environment variables):
    MEM9_API_KEY          mem9 API key. Falls back to a local JSON store when unset
    MEM9_API_URL          Defaults to https://api.mem9.ai
    MEM9_AGENT_ID         X-Mnemo-Agent-Id header (optional)
    MEM9_GUARD_POLICY     Path to a policy YAML (optional, defaults to Policy.strict())
    MEM9_GUARD_LOCAL_PATH Path of the fallback JSON store (default ./mem9_local_store.json)

rollback / snapshot restore is intentionally not exposed as a tool. Recovery is
an operator action; giving it to agents would let them cover up poisoned data
or discard legitimate writes.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

from agent_memory_guard import (
    IntegrityError,
    MemoryGuard,
    MemoryGuardError,
    Policy,
    PolicyViolation,
)
from mcp.server.fastmcp import FastMCP

from mem9_guard_mcp.client import DEFAULT_BASE_URL, Mem9Client, Mem9Error
from mem9_guard_mcp.store import LocalJsonStore, Mem9Store

log = logging.getLogger("mem9_guard_mcp")

mcp = FastMCP("mem9-guard")

_guard: MemoryGuard | None = None
_store: Any = None

_VALID_SOURCE_CLASSES = {"user_input", "external_tool", "agent_authored", "system"}


def build_guard() -> MemoryGuard:
    global _store
    api_key = os.environ.get("MEM9_API_KEY")
    if api_key:
        client = Mem9Client(
            api_key,
            base_url=os.environ.get("MEM9_API_URL", DEFAULT_BASE_URL),
            agent_id=os.environ.get("MEM9_AGENT_ID"),
        )
        store: Any = Mem9Store(client)
        log.info("backend: mem9 (%s)", os.environ.get("MEM9_API_URL", DEFAULT_BASE_URL))
    else:
        path = os.environ.get("MEM9_GUARD_LOCAL_PATH", "mem9_local_store.json")
        store = LocalJsonStore(path)
        log.warning("MEM9_API_KEY is not set — falling back to local JSON store: %s", path)

    policy_path = os.environ.get("MEM9_GUARD_POLICY")
    if policy_path:
        from agent_memory_guard.policies.policy import load_policy

        policy = load_policy(policy_path)
    else:
        policy = Policy.strict()

    _store = store
    return MemoryGuard(store, policy=policy)


def _get_guard() -> MemoryGuard:
    global _guard
    if _guard is None:
        _guard = build_guard()
    return _guard


@mcp.tool()
def memory_write(
    key: str,
    value: str,
    source_class: str = "agent_authored",
    memory_class: str | None = None,
) -> dict[str, Any]:
    """Write a value to guarded mem9 memory.

    The value is screened by agent-memory-guard before persisting; the result may be
    allow / redact / quarantine / block. Declare provenance honestly via source_class
    (user_input | external_tool | agent_authored | system) — it drives self-poisoning
    detection. Optionally classify with memory_class (e.g. "ephemeral").
    """
    if source_class not in _VALID_SOURCE_CLASSES:
        return {"status": "error", "message": f"invalid source_class: {source_class}"}
    try:
        action = _get_guard().write(
            key, value, source_class=source_class, cls=memory_class
        )
    except PolicyViolation as exc:
        return {"status": "blocked", "key": key, "reason": str(exc)}
    except MemoryGuardError as exc:
        return {"status": "error", "key": key, "message": str(exc)}
    except Mem9Error as exc:
        return {"status": "backend_error", "key": key, "message": str(exc)}
    return {"status": action.value, "key": key}


@mcp.tool()
def memory_read(key: str, default: str | None = None) -> dict[str, Any]:
    """Read a value from guarded mem9 memory.

    Reads run integrity verification and outbound screening; sensitive content may
    come back redacted, and tampered or policy-violating entries are blocked.
    """
    try:
        value = _get_guard().read(key, default)
    except IntegrityError as exc:
        return {"status": "integrity_error", "key": key, "message": str(exc)}
    except PolicyViolation as exc:
        return {"status": "blocked", "key": key, "reason": str(exc)}
    except Mem9Error as exc:
        return {"status": "backend_error", "key": key, "message": str(exc)}
    return {"status": "ok", "key": key, "value": value}


@mcp.tool()
def memory_delete(key: str) -> dict[str, Any]:
    """Delete a key from guarded mem9 memory. Protected keys cannot be deleted."""
    try:
        _get_guard().delete(key)
    except PolicyViolation as exc:
        return {"status": "blocked", "key": key, "reason": str(exc)}
    except Mem9Error as exc:
        return {"status": "backend_error", "key": key, "message": str(exc)}
    return {"status": "deleted", "key": key}


@mcp.tool()
def memory_list() -> dict[str, Any]:
    """List all keys currently stored in guarded mem9 memory."""
    _get_guard()
    try:
        keys = sorted(_store.keys())
    except Mem9Error as exc:
        return {"status": "backend_error", "message": str(exc)}
    return {"status": "ok", "keys": keys}


@mcp.tool()
def security_events(limit: int = 20) -> dict[str, Any]:
    """Return recent security events emitted by the guard (newest first).

    Useful for auditing why a write was blocked, redacted, or quarantined.
    """
    events = _get_guard().events
    return {
        "status": "ok",
        "total": len(events),
        "events": [ev.to_dict() for ev in reversed(events[-limit:])],
    }


@mcp.tool()
def quarantine_list() -> dict[str, Any]:
    """List writes currently held in quarantine (detected anomalies pending review)."""
    return {"status": "ok", "keys": sorted(_get_guard().quarantine.keys())}


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _get_guard()
    mcp.run()


if __name__ == "__main__":
    main()
