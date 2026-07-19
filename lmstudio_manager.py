from __future__ import annotations

import csv
import io
import json
import platform
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os

for _var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_var, None)

import httpx
from mcp.server.fastmcp import FastMCP

try:
    import psutil
except ImportError:
    psutil = None


mcp = FastMCP("lmstudio-manager")

BASE_DIR = Path.home() / "lmstudio-mcp-data"
BACKUP_DIR = BASE_DIR / "backups"
CONFIG_FILE = BASE_DIR / "lmstudio_manager_config.json"
PROFILE_FILE = BASE_DIR / "lmstudio_profiles.json"
REGISTRY_FILE = BASE_DIR / "lmstudio_registry.json"
STATE_FILE = BASE_DIR / "lmstudio_state.json"

LEGACY_REGISTRY_FILE = BASE_DIR / "model_registry.json"
LEGACY_BENCHMARK_FILE = BASE_DIR / "benchmark_history.json"
LEGACY_STATE_FILE = BASE_DIR / "runtime_state.json"

_CACHED_CHAT_ENDPOINT: str | None = None
_CONFIG_VERSION: int = 2


DEFAULT_CONFIG: dict[str, Any] = {
    "_version": _CONFIG_VERSION,
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234",
        "timeout_seconds": 30,
        "model_list_endpoints": [
            "/api/v1/models",
            "/v1/models",
        ],
        "model_load_endpoints": [
            "/api/v1/models/load",
            "/api/v1/model/load",
        ],
        "model_unload_endpoints": [
            "/api/v1/models/unload",
            "/api/v1/model/unload",
        ],
        "chat_endpoints": [
            "/v1/chat/completions",
            "/api/v1/chat",
        ],
    },
    "benchmark": {
        "repeat_count": 1,
        "max_tokens": 120,
        "temperature": 0.2,
    },
}

DEFAULT_PROFILES: dict[str, Any] = {
    "_version": _CONFIG_VERSION,
    "profiles": {
        "general_balanced": {
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 512,
            "max_context": 4096,
        },
        "coding_precise": {
            "temperature": 0.15,
            "top_p": 0.9,
            "max_tokens": 768,
            "max_context": 8192,
        },
        "analysis": {
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 768,
            "max_context": 8192,
        },
        "creative_open": {
            "temperature": 0.85,
            "top_p": 0.95,
            "max_tokens": 768,
            "max_context": 4096,
        },
        "low_memory_safe": {
            "temperature": 0.3,
            "top_p": 0.85,
            "max_tokens": 256,
            "max_context": 2048,
        },
        "fast_agent": {
            "temperature": 0.2,
            "top_p": 0.85,
            "max_tokens": 256,
            "max_context": 4096,
        },
        "structured_output": {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 512,
            "max_context": 8192,
        },
        "long_context": {
            "temperature": 0.3,
            "top_p": 0.9,
            "max_tokens": 1024,
            "max_context": 32768,
        },
    },
    "task_defaults": {
        "general": "general_balanced",
        "coding": "coding_precise",
        "analysis": "analysis",
        "creative": "creative_open",
        "agent": "fast_agent",
        "structured": "structured_output",
        "long_context": "long_context",
    },
}

DEFAULT_REGISTRY: dict[str, Any] = {
    "models": {},
    "events": [],
    "benchmark_runs": [],
}

DEFAULT_STATE: dict[str, Any] = {
    "loaded_model_name": None,
    "loaded_instance_id": None,
    "active_profile": "general_balanced",
    "last_chat_endpoint": None,
    "last_benchmark_at": None,
}

BENCHMARK_PROMPTS: dict[str, list[dict[str, Any]]] = {
    "general": [
        {
            "prompt": "In 4 short bullet points, explain what a reverse proxy does.",
            "keywords": ["proxy", "request", "server"],
        },
        {
            "prompt": "Summarize why regular backups matter for a Linux server in 3 sentences.",
            "keywords": ["backup", "recovery", "server"],
        },
    ],
    "coding": [
        {
            "prompt": (
                "Write a Python function named factorial that returns the factorial "
                "of a non-negative integer and handles 0 correctly."
            ),
            "keywords": ["def factorial", "return", "0"],
        },
        {
            "prompt": (
                "Explain in 4 lines the difference between a list and a tuple in Python."
            ),
            "keywords": ["list", "tuple", "mutable"],
        },
    ],
    "creative": [
        {
            "prompt": "Write a vivid 5-line sci-fi micro story about a silent server room.",
            "keywords": ["server", "silent"],
        },
        {
            "prompt": "Create 3 imaginative product names for an AI Linux assistant.",
            "keywords": [],
        },
    ],
    "instruction_following": [
        {
            "prompt": (
                "Respond with exactly 3 words, no more, no less. "
                "The words should describe a color."
            ),
            "keywords": [],
            "validator": "three_words",
        },
        {
            "prompt": (
                "List exactly 5 programming languages. Number each one. "
                "Do not include any other text."
            ),
            "keywords": ["1.", "2.", "3.", "4.", "5."],
            "validator": "five_numbered_items",
        },
        {
            "prompt": (
                "Answer this question with only YES or NO, nothing else: "
                "Is Python a compiled language?"
            ),
            "keywords": [],
            "validator": "yes_no_only",
        },
    ],
    "summarization": [
        {
            "prompt": (
                "Summarize the concept of containerization in exactly 2 sentences. "
                "Focus on what it does and why it matters."
            ),
            "keywords": ["container", "isolation", "deploy"],
        },
        {
            "prompt": (
                "In one sentence, explain the difference between TCP and UDP."
            ),
            "keywords": [],
        },
    ],
    "tool_use": [
        {
            "prompt": (
                "You have access to a tool called 'search_web' that takes a query string. "
                "The user asks: 'What is the weather in Tokyo?'. "
                "Respond with the tool call as a JSON object with 'tool' and 'arguments' keys. "
                "Do not include any text outside the JSON."
            ),
            "keywords": ["search_web", "Tokyo"],
            "validator": "tool_call_json",
        },
        {
            "prompt": (
                "You have a tool called 'read_file' that reads a file path. "
                "The user asks: 'Tell me a joke'. "
                "Should you use the tool? Respond with only YES or NO."
            ),
            "keywords": [],
            "validator": "yes_no_only",
        },
    ],
    "low_latency": [
        {
            "prompt": "What is 2+2?",
            "keywords": ["4"],
        },
        {
            "prompt": "Say hello.",
            "keywords": ["hello", "hi", "hey"],
        },
    ],
    "structured_output": [
        {
            "prompt": (
                'Return a JSON object with exactly these keys: "name", "age", "active". '
                'Use values: "test", 25, true. '
                "Return only the JSON, no explanation."
            ),
            "validator": "json_valid",
            "required_keys": ["name", "age", "active"],
        },
        {
            "prompt": (
                'Return a JSON array of exactly 3 objects. Each object must have '
                '"id" (integer) and "label" (string). '
                "Return only the JSON array, no explanation."
            ),
            "validator": "json_array_of_objects",
            "required_keys": ["id", "label"],
        },
    ],
    "agent_compatibility": [
        {
            "prompt": (
                "You are a coding assistant. The user says: 'Write a hello world in Python'. "
                "Respond with ONLY the code block, no explanation."
            ),
            "keywords": ["print", "hello"],
            "category": "instruction_following",
        },
        {
            "prompt": (
                "Return a JSON object with key 'result' containing the value 'success'. "
                "No other text."
            ),
            "validator": "json_valid",
            "required_keys": ["result"],
            "category": "json_format",
        },
        {
            "prompt": (
                "The user asks you to read a file. You have no file-reading tool available. "
                "What do you do? Explain in 1-2 sentences."
            ),
            "keywords": ["cannot", "no tool", "unable", "don't have"],
            "category": "tool_awareness",
        },
        {
            "prompt": (
                "Step 1: Think of a number. Step 2: Double it. Step 3: Report the result. "
                "Follow these steps in order and show your work."
            ),
            "keywords": ["step", "double", "result"],
            "category": "multi_step",
        },
    ],
}


