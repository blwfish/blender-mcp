# Blender MCP

MCP server for driving Blender from Claude Code.

Blender owns organic and natural geometry: trees, human figures, terrain features, rock faces, and streambeds. This MCP server lets Claude Code control Blender for these workflows without requiring direct Blender UI interaction.

## Components

- **MCP Server** (`src/blender_mcp/`) — FastMCP server connecting Claude Code to Blender over TCP
- **Blender Addon** (`addon/blender_mcp_bridge.py`) — TCP socket server running inside Blender

## Installation

### 1. Install the Blender Addon

1. Open Blender
2. Edit → Preferences → Add-ons → Install from Disk
3. Select `addon/blender_mcp_bridge.py`
4. Enable the addon (checkbox)
5. The server starts automatically. Verify in View3D → Sidebar (N) → Blender MCP tab

### 2. Install the MCP Server

Development install:
```bash
cd blender-mcp
pip install -e ".[test]"
```

Or with uv:
```bash
uv pip install -e ".[test]"
```

### 3. Configure Claude Code

Add to `~/.claude.json` or project `.claude.json`:
```json
{
  "mcpServers": {
    "blender": {
      "command": "python",
      "args": ["-m", "blender_mcp.server"]
    }
  }
}
```

Or with uvx (after publishing):
```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["blender-mcp"]
    }
  }
}
```

## Usage

Start Blender first (addon auto-starts the server), then use the MCP tools from Claude Code:

```
manage_connection(action="status")     # verify connection
get_scene_info(detail_level="mesh")   # see what's in the scene
execute_blender_code(code="...")       # run any Python in Blender
check_mesh_printability(object_name="Cube")
export_mesh(filepath="/tmp/model.stl", scale=0.01148)  # HO scale
```

## Tools

| Tool | Description |
|------|-------------|
| `execute_blender_code` | Run Python in Blender (access to `bpy` and all addons) |
| `get_scene_info` | Query scene objects, mesh stats, materials |
| `export_mesh` | Export to STL/OBJ/3MF with scale applied at export |
| `check_mesh_printability` | Manifold check, thin features, volume for resin printing |
| `screenshot` | Capture 3D viewport as PNG |
| `import_mesh` | Import STL/OBJ/3MF/STEP into Blender |
| `manage_connection` | Connection status, reconnect, ping |

## Scale Convention

All modeling occurs at **full prototype dimensions** (meters). Scale is applied only at export:
- HO scale (1:87.1): `scale = 0.01148`
- This avoids floating-point precision issues with millimeter-scale Blender geometry.

## Debugging

The server writes structured logs to `/tmp/blender_mcp_debug/` (override with `BLENDERMCP_LOG_DIR`):

| File | Contents |
|------|----------|
| `blender_mcp.log` | MCP server — every tool call, errors with full tracebacks |
| `addon.log` | Blender addon — connect events, command errors, bpy tracebacks |
| `operations_YYYYMMDD.json` | Newline-delimited JSON — one entry per call (errors always; all calls in VERBOSE mode) |

**LEAN mode** (default): only error details are verbose. **VERBOSE mode**: set `BLENDERMCP_LOG_LEVEL=DEBUG`.

The `manage_connection(action="status")` tool surfaces a live summary without touching the files:

```python
manage_connection(action="status")
# → includes "health" (ping counts, consecutive failures) and
#   "performance" (per-tool call counts, avg/min/max ms, error count)
```

Check these first when something goes wrong — the logs contain tracebacks and timing for every call made in the session.

## Troubleshooting

**"Cannot connect to Blender"**
- Ensure Blender is running with the addon enabled
- Check View3D → Sidebar → Blender MCP: Status should show "Listening"
- Default port is 9876; check for conflicts

**"Protocol version mismatch"**
- Update the component identified in the error message
- MCP server and Blender addon must have matching major.minor versions

**"Lost connection to Blender"**
- Call `manage_connection(action="reconnect")`
- If Blender crashed, restart Blender and reconnect

## Development

```bash
# Run unit and server tests (no Blender required)
pytest tests/test_protocol.py tests/test_server.py -v

# Run integration tests (requires Blender running)
pytest tests/test_integration.py -v
```

## Architecture

```
Claude Code ──(stdio/MCP)── MCP Server ──(TCP :9876)── Blender Addon
```

The Blender addon runs a TCP server on a background thread. Commands from the MCP server are queued and dispatched to Blender's main thread via `bpy.app.timers`. This is the only safe pattern for driving `bpy` from an external trigger.

Protocol: newline-delimited JSON with protocol version in every message.
