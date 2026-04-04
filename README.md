# blender-mcp

This tool enables your AI agent to use [Blender](https://www.blender.org/) — the open-source 3D modeling and rendering software — to create organic and natural 3D geometry for you.

## What This Does

You describe what you need — "model a tree for my scene" or "create terrain with a streambed" — and your AI agent does the rest: writing Blender Python scripts, manipulating meshes, checking printability, and exporting to STL/OBJ/3MF. Blender excels at organic geometry: trees, human figures, terrain features, rock faces, and anything where mesh modeling shines.

You don't need to know Blender. You don't need to know what mesh modeling means. You just need an AI agent (like [Claude](https://claude.ai/)).

## Getting Started

Tell your AI agent:

> Go to https://github.com/blwfish/blender-mcp and read the AGENT-INSTALL.md file. Follow the instructions to install and configure the Blender MCP server on this machine.

Your agent will handle the rest — installing prerequisites, cloning the repo, setting up the Blender addon, and registering itself. Once setup is complete, you can ask your agent to create 3D models.

## What You Can Ask Your Agent To Do

- **Create 3D models** — "Model a pine tree about 50 feet tall with realistic branching"
- **Sculpt terrain** — "Create a hillside with a streambed and some scattered rocks"
- **Prepare for 3D printing** — "Check if this model is printable and export it as STL at HO scale"
- **Work with existing models** — "Import this STL and add detail to the surface"
- **Use Blender addons** — "Use TheGrove to grow a realistic oak tree" (if installed)

## Background

I built this for myself. I use Claude Code on a Mac. Other platforms *should* work but are less tested. PRs for other agents and platforms will be considered.

### For Developers

```bash
# 86 tests, no Blender required
pytest tests/test_protocol.py tests/test_server.py tests/test_debug.py -v
```

See [AGENT-INSTALL.md](AGENT-INSTALL.md) for full technical details, architecture, contributing guidelines, and how to add new tools.

## Security

This MCP server grants your AI agent full access to Blender's Python environment, including the ability to run arbitrary code via `execute_blender_code`. This is by design — it's what makes the tool useful. However, you should be aware of the implications:

- **Arbitrary code execution**: The `execute_blender_code` tool can run any Python code inside Blender, with full access to the filesystem, network, and OS. This is equivalent to giving your AI agent a shell.
- **Unrestricted file access**: Mesh import/export operations accept arbitrary filesystem paths. The agent can read and write any file your user account can access.
- **Localhost TCP**: The MCP server communicates with the Blender addon over TCP on `127.0.0.1:9876`. It is not exposed to the network, but any local process can connect to this port.
- **No authentication**: The TCP channel has no shared secret or token. On a single-user workstation this is fine; on shared systems, be aware that other local users could connect.

**This tool is intended for local development use on a single-user machine.** Do not expose it to untrusted networks or users.

## License

MIT