@dataclass
class HttpResult:
    ok: bool
    status: int
    data: Any
    error_message: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    return override if override is not None else base


def ensure_dir_structure() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def ensure_json_file(path: Path, default_data: dict[str, Any]) -> None:
    if not path.exists():
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")


def ensure_versioned_json_file(path: Path, default_data: dict[str, Any]) -> None:
    if not path.exists():
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")
        return
    try:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        on_disk_version = -1
    else:
        on_disk_version = on_disk.get("_version") if isinstance(on_disk, dict) else -1
    code_version = default_data.get("_version", -1)
    if on_disk_version != code_version:
        backup_file(path)
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")


def _load_legacy_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def migrate_legacy_files() -> None:
    ensure_dir_structure()
    if LEGACY_STATE_FILE.exists() and not STATE_FILE.exists():
        old = _load_legacy_json(LEGACY_STATE_FILE)
        merged = deep_merge(DEFAULT_STATE, old) if old else dict(DEFAULT_STATE)
        write_json(STATE_FILE, merged)
        backup_file(LEGACY_STATE_FILE)
    if not REGISTRY_FILE.exists() and (
        LEGACY_REGISTRY_FILE.exists() or LEGACY_BENCHMARK_FILE.exists()
    ):
        old_registry = _load_legacy_json(LEGACY_REGISTRY_FILE) or {}
        old_bench = _load_legacy_json(LEGACY_BENCHMARK_FILE) or {}
        models = old_registry.get("models") if isinstance(old_registry.get("models"), dict) else {}
        events = old_registry.get("events") if isinstance(old_registry.get("events"), list) else []
        runs = old_bench.get("runs") if isinstance(old_bench.get("runs"), list) else []
        merged = {"models": models, "events": events, "benchmark_runs": runs}
        write_json(REGISTRY_FILE, deep_merge(DEFAULT_REGISTRY, merged))
        if LEGACY_REGISTRY_FILE.exists():
            backup_file(LEGACY_REGISTRY_FILE)
        if LEGACY_BENCHMARK_FILE.exists():
            backup_file(LEGACY_BENCHMARK_FILE)


def ensure_all_files() -> None:
    ensure_dir_structure()
    migrate_legacy_files()
    ensure_versioned_json_file(CONFIG_FILE, DEFAULT_CONFIG)
    ensure_versioned_json_file(PROFILE_FILE, DEFAULT_PROFILES)
    ensure_json_file(REGISTRY_FILE, DEFAULT_REGISTRY)
    ensure_json_file(STATE_FILE, DEFAULT_STATE)


def read_json(path: Path, default_data: dict[str, Any]) -> dict[str, Any]:
    ensure_json_file(path, default_data)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return deep_merge(default_data, raw)
    except Exception:
        backup_file(path)
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")
        return json.loads(json.dumps(default_data))


def write_json(path: Path, data: dict[str, Any]) -> None:
    ensure_dir_structure()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def backup_file(path: Path) -> str:
    ensure_dir_structure()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"{path.stem}-{timestamp}{path.suffix}"
    if path.exists():
        shutil.copy2(path, target)
    return str(target)


def get_config() -> dict[str, Any]:
    return read_json(CONFIG_FILE, DEFAULT_CONFIG)


def save_config(data: dict[str, Any]) -> None:
    write_json(CONFIG_FILE, data)


def get_profiles() -> dict[str, Any]:
    return read_json(PROFILE_FILE, DEFAULT_PROFILES)


def save_profiles(data: dict[str, Any]) -> None:
    write_json(PROFILE_FILE, data)


def get_registry() -> dict[str, Any]:
    return read_json(REGISTRY_FILE, DEFAULT_REGISTRY)


def save_registry(data: dict[str, Any]) -> None:
    write_json(REGISTRY_FILE, data)


def get_state() -> dict[str, Any]:
    return read_json(STATE_FILE, DEFAULT_STATE)


def save_state(data: dict[str, Any]) -> None:
    write_json(STATE_FILE, data)


def get_benchmark_history() -> dict[str, Any]:
    registry = get_registry()
    return {"runs": registry.get("benchmark_runs", [])}


def save_benchmark_history(data: dict[str, Any]) -> None:
    registry = get_registry()
    registry["benchmark_runs"] = data.get("runs", []) if isinstance(data, dict) else []
    save_registry(registry)


def log_event(event_type: str, details: dict[str, Any]) -> None:
    try:
        registry = get_registry()
        events = registry.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            registry["events"] = events
        events.append({"timestamp": now_iso(), "type": event_type, "details": details})
        save_registry(registry)
    except Exception:
        pass


def invalidate_chat_endpoint_cache() -> None:
    global _CACHED_CHAT_ENDPOINT
    _CACHED_CHAT_ENDPOINT = None


def http_request_json(
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> HttpResult:
    config = get_config()
    base_url = os.environ.get("LMSTUDIO_BASE_URL") or config["lmstudio"]["base_url"]
    base_url = base_url.rstrip("/")
    request_url = f"{base_url}{endpoint}"
    timeout_value = timeout or int(config["lmstudio"].get("timeout_seconds", 30))
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = config["lmstudio"].get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    content = json.dumps(payload).encode("utf-8") if payload is not None else None
    try:
        with httpx.Client(timeout=timeout_value) as client:
            resp = client.request(method.upper(), request_url, content=content, headers=headers)
            if resp.status_code >= 400:
                raw = resp.text
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {"raw": raw}
                return HttpResult(ok=False, status=resp.status_code, data=data, error_message=f"HTTP {resp.status_code}")
            raw = resp.text
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"raw": raw}
            return HttpResult(ok=True, status=resp.status_code, data=data)
    except httpx.HTTPError as exc:
        return HttpResult(ok=False, status=0, data={}, error_message=str(exc))


