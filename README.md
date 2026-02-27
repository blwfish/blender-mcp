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

## License

MIT
