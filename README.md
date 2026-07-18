# LM Studio Manager — MCP Server

## What This Is

This is an **MCP server** (Model Context Protocol). It is a Python script that LM Studio launches as a child process. When active, it exposes 18 callable tools that any MCP-compatible client (like LM Studio's chat) can use to manage models programmatically.

**This is NOT a skill, plugin, or knowledge file.** It is an executable Python server that communicates with LM Studio over HTTP using LM Studio's local API.

## What It Does

- Lists all installed models with loaded/unloaded status
- Loads a model into LM Studio memory
- Unloads a model from LM Studio memory (using `instance_id`)
- Sends chat prompts to a loaded model
- Benchmarks models on scoring tasks
- Compares multiple models head-to-head
- Manages parameter profiles (temperature, max_tokens, etc.)
- Auto-tunes settings based on available system RAM
- Logs events and benchmark history to local JSON files

## Architecture

```
LM Studio (GUI)
    |
    |-- spawns lmstudio_manager.py as child process (via mcp.json config)
    |       |
    |       |-- FastMCP server (exposes tools via stdio)
    |       |-- httpx client (talks to LM Studio's HTTP API on localhost:1234)
    |       |-- JSON files in ~/lmstudio-mcp-data/ (persistent state)
    |
    |-- MCP client calls tools like: load_model("gemma-4-e4b-it-qat")
```

LM Studio reads `mcp.json` on startup, spawns the Python process, and exposes the tools in its chat interface. The tools appear as available functions the model can call.

## Deployment — Linux

```bash
# 1. Clone the repo
git clone https://github.com/athenamiro/lmstudio-manager.git
cd lmstudio-manager

# 2. Create virtual environment and install dependencies
python3 -m venv .venv
.venv/bin/pip install mcp httpx psutil

# 3. Verify it works (should print "All imports OK")
.venv/bin/python -c "import mcp, httpx, psutil; print('All imports OK')"

# 4. Create mcp.json in LM Studio's config directory
#    Usually ~/.lmstudio/mcp.json
cat > ~/.lmstudio/mcp.json << 'EOF'
{
  "mcpServers": {
    "lmstudio-manager": {
      "command": "/home/YOUR_USERNAME/lmstudio-manager/.venv/bin/python",
      "args": ["/home/YOUR_USERNAME/lmstudio-manager/lmstudio_manager.py"]
    }
  }
}
EOF

# 5. Restart LM Studio
```

Replace `YOUR_USERNAME` with your actual Linux username.

## Deployment — Windows

```powershell
# 1. Clone the repo
git clone https://github.com/athenamiro/lmstudio-manager.git
cd lmstudio-manager

# 2. Create virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\pip install mcp httpx psutil

# 3. Create mcp.json in LM Studio's config directory
#    Usually in the LM Studio installation folder or %APPDATA%\LM Studio\
```

```json
{
  "mcpServers": {
    "lmstudio-manager": {
      "command": "C:\\Users\\YOUR_USERNAME\\lmstudio-manager\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YOUR_USERNAME\\lmstudio-manager\\lmstudio_manager.py"]
    }
  }
}
```

## After Deployment

1. **Restart LM Studio** — it reads `mcp.json` on startup
2. **Open a chat** — the MCP tools become available automatically
3. **Test** — ask the model to call `health_check` or `list_models`

The model in LM Studio will see these tools and can call them when you ask it to manage models, load/unload, chat, benchmark, etc.

## Data Files

Created automatically at `~/lmstudio-mcp-data/` on first run:

| File | Purpose | Editable? |
|------|---------|-----------|
| `lmstudio_manager_config.json` | LM Studio URL, API key, endpoint lists, timeouts | Yes — via `update_manager_config` tool |
| `lmstudio_state.json` | Active model name, instance ID, cached chat endpoint | Auto-managed |
| `lmstudio_profiles.json` | Parameter presets (general, coding, creative, low-memory) | Yes |
| `lmstudio_registry.json` | Event log and benchmark history | Auto-managed |

## LM Studio API Endpoints Used

The server talks to LM Studio's local HTTP API. These are the endpoints it uses:

| Action | Method | Endpoint |
|--------|--------|----------|
| List models | GET | `/api/v1/models` |
| Load model | POST | `/api/v1/models/load` |
| Unload model | POST | `/api/v1/models/unload` |
| Chat completion | POST | `/v1/chat/completions` |

Authentication: Bearer token from `lmstudio_manager_config.json` → `lmstudio.api_key`.

## MCP Tools Reference

These are the tools the server exposes. They are callable functions, not documentation.

| Tool | Input | Description |
|------|-------|-------------|
| `health_check` | none | Check LM Studio reachability, system resources, and state |
| `get_manager_config` | none | View current configuration |
| `update_manager_config` | `patch` (dict) | Patch config values with automatic backup |
| `list_models` | none | List all models with loaded instance info |
| `load_model` | `model_name` (string) | Load a model into LM Studio memory |
| `unload_model` | `model_name` (string, optional) | Unload a model by name or tracked instance ID |
| `chat` | `prompt` (string), optional: `model_name`, `system_prompt`, `temperature`, `max_tokens` | Send a chat prompt |
| `benchmark_model` | optional: `task_type` ("general"/"coding"/"creative"), `model_name` | Run benchmark and score a model |
| `compare_models` | `model_names` (list of strings), optional: `task_type` | Compare multiple models head-to-head |
| `flag_model` | `model_name`, optional: `score`, `note`, `tags` | Tag a model in the registry |
| `get_profiles_config` | none | View available parameter profiles |
| `set_profile` | `profile_name` (string) | Switch active parameter profile |
| `auto_tune_model` | optional: `task_type` | Auto-select profile based on available RAM |
| `get_runtime_state` | none | View current runtime state and system info |
| `backup_manager_files` | none | Backup all JSON data files |
| `benchmark_history` | optional: `limit` (int) | View past benchmark runs |
| `view_registry` | optional: `limit` (int) | View events and model registry |
| `clear_endpoint_cache` | none | Force re-discovery of chat endpoint |

## Troubleshooting

**Tools don't appear in LM Studio chat:**
- Check that `mcp.json` exists in LM Studio's config directory
- Verify the Python path in `mcp.json` points to the venv's python binary
- Restart LM Studio after adding/changing `mcp.json`
- Check LM Studio logs for MCP errors

**"ModuleNotFoundError: No module named 'mcp'`:**
- The venv doesn't have the required packages
- Run: `.venv/bin/pip install mcp httpx psutil` (Linux) or `.venv\Scripts\pip install mcp httpx psutil` (Windows)

**Chat or load/unload fails with HTTP 401:**
- LM Studio requires an API key
- Set it via the `update_manager_config` tool or edit `~/lmstudio-mcp-data/lmstudio_manager_config.json` and add `"api_key": "your-key-here"` inside the `"lmstudio"` object

**Unload fails with "Missing required field 'instance_id'`:**
- This server handles `instance_id` automatically by querying the live model list
- If it still fails, call `list_models` first to refresh the state, then try unload again

**Port conflict (default 1234):**
- If LM Studio runs on a different port, update `base_url` in `lmstudio_manager_config.json`

## License

MIT