def try_endpoints(
    method: str,
    endpoints: list[str],
    payload: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> tuple[str | None, HttpResult]:
    last_result = HttpResult(ok=False, status=0, data={}, error_message="No endpoints tried")
    for endpoint in endpoints:
        result = http_request_json(method, endpoint, payload=payload, timeout=timeout)
        if result.ok:
            return endpoint, result
        last_result = result
    return None, last_result


def get_system_info() -> dict[str, Any]:
    vm = psutil.virtual_memory() if psutil else None
    cpu_percent = psutil.cpu_percent(interval=0.2) if psutil else None
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_percent": cpu_percent,
        "memory": {
            "available_gb": round(vm.available / (1024 ** 3), 2) if vm else None,
            "total_gb": round(vm.total / (1024 ** 3), 2) if vm else None,
            "used_percent": vm.percent if vm else None,
        },
        "psutil_installed": psutil is not None,
    }


def get_memory_snapshot() -> dict[str, Any]:
    if not psutil:
        return {"error": "psutil not installed"}
    vm = psutil.virtual_memory()
    proc = psutil.Process()
    mem_info = proc.memory_info()
    return {
        "system_available_gb": round(vm.available / (1024 ** 3), 2),
        "system_used_percent": vm.percent,
        "process_rss_mb": round(mem_info.rss / (1024 ** 2), 2),
        "process_vms_mb": round(mem_info.vms / (1024 ** 2), 2),
    }


def list_models_internal() -> dict[str, Any]:
    config = get_config()
    endpoint, result = try_endpoints("GET", config["lmstudio"]["model_list_endpoints"])
    if not result.ok:
        return {"ok": False, "error": result.error_message or "Failed to query model list", "status": result.status}
    raw_models = []
    if isinstance(result.data, dict):
        if isinstance(result.data.get("data"), list):
            raw_models = result.data["data"]
        elif isinstance(result.data.get("models"), list):
            raw_models = result.data["models"]
    elif isinstance(result.data, list):
        raw_models = result.data
    models = []
    for item in raw_models:
        if isinstance(item, str):
            models.append({"id": item, "name": item})
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("key") or item.get("model") or item.get("name")
            models.append({
                "id": model_id,
                "name": item.get("display_name") or item.get("name") or model_id,
                "loaded_instances": item.get("loaded_instances", []),
                "raw": item,
            })
    return {"ok": True, "endpoint": endpoint, "models": models, "count": len(models)}


def model_exists(model_name: str) -> bool:
    listed = list_models_internal()
    if not listed.get("ok"):
        return False
    for model in listed.get("models", []):
        if model.get("id") == model_name or model.get("name") == model_name:
            return True
    return False


def load_model_internal(model_name: str) -> dict[str, Any]:
    config = get_config()
    payload_variants = [{"model": model_name}, {"identifier": model_name}, {"model_name": model_name}]
    last_result = None
    used_endpoint = None
    for payload in payload_variants:
        endpoint, result = try_endpoints("POST", config["lmstudio"]["model_load_endpoints"], payload=payload, timeout=120)
        if result.ok:
            used_endpoint = endpoint
            last_result = result
            break
        last_result = result
    if not last_result or not last_result.ok:
        return {"ok": False, "error": (last_result.error_message if last_result else "Load failed"), "status": (last_result.status if last_result else 0)}
    state = get_state()
    state["loaded_model_name"] = model_name
    discovered_instance_id: str | None = None
    listed = list_models_internal()
    if listed.get("ok"):
        for model in listed.get("models", []):
            if model.get("id") == model_name:
                instances = model.get("loaded_instances", [])
                if instances:
                    discovered_instance_id = instances[-1].get("id")
                break
    state["loaded_instance_id"] = discovered_instance_id
    save_state(state)
    invalidate_chat_endpoint_cache()
    log_event("model_loaded", {"model": model_name, "endpoint": used_endpoint})
    return {"ok": True, "endpoint": used_endpoint, "response": last_result.data, "loaded_model_name": model_name}


def unload_model_internal(model_name: str | None = None) -> dict[str, Any]:
    state = get_state()
    target_model = model_name or state.get("loaded_model_name")
    config = get_config()
    instance_id = state.get("loaded_instance_id")
    if not instance_id and target_model:
        listed = list_models_internal()
        if listed.get("ok"):
            for model in listed.get("models", []):
                if model.get("id") == target_model:
                    instances = model.get("loaded_instances", [])
                    if instances:
                        instance_id = instances[0].get("id")
                    break
    payload_candidates = []
    if instance_id:
        payload_candidates.append({"instance_id": instance_id})
    if target_model:
        payload_candidates.extend([{"model": target_model}, {"identifier": target_model}])
    if not payload_candidates:
        payload_candidates = [{}]
    last_result = None
    used_endpoint = None
    for payload in payload_candidates:
        endpoint, result = try_endpoints("POST", config["lmstudio"]["model_unload_endpoints"], payload=payload, timeout=120)
        if result.ok:
            used_endpoint = endpoint
            last_result = result
            break
        last_result = result
    if not last_result or not last_result.ok:
        return {"ok": False, "error": (last_result.error_message if last_result else "Unload failed"), "status": (last_result.status if last_result else 0)}
    if target_model and target_model == state.get("loaded_model_name"):
        state["loaded_model_name"] = None
        state["loaded_instance_id"] = None
        save_state(state)
    elif target_model is None:
        state["loaded_model_name"] = None
        state["loaded_instance_id"] = None
        save_state(state)
    invalidate_chat_endpoint_cache()
    log_event("model_unloaded", {"model": target_model, "endpoint": used_endpoint})
    return {"ok": True, "endpoint": used_endpoint, "response": last_result.data, "unloaded_model_name": target_model}


def score_text(output: str, keywords: list[str]) -> float:
    if not output.strip():
        return 0.0
    if not keywords:
        return 0.7 if len(output.strip()) > 20 else 0.3
    lower_output = output.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in lower_output)
    return round(hits / max(len(keywords), 1), 3)


