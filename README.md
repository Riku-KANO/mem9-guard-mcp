# mem9-guard-mcp

An MCP server that exposes [mem9](https://github.com/mem9-ai/mem9) (the TiDB team's
persistent memory backend for AI agents) behind
[OWASP agent-memory-guard](https://owasp.org/www-project-agent-memory-guard/).

Agents never touch the raw mem9 API — every read and write goes through the guard:

```
MCP client (agent)
        │  memory_read / memory_write / ...
        ▼
  mem9-guard-mcp (this server)
        │  MemoryGuard + Policy.strict()   ← inspect, then block / quarantine / redact
        ▼
  Mem9Store adapter (MemoryStore Protocol)
        │  REST (X-API-Key)
        ▼
      mem9 (api.mem9.ai or self-hosted)
```

This protects agent memory against prompt injection, secret leakage, and memory
poisoning: malicious or sensitive content is blocked, quarantined, or redacted
according to policy before it ever reaches — or returns from — the store.

## Tools

| Tool | Description |
|---|---|
| `memory_write(key, value, source_class, memory_class)` | Guarded write. Result is `allow` / `redact` / `quarantine` / `blocked` |
| `memory_read(key, default)` | Read with integrity verification and outbound screening |
| `memory_delete(key)` | Delete a key (protected keys are blocked) |
| `memory_list()` | List stored keys |
| `security_events(limit)` | Recent security events emitted by the guard (for auditing) |
| `quarantine_list()` | Writes currently held in quarantine |

`rollback` / snapshot restore is intentionally **not** exposed. Recovery is an
operator action; giving it to agents would let them discard legitimate writes
or cover up poisoned data.

## Configuration (environment variables)

| Variable | Description |
|---|---|
| `MEM9_API_KEY` | mem9 API key. **Falls back to a local JSON store when unset** |
| `MEM9_API_URL` | Defaults to `https://api.mem9.ai`. Override for self-hosted mem9 |
| `MEM9_AGENT_ID` | `X-Mnemo-Agent-Id` header (optional) |
| `MEM9_GUARD_POLICY` | Path to a policy YAML. Defaults to `Policy.strict()` |
| `MEM9_GUARD_LOCAL_PATH` | Path of the fallback JSON store (default `mem9_local_store.json`) |

## Registering with Claude Code

```powershell
claude mcp add mem9-guard `
  --env MEM9_API_KEY=<your-key> `
  -- uv run --project <path-to-this-repo> mem9-guard-mcp
```

Or with any MCP client that supports stdio servers:

```json
{
  "mcpServers": {
    "mem9-guard": {
      "command": "uv",
      "args": ["run", "--project", "<path-to-this-repo>", "mem9-guard-mcp"],
      "env": { "MEM9_API_KEY": "<your-key>" }
    }
  }
}
```

## Development

```powershell
uv sync
uv run pytest

# End-to-end smoke test over stdio (no LLM involved)
uv run python scripts/smoke_stdio.py
```

## Notes

The mem9 v1alpha2 JSON field names (`content` / `metadata` / `id`) are not yet
covered by a published official schema, so they are centralized as assumptions
in `src/mem9_guard_mcp/client.py`. If the real API differs, that is the only
file that needs to change.

## License

[MIT](LICENSE)
