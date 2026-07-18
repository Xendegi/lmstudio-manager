# LM Studio Manager — MCP Server

A production-ready MCP (Model Context Protocol) server for [LM Studio](https://lmstudio.ai). Manages models, sends chat prompts, benchmarks, and more — all through structured MCP tools instead of manual API calls.

## Features

- **Health check** — verify LM Studio is reachable and see system resources
- **List models** — discover all installed/available models with loaded instance info
- **Load / unload models** — with automatic `instance_id` tracking
- **Chat** — send prompts using profile-based parameter presets
- **Benchmark** — score models on general, coding, and creative tasks
- **Compare models** — head-to-head ranking with latency and score
- **Profiles** — reusable parameter presets (coding, analysis, creative, low-memory)
- **Auto-tune** — selects the best profile based on system resources
- **Registry** — event log and benchmark history with backup support

## Requirements

- Python 3.11+
- LM Studio running with API enabled
- `mcp`, `httpx`, `psutil` Python packages

## Quick Start

```powershell
# 1. Create a virtual environment
python -m venv .venv
.venv\Scripts\pip install mcp httpx psutil

# 2. Copy lmstudio_manager.py to your LM Studio project folder

# 3. Add to your mcp.json (inside LM Studio's config directory)
```

**mcp.json:**
```json
{
  "mcpServers": {
    "lmstudio-manager": {
      "command": "G:\\AI\\LM-Studio\\.venv\\Scripts\\python.exe",
      "args": ["G:\\AI\\LM-Studio\\lmstudio_manager.py"]
    }
  }
}
```

## Configuration Files

All stored in `~/lmstudio-mcp-data/` (created automatically):

| File | Purpose |
|------|---------|
| `lmstudio_manager_config.json` | Base URL, API key, timeouts, endpoint lists |
| `lmstudio_state.json` | Active model, instance ID, cached endpoints |
| `lmstudio_profiles.json` | Parameter presets (general, coding, creative, etc.) |
| `lmstudio_registry.json` | Events and benchmark history |

## MCP Tools

| Tool | Description |
|------|-------------|
| `health_check` | Check LM Studio reachability, system info, and state |
| `get_manager_config` | View current configuration |
| `update_manager_config` | Patch config values (backs up first) |
| `list_models` | List all available models with loaded status |
| `load_model` | Load a specific model into LM Studio |
| `unload_model` | Unload a model by name or instance ID |
| `chat` | Send a chat prompt with optional system prompt and parameters |
| `benchmark_model` | Run benchmark prompts and score a model |
| `compare_models` | Compare multiple models head-to-head |
| `flag_model` | Tag and annotate a model in the registry |
| `get_profiles_config` | View available parameter profiles |
| `set_profile` | Switch active parameter profile |
| `auto_tune_model` | Auto-select profile based on system resources |
| `get_runtime_state` | View current runtime state and system info |
| `backup_manager_files` | Backup all JSON data files |
| `benchmark_history` | View past benchmark runs |
| `view_registry` | View events and model registry |
| `clear_endpoint_cache` | Force re-discovery of chat endpoint |

## API Endpoints

The server auto-discovers and caches working LM Studio endpoints:

- **Models**: `GET /api/v1/models`
- **Load**: `POST /api/v1/models/load`
- **Unload**: `POST /api/v1/models/unload`
- **Chat**: `POST /v1/chat/completions`

## License

MIT