def validate_output(output: str, validator: str, required_keys: list[str] | None = None) -> dict[str, Any]:
    text = output.strip()
    if validator == "three_words":
        words = text.split()
        return {"pass": len(words) == 3, "detail": f"{len(words)} words found", "score": 1.0 if len(words) == 3 else 0.0}
    elif validator == "five_numbered_items":
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        numbered = sum(1 for l in lines if re.match(r"^\d+[\.\)]\s", l))
        return {"pass": numbered == 5, "detail": f"{numbered} numbered items", "score": 1.0 if numbered == 5 else min(numbered / 5.0, 0.8)}
    elif validator == "yes_no_only":
        clean = text.upper().strip()
        is_valid = clean in ("YES", "NO")
        return {"pass": is_valid, "detail": f"Got: {text[:50]}", "score": 1.0 if is_valid else 0.0}
    elif validator == "tool_call_json":
        try:
            parsed = json.loads(text)
            has_tool = "tool" in parsed or "function" in parsed
            has_args = "arguments" in parsed or "args" in parsed or "parameters" in parsed
            return {"pass": has_tool and has_args, "detail": "Valid tool call JSON" if (has_tool and has_args) else f"Missing keys in: {list(parsed.keys())}", "score": 1.0 if (has_tool and has_args) else 0.3}
        except (json.JSONDecodeError, TypeError):
            return {"pass": False, "detail": "Not valid JSON", "score": 0.0}
    elif validator == "json_valid":
        try:
            parsed = json.loads(text)
            keys_ok = all(k in parsed for k in (required_keys or []))
            return {"pass": isinstance(parsed, dict) and keys_ok, "detail": f"Keys: {list(parsed.keys()) if isinstance(parsed, dict) else 'not dict'}", "score": 1.0 if (isinstance(parsed, dict) and keys_ok) else 0.3}
        except (json.JSONDecodeError, TypeError):
            return {"pass": False, "detail": "Not valid JSON", "score": 0.0}
    elif validator == "json_array_of_objects":
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, list) or len(parsed) < 1:
                return {"pass": False, "detail": "Not a non-empty array", "score": 0.0}
            all_obj = all(isinstance(o, dict) for o in parsed)
            keys_ok = all(all(k in o for k in (required_keys or [])) for o in parsed)
            return {"pass": all_obj and keys_ok, "detail": f"{len(parsed)} objects", "score": 1.0 if (all_obj and keys_ok) else 0.3}
        except (json.JSONDecodeError, TypeError):
            return {"pass": False, "detail": "Not valid JSON", "score": 0.0}
    return {"pass": True, "detail": "No validator", "score": 0.5}


def extract_chat_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            text = first.get("text")
            if isinstance(text, str):
                return text
    if isinstance(data.get("content"), str):
        return data["content"]
    return ""


def find_working_chat_endpoint(force_refresh: bool = False) -> str:
    global _CACHED_CHAT_ENDPOINT
    state = get_state()
    if not force_refresh and _CACHED_CHAT_ENDPOINT:
        return _CACHED_CHAT_ENDPOINT
    config = get_config()
    candidate_model = state.get("loaded_model_name")
    if not candidate_model:
        listed = list_models_internal()
        if listed.get("ok") and listed.get("models"):
            candidate_model = listed["models"][0].get("id") or listed["models"][0].get("name")
    for endpoint in config["lmstudio"]["chat_endpoints"]:
        payload: dict[str, Any] = {"messages": [{"role": "user", "content": "reply with ok"}], "temperature": 0.0, "max_tokens": 8}
        if candidate_model:
            payload["model"] = candidate_model
        result = http_request_json("POST", endpoint, payload=payload, timeout=10)
        if result.ok:
            _CACHED_CHAT_ENDPOINT = endpoint
            state["last_chat_endpoint"] = endpoint
            save_state(state)
            return endpoint
    fallback = config["lmstudio"]["chat_endpoints"][0]
    _CACHED_CHAT_ENDPOINT = fallback
    return fallback


