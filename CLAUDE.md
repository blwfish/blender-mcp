# Blender MCP — Development Notes

## Project Layout

```
src/blender_mcp/
    server.py       FastMCP server (7 tools, stdio transport)
    connection.py   Async TCP connection to Blender (port 9876)
    protocol.py     Request/Response dataclasses, error codes, versioning
    debug.py        File-based logging + performance tracking (see below)
    health.py       Background health monitor / ping loop (see below)
    validators.py   HO scale constants, mesh validation helpers
addon/
    blender_mcp_bridge.py   Single-file Blender addon (4.2+ / 5.x)
tests/
    test_protocol.py    26 tests — no Blender, no network
    test_server.py      15 tests — MockBlenderServer fixture
    test_debug.py       17 tests — no Blender, no network
    test_integration.py Requires live Blender
```

## Running Tests

```bash
# All offline tests (no Blender needed)
pytest tests/test_protocol.py tests/test_server.py tests/test_debug.py -v

# Full suite (requires Blender running with addon)
pytest tests/ -v
```

## Debug Infrastructure

**This has repaid itself within an hour every time it's been used. Check these first when something goes wrong.**

### Files written

| File | Contents |
|------|----------|
| `/tmp/blender_mcp_debug/blender_mcp.log` | MCP server: every tool call, errors with tracebacks (rotating 10 MB × 5) |
| `/tmp/blender_mcp_debug/addon.log` | Blender addon: connect/disconnect events, command errors, bpy tracebacks (rotating 10 MB × 5) |
| `/tmp/blender_mcp_debug/operations_YYYYMMDD.json` | Newline-delimited JSON; one entry per tool call (errors always; successes in VERBOSE mode) |

Override log location: `BLENDERMCP_LOG_DIR=/path/to/dir`

### LEAN vs VERBOSE mode

| Mode | When | What's logged |
|------|------|---------------|
| LEAN (default) | `BLENDERMCP_LOG_LEVEL=INFO` | Errors (full params + traceback) + summary line per call |
| VERBOSE | `BLENDERMCP_LOG_LEVEL=DEBUG` | Everything: params, results, timing for every call |

### Accessing diagnostics from Claude Code

```python
manage_connection(action="status")
# Returns:
# {
#   "connected": True, "blender_version": "5.0.1", ...
#   "health": {
#     "healthy": True, "total_pings": 12, "consecutive_failures": 0,
#     "last_success_ago_s": 4.2, "reconnect_attempts": 0
#   },
#   "performance": {
#     "_summary": {"total_calls": 47, "total_errors": 2, "uptime_s": 312.4, "mode": "LEAN"},
#     "execute_blender_code": {"count": 30, "avg_ms": 140.2, "min_ms": 45.1, "max_ms": 890.3},
#     "get_scene_info": {"count": 12, "avg_ms": 22.0, ...},
#     ...
#   }
# }
```

### Python API (when debugging the server itself)

```python
from blender_mcp.debug import get_debugger
from blender_mcp.health import get_monitor

dbg = get_debugger()
dbg.performance_report()        # per-tool timing stats
dbg.export_debug_package()      # log_dir + op_log path + perf stats
dbg.log_operation("my_op", parameters={"x": 1}, duration=0.05)

mon = get_monitor()
mon.get_status()                # healthy, consecutive_failures, etc.
mon.export_report()             # full event history (last 50 events)
```

### `tool_decorator()` — applied automatically to all data tools

`server.py` applies `@_tool_dec()` to every tool except `manage_connection`. This decorator:
- Records wall-clock duration for every call
- Calls `log_operation()` (text log + JSON op-log)
- Calls `track_performance()` (feeds the per-tool stats in `manage_connection status`)
- In LEAN mode, replaces large `code` parameters with `<N chars>` summary
- Re-raises exceptions unchanged (transparent to FastMCP)

If `init_debugger()` failed at startup, `_tool_dec()` returns a no-op wrapper so the server still runs.

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `BLENDERMCP_LOG_DIR` | `/tmp/blender_mcp_debug` | Log file directory |
| `BLENDERMCP_LOG_LEVEL` | `INFO` | `DEBUG` → VERBOSE mode |
| `BLENDER_HOST` | `127.0.0.1` | Blender addon TCP host |
| `BLENDER_PORT` | `9876` | Blender addon TCP port |

## Scale Convention

All modeling in **meters** (prototype scale). Apply scale only at export:
- HO (1:87.1): `scale = 0.01148`
- This avoids floating-point issues that appear with mm-scale Blender geometry.

## Protocol

Newline-delimited JSON. Every message includes `protocol_version` (`"0.1.0"`). Major.minor must match between server and addon; patch may differ. Version mismatch → `BlenderConnectionError` at connect time.

## Key Invariants

- **Never write to stdout.** MCP uses stdio transport; stdout corruption breaks the session. All logging goes to files only.
- **Blender main thread only.** The addon queues all `bpy` calls via `bpy.app.timers`; never call `bpy` from the socket thread directly.
- **Singleton connection.** `_connection` in `server.py` is module-level; health monitor is started on first successful connect and reused.
