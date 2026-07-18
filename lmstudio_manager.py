from __future__ import annotations

import json
import platform
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Legacy file names superseded by STATE_FILE / REGISTRY_FILE. Kept for one-time
# migration in migrate_legacy_files(); no code reads them after that.
LEGACY_REGISTRY_FILE = BASE_DIR / "model_registry.json"
LEGACY_BENCHMARK_FILE = BASE_DIR / "benchmark_history.json"
LEGACY_STATE_FILE = BASE_DIR / "runtime_state.json"

_CACHED_CHAT_ENDPOINT: str | None = None

# Bump this integer whenever DEFAULT_CONFIG or DEFAULT_PROFILES change
# structurally (new keys, reordered endpoints, new profiles, etc.).  On boot
# the server compares this value to the on-disk ``_version`` field and, if the
# code's version is newer, auto-regenerates the file from the fresh defaults
# (backing up the old file first).  User-supplied values inside unchanged keys
# are NOT preserved across a version bump — the intent is a clean reset so the
# file always matches the running code's expectations.
_CONFIG_VERSION: int = 1


DEFAULT_CONFIG: dict[str, Any] = {
    "_version": _CONFIG_VERSION,
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234",
        "timeout_seconds": 30,
        # Spec-correct endpoints first; older/alternative variants kept as
        # fallbacks so the server degrades gracefully across LM Studio versions.
        "model_list_endpoints": [
            "/api/v1/models",
            "/v1/models",
            "/api/v0/models",
        ],
        "model_load_endpoints": [
            "/api/v1/models/load",
            "/api/v1/model/load",
            "/api/v0/model/load",
        ],
        "model_unload_endpoints": [
            "/api/v1/models/unload",
            "/api/v1/model/unload",
        ],
        "chat_endpoints": [
            "/v1/chat/completions",
            "/api/v1/chat",
            "/api/v0/chat/completions",
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
    },
    "task_defaults": {
        "general": "general_balanced",
        "coding": "coding_precise",
        "analysis": "analysis",
        "creative": "creative_open",
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
    """Like ``ensure_json_file`` but also auto-regenerates the file when the
    on-disk ``_version`` field is older than the code's ``_CONFIG_VERSION``.

    This prevents stale on-disk config/profile files from silently overriding
    structural changes to ``DEFAULT_CONFIG`` or ``DEFAULT_PROFILES``.  When a
    version mismatch is detected the old file is backed up and a fresh copy is
    written from the code's current defaults.

    Used exclusively for schema files (config, profiles).  State and registry
    files are *user data* and must never be auto-reset.
    """
    if not path.exists():
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")
        return

    try:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Corrupt JSON — treat as version mismatch
        on_disk_version = -1
    else:
        on_disk_version = on_disk.get("_version") if isinstance(on_disk, dict) else -1

    code_version = default_data.get("_version", -1)

    if on_disk_version != code_version:
        backup_file(path)
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")


def _load_legacy_json(path: Path) -> dict[str, Any] | None:
    """Read a legacy JSON file if it exists; return None otherwise."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def migrate_legacy_files() -> None:
    """One-time, idempotent migration from older file layout to current one.

    Old: runtime_state.json, model_registry.json, benchmark_history.json
    New: lmstudio_state.json,        lmstudio_registry.json

    Rules:
      - Only runs when the new file does not yet exist (idempotent).
      - Existing data is deep-merged over defaults; nothing is invented.
      - Old files are copied into backups/ and left in place, never deleted.
    """
    ensure_dir_structure()

    # --- State: runtime_state.json -> lmstudio_state.json ---
    if LEGACY_STATE_FILE.exists() and not STATE_FILE.exists():
        old = _load_legacy_json(LEGACY_STATE_FILE)
        merged = deep_merge(DEFAULT_STATE, old) if old else dict(DEFAULT_STATE)
        write_json(STATE_FILE, merged)
        backup_file(LEGACY_STATE_FILE)

    # --- Registry: model_registry.json + benchmark_history.json -> lmstudio_registry.json ---
    if not REGISTRY_FILE.exists() and (
        LEGACY_REGISTRY_FILE.exists() or LEGACY_BENCHMARK_FILE.exists()
    ):
        old_registry = _load_legacy_json(LEGACY_REGISTRY_FILE) or {}
        old_bench = _load_legacy_json(LEGACY_BENCHMARK_FILE) or {}

        models = old_registry.get("models") if isinstance(old_registry.get("models"), dict) else {}
        events = (
            old_registry.get("events")
            if isinstance(old_registry.get("events"), list)
            else []
        )
        runs = old_bench.get("runs") if isinstance(old_bench.get("runs"), list) else []

        merged = {
            "models": models,
            "events": events,
            "benchmark_runs": runs,
        }
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
    """Return benchmark runs under the legacy ``{"runs": [...]}`` wrapper.

    Storage now lives in ``lmstudio_registry.json`` under ``benchmark_runs``,
    but the {"runs": [...]} shape is preserved so existing call sites are
    unchanged.
    """
    registry = get_registry()
    return {"runs": registry.get("benchmark_runs", [])}


def save_benchmark_history(data: dict[str, Any]) -> None:
    registry = get_registry()
    registry["benchmark_runs"] = data.get("runs", []) if isinstance(data, dict) else []
    save_registry(registry)


def log_event(event_type: str, details: dict[str, Any]) -> None:
    """Append a lightweight event entry to the registry.

    Intentionally non-fatal: a logging failure must never break the calling
    flow (model load/unload/benchmark), so all errors are swallowed.
    """
    try:
        registry = get_registry()
        events = registry.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            registry["events"] = events
        events.append(
            {
                "timestamp": now_iso(),
                "type": event_type,
                "details": details,
            }
        )
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
    base_url = config["lmstudio"]["base_url"].rstrip("/")
    request_url = f"{base_url}{endpoint}"
    timeout_value = timeout or int(config["lmstudio"].get("timeout_seconds", 30))

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = config["lmstudio"].get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    content = json.dumps(payload).encode("utf-8") if payload is not None else None

    try:
        with httpx.Client(timeout=timeout_value) as client:
            resp = client.request(
                method.upper(),
                request_url,
                content=content,
                headers=headers,
            )
            # Mirror urllib semantics: non-2xx is treated as a structured error
            # result rather than raised, so callers can inspect status + body.
            if resp.status_code >= 400:
                raw = resp.text
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {"raw": raw}
                return HttpResult(
                    ok=False,
                    status=resp.status_code,
                    data=data,
                    error_message=f"HTTP {resp.status_code}",
                )

            raw = resp.text
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"raw": raw}
            return HttpResult(ok=True, status=resp.status_code, data=data)
    except httpx.HTTPError as exc:
        # Connection failures, timeouts, protocol errors, etc. There is no HTTP
        # status to report, so surface status 0 like the previous urllib path.
        return HttpResult(
            ok=False,
            status=0,
            data={},
            error_message=str(exc),
        )


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

    info = {
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
    return info


def list_models_internal() -> dict[str, Any]:
    config = get_config()
    endpoint, result = try_endpoints("GET", config["lmstudio"]["model_list_endpoints"])

    if not result.ok:
        return {
            "ok": False,
            "error": result.error_message or "Failed to query model list",
            "status": result.status,
        }

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
            model_id = (
                item.get("id")
                or item.get("key")
                or item.get("model")
                or item.get("name")
            )
            models.append(
                {
                    "id": model_id,
                    "name": item.get("display_name") or item.get("name") or model_id,
                    "loaded_instances": item.get("loaded_instances", []),
                    "raw": item,
                }
            )

    return {
        "ok": True,
        "endpoint": endpoint,
        "models": models,
        "count": len(models),
    }


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
    payload_variants = [
        {"model": model_name},
        {"identifier": model_name},
        {"model_name": model_name},
    ]

    last_result = None
    used_endpoint = None

    for payload in payload_variants:
        endpoint, result = try_endpoints(
            "POST",
            config["lmstudio"]["model_load_endpoints"],
            payload=payload,
            timeout=120,
        )
        if result.ok:
            used_endpoint = endpoint
            last_result = result
            break
        last_result = result

    if not last_result or not last_result.ok:
        return {
            "ok": False,
            "error": (last_result.error_message if last_result else "Load failed"),
            "status": (last_result.status if last_result else 0),
        }

    state = get_state()
    state["loaded_model_name"] = model_name

    # Discover instance_id from the live model list (load response omits it).
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

    return {
        "ok": True,
        "endpoint": used_endpoint,
        "response": last_result.data,
        "loaded_model_name": model_name,
    }


def unload_model_internal(model_name: str | None = None) -> dict[str, Any]:
    state = get_state()
    target_model = model_name or state.get("loaded_model_name")
    config = get_config()

    # Discover instance_id: prefer stored value, then query live model list.
    instance_id = state.get("loaded_instance_id")
    if not instance_id and target_model:
        listed = list_models_internal()
        if listed.get("ok"):
            for model in listed.get("models", []):
                model_id = model.get("id")
                if model_id == target_model:
                    instances = model.get("loaded_instances", [])
                    if instances:
                        instance_id = instances[0].get("id")
                    break

    # Build payload candidates: instance_id is required by current LM Studio.
    payload_candidates = []
    if instance_id:
        payload_candidates.append({"instance_id": instance_id})
    if target_model:
        payload_candidates.extend([
            {"model": target_model},
            {"identifier": target_model},
        ])
    if not payload_candidates:
        payload_candidates = [{}]

    last_result = None
    used_endpoint = None

    for payload in payload_candidates:
        endpoint, result = try_endpoints(
            "POST",
            config["lmstudio"]["model_unload_endpoints"],
            payload=payload,
            timeout=120,
        )
        if result.ok:
            used_endpoint = endpoint
            last_result = result
            break
        last_result = result

    if not last_result or not last_result.ok:
        return {
            "ok": False,
            "error": (last_result.error_message if last_result else "Unload failed"),
            "status": (last_result.status if last_result else 0),
        }

    if target_model and target_model == state.get("loaded_model_name"):
        state["loaded_model_name"] = None
        state["loaded_instance_id"] = None
        save_state(state)
    elif target_model is None:
        state["loaded_model_name"] = None
        state["loaded_instance_id"] = None
        save_state(state)

    invalidate_chat_endpoint_cache()

    log_event(
        "model_unloaded",
        {"model": target_model, "endpoint": used_endpoint},
    )

    return {
        "ok": True,
        "endpoint": used_endpoint,
        "response": last_result.data,
        "unloaded_model_name": target_model,
    }


def score_text(output: str, keywords: list[str]) -> float:
    if not output.strip():
        return 0.0
    if not keywords:
        return 0.7 if len(output.strip()) > 20 else 0.3

    lower_output = output.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in lower_output)
    return round(hits / max(len(keywords), 1), 3)


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
            candidate_model = (
                listed["models"][0].get("id")
                or listed["models"][0].get("name")
            )

    for endpoint in config["lmstudio"]["chat_endpoints"]:
        payload = {
            "messages": [{"role": "user", "content": "reply with ok"}],
            "temperature": 0.0,
            "max_tokens": 8,
        }
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
    active_profile = profiles["profiles"].get(
        active_profile_name,
        profiles["profiles"]["general_balanced"],
    )

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

    result = http_request_json(
        "POST",
        endpoint,
        payload=payload,
        timeout=max(int(config["lmstudio"].get("timeout_seconds", 30)), 60),
    )

    if not result.ok:
        invalidate_chat_endpoint_cache()
        retry_endpoint = find_working_chat_endpoint(force_refresh=True)
        if retry_endpoint != endpoint:
            result = http_request_json(
                "POST",
                retry_endpoint,
                payload=payload,
                timeout=max(int(config["lmstudio"].get("timeout_seconds", 30)), 60),
            )
            endpoint = retry_endpoint

    if not result.ok:
        return {
            "ok": False,
            "error": result.error_message or "Chat request failed",
            "status": result.status,
            "endpoint": endpoint,
        }

    text = extract_chat_text(result.data)

    return {
        "ok": True,
        "endpoint": endpoint,
        "model": target_model,
        "text": text,
        "raw": result.data,
    }


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
            return {
                "ok": False,
                "error": f"Failed to load model {model_name}: {load_result.get('error')}",
            }

    collected_scores: list[float] = []
    collected_latencies: list[float] = []
    prompt_results: list[dict[str, Any]] = []

    for item in prompts:
        for _ in range(repeat_count):
            started = time.perf_counter()
            chat_result = chat_internal(
                prompt=item["prompt"],
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed = round(time.perf_counter() - started, 3)

            output_text = chat_result.get("text", "") if chat_result.get("ok") else ""
            score = score_text(output_text, item.get("keywords", []))

            collected_latencies.append(elapsed)
            collected_scores.append(score)
            prompt_results.append(
                {
                    "prompt": item["prompt"],
                    "latency_seconds": elapsed,
                    "score": score,
                    "ok": chat_result.get("ok", False),
                    "output_preview": output_text[:300],
                    "error": chat_result.get("error"),
                }
            )

    avg_score = round(sum(collected_scores) / len(collected_scores), 3) if collected_scores else 0.0
    avg_latency = round(sum(collected_latencies) / len(collected_latencies), 3) if collected_latencies else 0.0

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
        "results": prompt_results,
    }
    history["runs"].append(run_entry)
    save_benchmark_history(history)

    log_event(
        "benchmark_run",
        {
            "task_type": task_key,
            "model_name": run_entry["model_name"],
            "average_score": avg_score,
            "average_latency_seconds": avg_latency,
        },
    )

    return {
        "ok": True,
        "task_type": task_key,
        "model_name": model_name or state.get("loaded_model_name"),
        "average_score": avg_score,
        "average_latency_seconds": avg_latency,
        "results": prompt_results,
    }


def compare_models_internal(model_names: list[str], task_type: str = "general") -> dict[str, Any]:
    state = get_state()
    original_model = state.get("loaded_model_name")
    rankings: list[dict[str, Any]] = []

    try:
        for model_name in model_names:
            load_result = load_model_internal(model_name)
            if not load_result.get("ok"):
                rankings.append(
                    {
                        "ok": False,
                        "model_name": model_name,
                        "error": load_result.get("error"),
                        "average_score": 0.0,
                        "average_latency_seconds": 9999.0,
                    }
                )
                continue

            bench_result = benchmark_model_internal(task_type=task_type, model_name=model_name)
            rankings.append(bench_result)

        rankings.sort(
            key=lambda item: (
                float(item.get("average_score") or 0.0),
                -float(item.get("average_latency_seconds") or 9999.0),
            ),
            reverse=True,
        )
    finally:
        if original_model:
            load_model_internal(original_model)
        else:
            unload_model_internal()

    return {
        "ok": True,
        "task_type": task_type,
        "rankings": rankings,
        "winner": rankings[0] if rankings else None,
    }


def flag_model_internal(
    model_name: str,
    score: float | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
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

    return {
        "ok": True,
        "model_name": model_name,
        "entry": model_entry,
    }


def set_profile_internal(profile_name: str) -> dict[str, Any]:
    profiles = get_profiles()
    if profile_name not in profiles["profiles"]:
        return {
            "ok": False,
            "error": f"Unknown profile: {profile_name}",
            "available_profiles": sorted(profiles["profiles"].keys()),
        }

    state = get_state()
    state["active_profile"] = profile_name
    save_state(state)

    return {
        "ok": True,
        "active_profile": profile_name,
        "settings": profiles["profiles"][profile_name],
    }


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
    return {
        "ok": result.get("ok", False),
        "task_type": task_type,
        "selected_profile": selected,
        "system_info": system_info,
        "profile_result": result,
    }


@mcp.tool()
def health_check() -> dict[str, Any]:
    ensure_all_files()
    models = list_models_internal()
    return {
        "ok": True,
        "data_dir": str(BASE_DIR),
        "lmstudio_reachable": models.get("ok", False),
        "model_count": models.get("count", 0),
        "system": get_system_info(),
        "state": get_state(),
    }


@mcp.tool()
def get_manager_config() -> dict[str, Any]:
    ensure_all_files()
    return {
        "ok": True,
        "config": get_config(),
    }


@mcp.tool()
def update_manager_config(patch: dict[str, Any]) -> dict[str, Any]:
    ensure_all_files()
    current = get_config()
    backup_path = backup_file(CONFIG_FILE)
    updated = deep_merge(current, patch)
    save_config(updated)
    invalidate_chat_endpoint_cache()
    return {
        "ok": True,
        "backup_path": backup_path,
        "config": updated,
    }


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
def chat(
    prompt: str,
    model_name: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    ensure_all_files()
    return chat_internal(
        prompt=prompt,
        model_name=model_name,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


@mcp.tool()
def benchmark_model(
    task_type: str = "general",
    model_name: str | None = None,
) -> dict[str, Any]:
    ensure_all_files()
    return benchmark_model_internal(task_type=task_type, model_name=model_name)


@mcp.tool()
def compare_models(
    model_names: list[str],
    task_type: str = "general",
) -> dict[str, Any]:
    ensure_all_files()
    return compare_models_internal(model_names=model_names, task_type=task_type)


@mcp.tool()
def flag_model(
    model_name: str,
    score: float | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    ensure_all_files()
    return flag_model_internal(model_name=model_name, score=score, note=note, tags=tags)


@mcp.tool()
def get_profiles_config() -> dict[str, Any]:
    ensure_all_files()
    return {
        "ok": True,
        "profiles": get_profiles(),
        "active_profile": get_state().get("active_profile"),
    }


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
    return {
        "ok": True,
        "state": get_state(),
        "system": get_system_info(),
    }


@mcp.tool()
def backup_manager_files() -> dict[str, Any]:
    ensure_all_files()
    backups = {
        "config": backup_file(CONFIG_FILE),
        "profiles": backup_file(PROFILE_FILE),
        "registry": backup_file(REGISTRY_FILE),
        "state": backup_file(STATE_FILE),
    }
    return {
        "ok": True,
        "backups": backups,
    }


@mcp.tool()
def benchmark_history(limit: int = 10) -> dict[str, Any]:
    ensure_all_files()
    history = get_benchmark_history()
    runs = history.get("runs", [])
    return {
        "ok": True,
        "count": len(runs),
        "runs": runs[-max(limit, 1):],
    }


@mcp.tool()
def view_registry(limit: int = 20) -> dict[str, Any]:
    """Expose the registry summary as an MCP tool.

    Named ``view_registry`` (not ``get_registry``) to avoid shadowing the
    internal ``get_registry()`` persistence helper used throughout this module.
    """
    ensure_all_files()
    registry = get_registry()
    events = registry.get("events", [])
    if not isinstance(events, list):
        events = []
    runs = registry.get("benchmark_runs", [])
    if not isinstance(runs, list):
        runs = []
    return {
        "ok": True,
        "models": registry.get("models", {}),
        "recent_events": events[-max(limit, 1):],
        "event_count": len(events),
        "benchmark_run_count": len(runs),
    }


@mcp.tool()
def clear_endpoint_cache() -> dict[str, Any]:
    ensure_all_files()
    invalidate_chat_endpoint_cache()
    return {
        "ok": True,
        "cleared": True,
    }


def main() -> None:
    ensure_all_files()
    mcp.run()


if __name__ == "__main__":
    main()