def chat_internal(
    prompt: str,
    model_name: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    state = get_state()
    profiles = get_profiles()
    config = get_config()
    active_profile_name = state.get("active_profile") or "general_balanced"
    active_profile = profiles["profiles"].get(active_profile_name, profiles["profiles"]["general_balanced"])
    target_model = model_name or state.get("loaded_model_name")
    endpoint = find_working_chat_endpoint()
    payload = {
        "messages": [],
        "temperature": temperature if temperature is not None else active_profile.get("temperature", 0.4),
        "max_tokens": max_tokens if max_tokens is not None else active_profile.get("max_tokens", 512),
        "top_p": active_profile.get("top_p", 0.9),
        "stream": False,
    }
    if target_model:
        payload["model"] = target_model
    if system_prompt:
        payload["messages"].append({"role": "system", "content": system_prompt})
    payload["messages"].append({"role": "user", "content": prompt})
    result = http_request_json("POST", endpoint, payload=payload, timeout=max(int(config["lmstudio"].get("timeout_seconds", 30)), 60))
    if not result.ok:
        invalidate_chat_endpoint_cache()
        retry_endpoint = find_working_chat_endpoint(force_refresh=True)
        if retry_endpoint != endpoint:
            result = http_request_json("POST", retry_endpoint, payload=payload, timeout=max(int(config["lmstudio"].get("timeout_seconds", 30)), 60))
            endpoint = retry_endpoint
    if not result.ok:
        return {"ok": False, "error": result.error_message or "Chat request failed", "status": result.status, "endpoint": endpoint}
    text = extract_chat_text(result.data)
    return {"ok": True, "endpoint": endpoint, "model": target_model, "text": text, "raw": result.data}


def _run_single_benchmark_prompt(
    item: dict[str, Any],
    model_name: str | None,
    temperature: float,
    max_tokens: int,
    repeat_count: int,
) -> tuple[list[float], list[float], list[dict[str, Any]], list[dict[str, Any]]]:
    collected_scores: list[float] = []
    collected_latencies: list[float] = []
    prompt_results: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    for _ in range(repeat_count):
        started = time.perf_counter()
        chat_result = chat_internal(prompt=item["prompt"], model_name=model_name, temperature=temperature, max_tokens=max_tokens)
        elapsed = round(time.perf_counter() - started, 3)
        output_text = chat_result.get("text", "") if chat_result.get("ok") else ""
        keywords = item.get("keywords", [])
        score = score_text(output_text, keywords)
        validator = item.get("validator")
        validation = validate_output(output_text, validator, item.get("required_keys")) if validator else None
        if validation:
            score = round((score + validation.get("score", 0)) / 2, 3) if keywords else validation.get("score", 0.5)
            validations.append({"validator": validator, **validation})
        collected_latencies.append(elapsed)
        collected_scores.append(score)
        prompt_results.append({
            "prompt": item["prompt"],
            "latency_seconds": elapsed,
            "score": score,
            "ok": chat_result.get("ok", False),
            "output_length": len(output_text),
            "output_preview": output_text[:300],
            "error": chat_result.get("error"),
            "validation": validation,
        })
    return collected_scores, collected_latencies, prompt_results, validations


def benchmark_model_internal(task_type: str = "general", model_name: str | None = None) -> dict[str, Any]:
    task_key = task_type if task_type in BENCHMARK_PROMPTS else "general"
    prompts = BENCHMARK_PROMPTS[task_key]
    config = get_config()
    repeat_count = int(config["benchmark"].get("repeat_count", 1))
    max_tokens = int(config["benchmark"].get("max_tokens", 120))
    temperature = float(config["benchmark"].get("temperature", 0.2))
    if model_name:
        load_result = load_model_internal(model_name)
        if not load_result.get("ok"):
            return {"ok": False, "error": f"Failed to load model {model_name}: {load_result.get('error')}"}
    all_scores: list[float] = []
    all_latencies: list[float] = []
    all_results: list[dict[str, Any]] = []
    for item in prompts:
        s, l, r, _ = _run_single_benchmark_prompt(item, model_name, temperature, max_tokens, repeat_count)
        all_scores.extend(s)
        all_latencies.extend(l)
        all_results.extend(r)
    avg_score = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0.0
    avg_latency = round(sum(all_latencies) / len(all_latencies), 3) if all_latencies else 0.0
    state = get_state()
    state["last_benchmark_at"] = now_iso()
    save_state(state)
    history = get_benchmark_history()
    run_entry = {
        "timestamp": now_iso(),
        "task_type": task_key,
        "model_name": model_name or state.get("loaded_model_name"),
        "average_score": avg_score,
        "average_latency_seconds": avg_latency,
        "results": all_results,
    }
    history["runs"].append(run_entry)
    save_benchmark_history(history)
    log_event("benchmark_run", {"task_type": task_key, "model_name": run_entry["model_name"], "average_score": avg_score, "average_latency_seconds": avg_latency})
    return {"ok": True, "task_type": task_key, "model_name": model_name or state.get("loaded_model_name"), "average_score": avg_score, "average_latency_seconds": avg_latency, "results": all_results}


def compare_models_internal(model_names: list[str], task_type: str = "general") -> dict[str, Any]:
    state = get_state()
    original_model = state.get("loaded_model_name")
    rankings: list[dict[str, Any]] = []
    try:
        for model_name in model_names:
            load_result = load_model_internal(model_name)
            if not load_result.get("ok"):
                rankings.append({"ok": False, "model_name": model_name, "error": load_result.get("error"), "average_score": 0.0, "average_latency_seconds": 9999.0})
                continue
            bench_result = benchmark_model_internal(task_type=task_type, model_name=model_name)
            rankings.append(bench_result)
        rankings.sort(key=lambda item: (float(item.get("average_score") or 0.0), -float(item.get("average_latency_seconds") or 9999.0)), reverse=True)
    finally:
        if original_model:
            load_model_internal(original_model)
        else:
            unload_model_internal()
    return {"ok": True, "task_type": task_type, "rankings": rankings, "winner": rankings[0] if rankings else None}


def flag_model_internal(model_name: str, score: float | None = None, note: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    registry = get_registry()
    models = registry.setdefault("models", {})
    model_entry = models.setdefault(model_name, {})
    if score is not None:
        model_entry["score"] = score
    if note is not None:
        model_entry["note"] = note
    if tags is not None:
        model_entry["tags"] = tags
    model_entry["updated_at"] = now_iso()
    save_registry(registry)
    return {"ok": True, "model_name": model_name, "entry": model_entry}


def set_profile_internal(profile_name: str) -> dict[str, Any]:
    profiles = get_profiles()
    if profile_name not in profiles["profiles"]:
        return {"ok": False, "error": f"Unknown profile: {profile_name}", "available_profiles": sorted(profiles["profiles"].keys())}
    state = get_state()
    state["active_profile"] = profile_name
    save_state(state)
    return {"ok": True, "active_profile": profile_name, "settings": profiles["profiles"][profile_name]}


def auto_tune_model_internal(task_type: str = "general") -> dict[str, Any]:
    profiles = get_profiles()
    system_info = get_system_info()
    available_mem = system_info["memory"].get("available_gb")
    task_defaults = profiles.get("task_defaults", {})
    if available_mem is not None and available_mem < 6:
        selected = "low_memory_safe"
    else:
        selected = task_defaults.get(task_type, "general_balanced")
    result = set_profile_internal(selected)
    return {"ok": result.get("ok", False), "task_type": task_type, "selected_profile": selected, "system_info": system_info, "profile_result": result}


def resource_profile_model_internal(model_name: str | None = None) -> dict[str, Any]:
    target = model_name or get_state().get("loaded_model_name")
    if not target:
        return {"ok": False, "error": "No model specified and no model currently loaded"}
    state = get_state()
    was_loaded = state.get("loaded_model_name")
    ram_before = get_memory_snapshot()
    load_start = time.perf_counter()
    load_result = load_model_internal(target)
    load_time = round(time.perf_counter() - load_start, 3)
    if not load_result.get("ok"):
        return {"ok": False, "error": f"Failed to load: {load_result.get('error')}"}
    ram_after_load = get_memory_snapshot()
    chat_result = chat_internal(prompt="Say OK.", model_name=target, max_tokens=8, temperature=0.0)
    ram_during = get_memory_snapshot()
    unload_start = time.perf_counter()
    unload_model_internal(target)
    unload_time = round(time.perf_counter() - unload_start, 3)
    ram_after_unload = get_memory_snapshot()
    if was_loaded and was_loaded != target:
        load_model_internal(was_loaded)
    ram_delta_loaded = None
    if ram_before.get("process_rss_mb") and ram_after_load.get("process_rss_mb"):
        ram_delta_loaded = round(ram_after_load["process_rss_mb"] - ram_before["process_rss_mb"], 2)
    ram_delta_inference = None
    if ram_after_load.get("process_rss_mb") and ram_during.get("process_rss_mb"):
        ram_delta_inference = round(ram_during["process_rss_mb"] - ram_after_load["process_rss_mb"], 2)
    return {
        "ok": True,
        "model_name": target,
        "load_time_seconds": load_time,
        "unload_time_seconds": unload_time,
        "ram_before_load": ram_before,
        "ram_after_load": ram_after_load,
        "ram_during_inference": ram_during,
        "ram_after_unload": ram_after_unload,
        "ram_delta_on_load_mb": ram_delta_loaded,
        "ram_delta_during_inference_mb": ram_delta_inference,
    }


def agent_compatibility_test_internal(model_name: str | None = None) -> dict[str, Any]:
    target = model_name or get_state().get("loaded_model_name")
    if not target:
        return {"ok": False, "error": "No model specified and no model currently loaded"}
    config = get_config()
    max_tokens = int(config["benchmark"].get("max_tokens", 200))
    category_scores: dict[str, list[float]] = {}
    results: list[dict[str, Any]] = []
    for item in BENCHMARK_PROMPTS["agent_compatibility"]:
        category = item.get("category", "general")
        started = time.perf_counter()
        chat_result = chat_internal(prompt=item["prompt"], model_name=target, temperature=0.2, max_tokens=max_tokens)
        elapsed = round(time.perf_counter() - started, 3)
        output_text = chat_result.get("text", "") if chat_result.get("ok") else ""
        keywords = item.get("keywords", [])
        score = score_text(output_text, keywords)
        validator = item.get("validator")
        if validator:
            validation = validate_output(output_text, validator, item.get("required_keys"))
            score = round((score + validation.get("score", 0)) / 2, 3) if keywords else validation.get("score", 0.5)
        category_scores.setdefault(category, []).append(score)
        results.append({"prompt": item["prompt"], "category": category, "score": score, "latency_seconds": elapsed, "output_preview": output_text[:300]})
    per_category = {cat: round(sum(s) / len(s), 3) for cat, s in category_scores.items()}
    overall = round(sum(per_category.values()) / len(per_category), 3) if per_category else 0.0
    log_event("agent_compatibility_test", {"model": target, "overall_score": overall, "per_category": per_category})
    return {"ok": True, "model_name": target, "overall_score": overall, "per_category": per_category, "results": results}


def structured_output_test_internal(model_name: str | None = None) -> dict[str, Any]:
    target = model_name or get_state().get("loaded_model_name")
    if not target:
        return {"ok": False, "error": "No model specified and no model currently loaded"}
    config = get_config()
    max_tokens = int(config["benchmark"].get("max_tokens", 300))
    results: list[dict[str, Any]] = []
    total_score = 0.0
    for item in BENCHMARK_PROMPTS["structured_output"]:
        started = time.perf_counter()
        chat_result = chat_internal(prompt=item["prompt"], model_name=target, temperature=0.0, max_tokens=max_tokens)
        elapsed = round(time.perf_counter() - started, 3)
        output_text = chat_result.get("text", "") if chat_result.get("ok") else ""
        validation = validate_output(output_text, item.get("validator", ""), item.get("required_keys"))
        has_extra_text = False
        try:
            parsed = json.loads(output_text.strip())
            stripped = output_text.strip()
            reparsed = json.dumps(parsed, ensure_ascii=False)
            if stripped != reparsed:
                has_extra_text = True
        except (json.JSONDecodeError, TypeError):
            pass
        score = validation.get("score", 0.0)
        if has_extra_text:
            score = round(score * 0.8, 3)
        total_score += score
        results.append({"prompt": item["prompt"], "score": score, "valid_json": validation.get("pass", False), "has_extra_text": has_extra_text, "latency_seconds": elapsed, "output_preview": output_text[:300]})
    avg_score = round(total_score / len(results), 3) if results else 0.0
    log_event("structured_output_test", {"model": target, "average_score": avg_score})
    return {"ok": True, "model_name": target, "average_score": avg_score, "test_count": len(results), "results": results}


def context_stress_test_internal(model_name: str | None = None) -> dict[str, Any]:
    target = model_name or get_state().get("loaded_model_name")
    if not target:
        return {"ok": False, "error": "No model specified and no model currently loaded"}
    levels = {
        "short": "What is 2+2? Reply with the number only.",
        "medium": "Summarize in 3 sentences: " + ("Containerization packages software into isolated units. " * 20),
        "long": "Based on the following text, answer: What is the main topic? TEXT: " + ("This is a passage about distributed systems and their benefits. " * 100),
    }
    results: list[dict[str, Any]] = []
    for level, prompt in levels.items():
        started = time.perf_counter()
        chat_result = chat_internal(prompt=prompt, model_name=target, temperature=0.2, max_tokens=200)
        elapsed = round(time.perf_counter() - started, 3)
        output_text = chat_result.get("text", "") if chat_result.get("ok") else ""
        results.append({"context_level": level, "prompt_length": len(prompt), "output_length": len(output_text), "latency_seconds": elapsed, "ok": chat_result.get("ok", False), "output_preview": output_text[:200]})
    latency_growth = None
    if len(results) >= 2 and results[0]["latency_seconds"] > 0:
        latency_growth = round(results[-1]["latency_seconds"] / max(results[0]["latency_seconds"], 0.001), 2)
    return {"ok": True, "model_name": target, "latency_growth_factor": latency_growth, "results": results}


def prompt_stability_test_internal(prompt: str = "Return a JSON object with key 'count' and an integer value.", repeats: int = 5, model_name: str | None = None) -> dict[str, Any]:
    target = model_name or get_state().get("loaded_model_name")
    if not target:
        return {"ok": False, "error": "No model specified and no model currently loaded"}
    outputs: list[str] = []
    latencies: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        chat_result = chat_internal(prompt=prompt, model_name=target, temperature=0.0, max_tokens=128)
        elapsed = round(time.perf_counter() - started, 3)
        text = chat_result.get("text", "") if chat_result.get("ok") else ""
        outputs.append(text)
        latencies.append(elapsed)
    unique_outputs = list(set(o.strip() for o in outputs))
    consistency = round(1.0 - (len(unique_outputs) - 1) / max(repeats, 1), 3) if repeats > 0 else 0.0
    avg_latency = round(sum(latencies) / len(latencies), 3) if latencies else 0.0
    format_stable = all(validate_output(o, "json_valid").get("pass", False) for o in outputs)
    return {"ok": True, "model_name": target, "repeats": repeats, "unique_outputs": len(unique_outputs), "consistency_score": consistency, "format_stable": format_stable, "average_latency": avg_latency, "outputs": [{"text": o[:200], "latency": l} for o, l in zip(outputs, latencies)]}


def recommend_best_model_internal(task_type: str = "general") -> dict[str, Any]:
    registry = get_registry()
    runs = registry.get("benchmark_runs", [])
    system_info = get_system_info()
    available_ram = system_info["memory"].get("available_gb")
    task_runs: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if run.get("task_type") == task_type and run.get("model_name"):
            model = run["model_name"]
            task_runs.setdefault(model, []).append(run)
    if not task_runs:
        return {"ok": True, "task_type": task_type, "recommendation": None, "reason": "No benchmark data available for this task type. Run benchmarks first.", "system_ram_gb": available_ram}
    rankings: list[dict[str, Any]] = []
    for model, model_runs in task_runs.items():
        avg_score = round(sum(r.get("average_score", 0) for r in model_runs) / len(model_runs), 3)
        avg_latency = round(sum(r.get("average_latency_seconds", 9999) for r in model_runs) / len(model_runs), 3)
        rankings.append({"model_name": model, "average_score": avg_score, "average_latency_seconds": avg_latency, "runs": len(model_runs)})
    rankings.sort(key=lambda r: (r["average_score"], -r["average_latency_seconds"]), reverse=True)
    best = rankings[0]
    return {"ok": True, "task_type": task_type, "recommendation": best["model_name"], "score": best["average_score"], "latency": best["average_latency_seconds"], "all_rankings": rankings, "system_ram_gb": available_ram}


def query_history_internal(model_filter: str | None = None, task_filter: str | None = None, limit: int = 50) -> dict[str, Any]:
    registry = get_registry()
    runs = registry.get("benchmark_runs", [])
    filtered = runs
    if model_filter:
        filtered = [r for r in filtered if model_filter.lower() in (r.get("model_name") or "").lower()]
    if task_filter:
        filtered = [r for r in filtered if r.get("task_type") == task_filter]
    filtered = filtered[-limit:]
    model_stats: dict[str, dict[str, Any]] = {}
    for run in filtered:
        model = run.get("model_name", "unknown")
        if model not in model_stats:
            model_stats[model] = {"scores": [], "latencies": [], "run_count": 0}
        model_stats[model]["scores"].append(run.get("average_score", 0))
        model_stats[model]["latencies"].append(run.get("average_latency_seconds", 0))
        model_stats[model]["run_count"] += 1
    summary = {}
    for model, stats in model_stats.items():
        summary[model] = {
            "avg_score": round(sum(stats["scores"]) / len(stats["scores"]), 3),
            "avg_latency": round(sum(stats["latencies"]) / len(stats["latencies"]), 3),
            "runs": stats["run_count"],
            "best_score": max(stats["scores"]),
            "fastest_run": min(stats["latencies"]),
        }
    return {"ok": True, "total_runs": len(filtered), "model_summary": summary, "runs": filtered}


def export_benchmark_report_internal(format: str = "json", model_filter: str | None = None) -> dict[str, Any]:
    registry = get_registry()
    runs = registry.get("benchmark_runs", [])
    if model_filter:
        runs = [r for r in runs if model_filter.lower() in (r.get("model_name") or "").lower()]
    ensure_dir_structure()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if format == "csv":
        path = BASE_DIR / f"benchmark_report-{timestamp}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "model", "task_type", "average_score", "average_latency", "result_count"])
            for r in runs:
                writer.writerow([r.get("timestamp"), r.get("model_name"), r.get("task_type"), r.get("average_score"), r.get("average_latency_seconds"), len(r.get("results", []))])
        return {"ok": True, "format": "csv", "path": str(path), "run_count": len(runs)}
    elif format == "markdown":
        path = BASE_DIR / f"benchmark_report-{timestamp}.md"
        lines = ["# Benchmark Report", f"Generated: {now_iso()}", f"Total runs: {len(runs)}", ""]
        lines.append("| Model | Task | Score | Latency |")
        lines.append("|-------|------|-------|---------|")
        for r in runs:
            lines.append(f"| {r.get('model_name', '?')} | {r.get('task_type', '?')} | {r.get('average_score', 0)} | {r.get('average_latency_seconds', 0)}s |")
        path.write_text("\n".join(lines), encoding="utf-8")
        return {"ok": True, "format": "markdown", "path": str(path), "run_count": len(runs)}
    else:
        path = BASE_DIR / f"benchmark_report-{timestamp}.json"
        path.write_text(json.dumps({"generated": now_iso(), "runs": runs}, indent=2), encoding="utf-8")
        return {"ok": True, "format": "json", "path": str(path), "run_count": len(runs)}


def self_check_internal() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        cfg = get_config()
        effective_url = os.environ.get("LMSTUDIO_BASE_URL") or cfg['lmstudio']['base_url']
        checks.append({"check": "config_valid", "ok": True, "detail": f"base_url={effective_url}"})
    except Exception as e:
        checks.append({"check": "config_valid", "ok": False, "detail": str(e)})
    try:
        result = http_request_json("GET", "/api/v1/models", timeout=5)
        checks.append({"check": "http_connectivity", "ok": result.ok, "detail": f"Status {result.status}" if result.ok else result.error_message})
    except Exception as e:
        checks.append({"check": "http_connectivity", "ok": False, "detail": str(e)})
    config = get_config()
    working_endpoints: list[str] = []
    for ep in config["lmstudio"]["model_list_endpoints"]:
        result = http_request_json("GET", ep, timeout=5)
        if result.ok:
            working_endpoints.append(ep)
    checks.append({"check": "endpoints_available", "ok": len(working_endpoints) > 0, "detail": f"Working: {working_endpoints}"})
    checks.append({"check": "python_env", "ok": True, "detail": f"{platform.python_version()}, mcp={'ok' if True else 'missing'}"})
    writable = BASE_DIR.exists() and os_access(BASE_DIR)
    checks.append({"check": "writable_data_dir", "ok": writable, "detail": str(BASE_DIR)})
    all_ok = all(c["ok"] for c in checks)
    return {"ok": all_ok, "checks": checks}


def os_access(path: Path) -> bool:
    try:
        test_file = path / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True
    except Exception:
        return False


def switch_model_safely_internal(target_model: str, test_prompt: str = "Say OK.", test_max_tokens: int = 32) -> dict[str, Any]:
    state = get_state()
    original_model = state.get("loaded_model_name")
    original_instance = state.get("loaded_instance_id")
    result = {"ok": True, "original_model": original_model, "target_model": target_model, "steps": []}
    try:
        load_result = load_model_internal(target_model)
        result["steps"].append({"action": "load_target", "ok": load_result.get("ok", False), "detail": load_result.get("error") if not load_result.get("ok") else "loaded"})
        if not load_result.get("ok"):
            result["ok"] = False
            return result
        chat_result = chat_internal(prompt=test_prompt, model_name=target_model, max_tokens=test_max_tokens, temperature=0.0)
        result["steps"].append({"action": "test_chat", "ok": chat_result.get("ok", False), "response_preview": chat_result.get("text", "")[:100]})
        result["test_response"] = chat_result.get("text", "")
    finally:
        if original_model:
            restore = load_model_internal(original_model)
            result["steps"].append({"action": "restore_original", "ok": restore.get("ok", False)})
        else:
            unload_model_internal()
            result["steps"].append({"action": "unload_target", "ok": True})
    return result


def best_model_for_task_internal(category: str = "coding") -> dict[str, Any]:
    category_map = {
        "coding": "coding",
        "json": "structured_output",
        "structured": "structured_output",
        "fast": "low_latency",
        "agent": "agent_compatibility",
        "creative": "creative",
        "general": "general",
        "long_context": "general",
        "low_ram": "general",
    }
    task_type = category_map.get(category, category)
    rec = recommend_best_model_internal(task_type=task_type)
    system_info = get_system_info()
    available_ram = system_info["memory"].get("available_gb")
    if category == "low_ram" and available_ram is not None:
        registry = get_registry()
        runs = registry.get("benchmark_runs", [])
        model_scores: dict[str, list[float]] = {}
        for run in runs:
            model = run.get("model_name", "")
            model_scores.setdefault(model, []).append(run.get("average_score", 0))
        if model_scores:
            avg = {m: sum(s) / len(s) for m, s in model_scores.items()}
            best = max(avg, key=avg.get)
            rec["recommendation"] = best
            rec["reason"] = f"Best overall score when RAM is limited ({available_ram}GB available)"
    return rec


@mcp.tool()
def health_check() -> dict[str, Any]:
    ensure_all_files()
    models = list_models_internal()
    return {"ok": True, "data_dir": str(BASE_DIR), "lmstudio_reachable": models.get("ok", False), "model_count": models.get("count", 0), "system": get_system_info(), "state": get_state()}


@mcp.tool()
def get_manager_config() -> dict[str, Any]:
    ensure_all_files()
    return {"ok": True, "config": get_config()}


@mcp.tool()
def update_manager_config(patch: dict[str, Any]) -> dict[str, Any]:
    ensure_all_files()
    current = get_config()
    backup_path = backup_file(CONFIG_FILE)
    updated = deep_merge(current, patch)
    save_config(updated)
    invalidate_chat_endpoint_cache()
    return {"ok": True, "backup_path": backup_path, "config": updated}


@mcp.tool()
def list_models() -> dict[str, Any]:
    ensure_all_files()
    return list_models_internal()


@mcp.tool()
def load_model(model_name: str) -> dict[str, Any]:
    ensure_all_files()
    return load_model_internal(model_name)


@mcp.tool()
def unload_model(model_name: str | None = None) -> dict[str, Any]:
    ensure_all_files()
    return unload_model_internal(model_name)


@mcp.tool()
def chat(prompt: str, model_name: str | None = None, system_prompt: str | None = None, temperature: float | None = None, max_tokens: int | None = None) -> dict[str, Any]:
    ensure_all_files()
    return chat_internal(prompt=prompt, model_name=model_name, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens)


@mcp.tool()
def benchmark_model(task_type: str = "general", model_name: str | None = None) -> dict[str, Any]:
    ensure_all_files()
    return benchmark_model_internal(task_type=task_type, model_name=model_name)


@mcp.tool()
def compare_models(model_names: list[str], task_type: str = "general") -> dict[str, Any]:
    ensure_all_files()
    return compare_models_internal(model_names=model_names, task_type=task_type)


@mcp.tool()
def flag_model(model_name: str, score: float | None = None, note: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    ensure_all_files()
    return flag_model_internal(model_name=model_name, score=score, note=note, tags=tags)


@mcp.tool()
def get_profiles_config() -> dict[str, Any]:
    ensure_all_files()
    return {"ok": True, "profiles": get_profiles(), "active_profile": get_state().get("active_profile")}


@mcp.tool()
def set_profile(profile_name: str) -> dict[str, Any]:
    ensure_all_files()
    return set_profile_internal(profile_name)


@mcp.tool()
def auto_tune_model(task_type: str = "general") -> dict[str, Any]:
    ensure_all_files()
    return auto_tune_model_internal(task_type)


@mcp.tool()
def get_runtime_state() -> dict[str, Any]:
    ensure_all_files()
    return {"ok": True, "state": get_state(), "system": get_system_info()}


@mcp.tool()
def backup_manager_files() -> dict[str, Any]:
    ensure_all_files()
    backups = {"config": backup_file(CONFIG_FILE), "profiles": backup_file(PROFILE_FILE), "registry": backup_file(REGISTRY_FILE), "state": backup_file(STATE_FILE)}
    return {"ok": True, "backups": backups}


@mcp.tool()
def benchmark_history(limit: int = 10) -> dict[str, Any]:
    ensure_all_files()
    history = get_benchmark_history()
    runs = history.get("runs", [])
    return {"ok": True, "count": len(runs), "runs": runs[-max(limit, 1):]}


@mcp.tool()
def view_registry(limit: int = 20) -> dict[str, Any]:
    ensure_all_files()
    registry = get_registry()
    events = registry.get("events", [])
    if not isinstance(events, list):
        events = []
    runs = registry.get("benchmark_runs", [])
    if not isinstance(runs, list):
        runs = []
    return {"ok": True, "models": registry.get("models", {}), "recent_events": events[-max(limit, 1):], "event_count": len(events), "benchmark_run_count": len(runs)}


@mcp.tool()
def clear_endpoint_cache() -> dict[str, Any]:
    ensure_all_files()
    invalidate_chat_endpoint_cache()
    return {"ok": True, "cleared": True}


@mcp.tool()
def agent_compatibility_test(model_name: str | None = None) -> dict[str, Any]:
    """Test how suitable a model is for AI-agent workflows. Returns per-category scores for instruction following, JSON format, tool awareness, and multi-step reasoning."""
    ensure_all_files()
    return agent_compatibility_test_internal(model_name=model_name)


@mcp.tool()
def structured_output_test(model_name: str | None = None) -> dict[str, Any]:
    """Test a model's ability to return valid JSON, exact keys, no extra text, and schema-constrained output."""
    ensure_all_files()
    return structured_output_test_internal(model_name=model_name)


@mcp.tool()
def context_stress_test(model_name: str | None = None) -> dict[str, Any]:
    """Test model behavior across short, medium, and long context prompts. Measures latency growth and output quality."""
    ensure_all_files()
    return context_stress_test_internal(model_name=model_name)


@mcp.tool()
def prompt_stability_test(prompt: str = "Return a JSON object with key 'count' and an integer value.", repeats: int = 5, model_name: str | None = None) -> dict[str, Any]:
    """Run the same prompt multiple times to measure output consistency and format stability."""
    ensure_all_files()
    return prompt_stability_test_internal(prompt=prompt, repeats=repeats, model_name=model_name)


@mcp.tool()
def resource_profile_model(model_name: str | None = None) -> dict[str, Any]:
    """Measure RAM before/after load, during inference, CPU usage, and load/unload times for a model."""
    ensure_all_files()
    return resource_profile_model_internal(model_name=model_name)


@mcp.tool()
def recommend_best_model(task_type: str = "general") -> dict[str, Any]:
    """Based on stored benchmarks and system RAM, recommend the best model for a given task type."""
    ensure_all_files()
    return recommend_best_model_internal(task_type=task_type)


@mcp.tool()
def switch_model_safely(target_model: str, test_prompt: str = "Say OK.", test_max_tokens: int = 32) -> dict[str, Any]:
    """Safely switch to a model, run a test prompt, then restore the previous model. Useful for automation."""
    ensure_all_files()
    return switch_model_safely_internal(target_model=target_model, test_prompt=test_prompt, test_max_tokens=test_max_tokens)


@mcp.tool()
def export_benchmark_report(format: str = "json", model_filter: str | None = None) -> dict[str, Any]:
    """Export benchmark history as JSON, CSV, or Markdown report."""
    ensure_all_files()
    return export_benchmark_report_internal(format=format, model_filter=model_filter)


@mcp.tool()
def self_check() -> dict[str, Any]:
    """Verify config validity, HTTP connectivity, endpoint availability, Python environment, and writable data directory."""
    ensure_all_files()
    return self_check_internal()


@mcp.tool()
def query_history(model_filter: str | None = None, task_filter: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Query benchmark history with optional filters by model name and task type. Returns per-model summary stats."""
    ensure_all_files()
    return query_history_internal(model_filter=model_filter, task_filter=task_filter, limit=limit)


@mcp.tool()
def task_fit_score(task_type: str = "general", model_name: str | None = None) -> dict[str, Any]:
    """Quick evaluation of how well a model fits a specific task. Runs benchmark and returns fit score."""
    ensure_all_files()
    bench = benchmark_model_internal(task_type=task_type, model_name=model_name)
    if not bench.get("ok"):
        return bench
    latency = bench.get("average_latency_seconds", 0)
    score = bench.get("average_score", 0)
    speed_factor = max(0, 1.0 - (latency / 30.0))
    fit_score = round(score * 0.7 + speed_factor * 0.3, 3)
    return {"ok": True, "model_name": bench.get("model_name"), "task_type": task_type, "fit_score": fit_score, "benchmark_score": score, "average_latency": latency}


@mcp.tool()
def best_model_for_task(category: str = "general") -> dict[str, Any]:
    """Quick lookup: best model for a category. Categories: coding, json, fast, agent, creative, general, low_ram, long_context."""
    ensure_all_files()
    return best_model_for_task_internal(category=category)


def main() -> None:
    ensure_all_files()
    mcp.run()


if __name__ == "__main__":
    main()
