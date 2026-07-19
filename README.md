# LM Studio Manager — MCP Server

## What This Is

An **MCP server** (Model Context Protocol) for [LM Studio](https://lmstudio.ai). It is a Python script that LM Studio launches as a child process. When active, it exposes **30 callable tools** for model management, benchmarking, evaluation, and AI-agent optimization.

**This is NOT a skill or knowledge file.** It is an executable Python server that communicates with LM Studio over HTTP.

## What It Does

**Core Management:**
- List, load, and unload models with automatic `instance_id` tracking
- Send chat prompts using profile-based parameter presets
- Health check, system info, and runtime state

**Benchmarking & Evaluation:**
- Structured benchmark suite: general, coding, creative, instruction following, summarization, tool-use, low-latency
- Agent compatibility testing: instruction following, JSON format, tool awareness, multi-step reasoning
- Structured output testing: valid JSON, exact keys, no extra text
- Context stress testing: short/medium/long context with latency growth measurement
- Prompt stability testing: consistency and format stability across repeats
- Resource profiling: RAM before/after/during inference, load/unload times

**Intelligence Layer:**
- Automatic model recommendation based on benchmark history and system RAM
- Best-for-task quick lookups (coding, JSON, fast response, agent, creative, low RAM)
- Task fit scoring combining quality and speed
- Queryable history with filters by model, task type, and date

**Orchestration:**
- Safe model switching: save current → load target → test → restore
- Export reports in JSON, CSV, or Markdown
- Self-check tool verifying config, connectivity, endpoints, Python env, and data directory

## Requirements

- Python 3.11+
- LM Studio running with API enabled (on same machine or network)
- `mcp`, `httpx`, `psutil` Python packages

## Deployment — Linux

```bash
git clone https://github.com/Xendegi/lmstudio-manager.git
cd lmstudio-manager
python3 -m venv .venv
.venv/bin/pip install mcp httpx psutil

# Create mcp.json in LM Studio's config directory (~/.lmstudio/mcp.json)
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

# Restart LM Studio
```

## Deployment — Windows

```powershell
git clone https://github.com/Xendegi/lmstudio-manager.git
cd lmstudio-manager
python -m venv .venv
.venv\Scripts\pip install mcp httpx psutil
```

```json
{
  "mcpServers": {
    "lmstudio-manager": {
      "command": "C:\\Users\\YOU\\lmstudio-manager\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YOU\\lmstudio-manager\\lmstudio_manager.py"]
    }
  }
}
```

## Cross-Network Setup

If LM Studio runs on a different machine than the MCP server, pass the `LMSTUDIO_BASE_URL` environment variable:

```json
{
  "mcpServers": {
    "lmstudio-manager": {
      "command": "/path/to/python",
      "args": ["/path/to/lmstudio_manager.py"],
      "env": {
        "LMSTUDIO_BASE_URL": "http://192.168.2.100:1234"
      }
    }
  }
}
```

Or edit `~/lmstudio-mcp-data/lmstudio_manager_config.json` directly:

```json
{
  "lmstudio": {
    "base_url": "http://192.168.2.100:1234",
    "api_key": "your-lm-studio-api-key"
  }
}
```

## Data Files

Created automatically at `~/lmstudio-mcp-data/`:

| File | Purpose |
|------|---------|
| `lmstudio_manager_config.json` | LM Studio URL, API key, endpoint lists, timeouts |
| `lmstudio_state.json` | Active model, instance ID, cached chat endpoint |
| `lmstudio_profiles.json` | Parameter presets |
| `lmstudio_registry.json` | Events, benchmark history, model annotations |

## MCP Tools (30 total)

### Core Management

| Tool | Description |
|------|-------------|
| `health_check` | Check LM Studio reachability, system info, and state |
| `list_models` | List all models with loaded instance info |
| `load_model` | Load a model into LM Studio memory |
| `unload_model` | Unload a model by name or instance ID |
| `chat` | Send a chat prompt with optional system prompt and parameters |
| `get_runtime_state` | View current runtime state and system info |

### Configuration

| Tool | Description |
|------|-------------|
| `get_manager_config` | View current configuration |
| `update_manager_config` | Patch config values with automatic backup |
| `get_profiles_config` | View available parameter profiles |
| `set_profile` | Switch active parameter profile |
| `auto_tune_model` | Auto-select profile based on available RAM |
| `clear_endpoint_cache` | Force re-discovery of chat endpoint |

### Benchmarking

| Tool | Description |
|------|-------------|
| `benchmark_model` | Run benchmark prompts and score a model |
| `compare_models` | Compare multiple models head-to-head |
| `benchmark_history` | View past benchmark runs |
| `query_history` | Query history with filters by model, task type |
| `export_benchmark_report` | Export as JSON, CSV, or Markdown |

### Evaluation

| Tool | Description |
|------|-------------|
| `agent_compatibility_test` | Test agent workflow suitability (instruction following, JSON, tool awareness, multi-step) |
| `structured_output_test` | Test JSON validity, exact keys, no extra text |
| `context_stress_test` | Test across short/medium/long context |
| `prompt_stability_test` | Measure consistency across repeated prompts |
| `resource_profile_model` | Measure RAM, CPU, load/unload times |
| `task_fit_score` | Quick quality + speed fit evaluation |

### Intelligence

| Tool | Description |
|------|-------------|
| `recommend_best_model` | Best model for a task type based on history and RAM |
| `best_model_for_task` | Quick lookup: coding, json, fast, agent, creative, general, low_ram |
| `flag_model` | Tag and annotate a model in the registry |
| `view_registry` | View events and model registry |

### Orchestration

| Tool | Description |
|------|-------------|
| `switch_model_safely` | Save current → load target → test → restore |
| `backup_manager_files` | Backup all JSON data files |
| `self_check` | Verify config, connectivity, endpoints, Python env, data dir |

## Profiles

| Profile | Temp | Top-P | Max Tokens | Use Case |
|---------|------|-------|------------|----------|
| `general_balanced` | 0.4 | 0.9 | 512 | Default general chat |
| `coding_precise` | 0.15 | 0.9 | 768 | Code generation |
| `analysis` | 0.2 | 0.9 | 768 | Analytical tasks |
| `creative_open` | 0.85 | 0.95 | 768 | Creative writing |
| `low_memory_safe` | 0.3 | 0.85 | 256 | Constrained RAM |
| `fast_agent` | 0.2 | 0.85 | 256 | Agent workflows |
| `structured_output` | 0.0 | 1.0 | 512 | JSON/structured data |
| `long_context` | 0.3 | 0.9 | 1024 | Large context windows |

## Benchmark Task Types

| Type | What It Tests |
|------|--------------|
| `general` | General reasoning and knowledge |
| `coding` | Code generation and explanation |
| `creative` | Creative writing and imagination |
| `instruction_following` | Precise instruction adherence |
| `summarization` | Condensing information |
| `tool_use` | Tool selection and argument production |
| `low_latency` | Fast simple responses |
| `structured_output` | Valid JSON with exact keys |
| `agent_compatibility` | AI-agent workflow readiness |

## Troubleshooting

**Tools don't appear in LM Studio:**
- Check `mcp.json` exists in LM Studio's config directory
- Verify the Python path points to the venv's python binary
- Restart LM Studio

**"ModuleNotFoundError: No module named 'mcp'`:**
- Run: `.venv/bin/pip install mcp httpx psutil`

**HTTP 401 errors:**
- Set `api_key` in `lmstudio_manager_config.json` or via `update_manager_config`

**Unload fails:**
- Call `list_models` first to refresh instance tracking, then retry

## License

Self
