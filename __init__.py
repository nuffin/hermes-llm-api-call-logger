"""llm-api-call-logger plugin.

Hooks into ``pre_api_request`` and ``post_api_request`` to record every
LLM API call with full request/response details. Correlates pre/post data
via ``api_request_id``.

DB location is resolved from ``observability.data_dir`` in Hermes
``config.yaml`` (per-plugin ``observability.llm-api-call-logger.data_dir``
override supported).  Falls back to ``~/.hermes/llm-call-log.db``.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

# ---- paths ------------------------------------------------------------------

def _read_data_dir_from_observability_config(obs: Any) -> str | None:
    """Extract ``data_dir`` from an ``observability`` config dict.

    Recognises three structures (in priority order):
      observability:
        llm-api-call-logger:
          data_dir: <path>          # 1. Plugin-specific (highest in-file)
        default:
          data_dir: <path>          # 2. All-plugins default
        data_dir: <path>            # 3. Legacy flat format (backward compat)
    """
    if not obs or not isinstance(obs, dict):
        return None

    # 1. Plugin-specific override (new format)
    plugin_cfg = obs.get("llm-api-call-logger")
    if isinstance(plugin_cfg, dict):
        val = plugin_cfg.get("data_dir")
        if val and isinstance(val, str):
            return val

    # 2. All-plugins default (new format)
    default_cfg = obs.get("default")
    if isinstance(default_cfg, dict):
        val = default_cfg.get("data_dir")
        if val and isinstance(val, str):
            return val

    # 3. Backward compatibility: legacy flat ``data_dir`` string
    val = obs.get("data_dir")
    if val and isinstance(val, str):
        return val

    return None


def _read_config_yaml(config_path: Path | None) -> dict:
    """Safely read and parse a YAML config file. Returns empty dict on failure."""
    if not config_path or not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path) as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _resolve_data_dir() -> Path:
    """Resolve the observability data directory with multi-layer priority.

    Priority chain (highest to lowest):
      1. ``LLM_API_CALL_DATA_DIR`` env var (plugin-specific)
      2. ``OBSERVABILITY_DATA_DIR`` env var (generic)
      3. Per-profile config:  ``observability.llm-api-call-logger.data_dir``
      4. Per-profile config:  ``observability.default.data_dir``
      5. Per-profile config:  ``observability.data_dir`` (legacy flat, backwards compat)
      6. Global config (from hermes root): same structure as steps 3-5
      7. Fallback:  ``~/.hermes``
    """
    # 1-2. Env var overrides
    env_val = os.environ.get("LLM_API_CALL_DATA_DIR", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    env_val = os.environ.get("OBSERVABILITY_DATA_DIR", "").strip()
    if env_val:
        return Path(env_val).expanduser()

    # 3-5. Per-profile config (from ``HERMES_HOME``)
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    profile_config_path = Path(hermes_home) / "config.yaml" if hermes_home else None
    data_dir = None
    if profile_config_path:
        config = _read_config_yaml(profile_config_path)
        data_dir = _read_data_dir_from_observability_config(
            config.get("observability")
        )

    # 6. Global config (from hermes root — shared by all profiles)
    if not data_dir:
        # Avoid re-reading the same file when ``HERMES_HOME`` *is* the root
        try:
            from hermes_constants import get_default_hermes_root
        except ImportError:
            get_default_hermes_root = None

        if get_default_hermes_root is not None:
            global_config_path = get_default_hermes_root() / "config.yaml"
            if (profile_config_path is None
                    or global_config_path.resolve() != profile_config_path.resolve()):
                config = _read_config_yaml(global_config_path)
                data_dir = _read_data_dir_from_observability_config(
                    config.get("observability")
                )

    # 7. Hard-coded fallback
    if not data_dir:
        data_dir = "~/.hermes"

    return Path(data_dir).expanduser()


_HERMES_PERSONAL = _resolve_data_dir()
_DB_PATH = _HERMES_PERSONAL / "llm-call-log.db"

# ---- async write queue ------------------------------------------------------

_lock = threading.Lock()
_queue: list[dict[str, Any]] = []
_flush_timer: threading.Timer | None = None
_FLUSH_INTERVAL = 3.0

# ---- pre/post correlation cache ---------------------------------------------
# pre_api_request stores request data here; post_api_request reads it back
# and combines with response data before writing the final record.

_request_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()
_MAX_CACHE_AGE = 300  # 5 min — clean stale entries that never got a response


def _clean_stale_cache() -> None:
    """Remove old cache entries that never received a post_api_request."""
    now = datetime.datetime.now()
    with _cache_lock:
        stale = [
            rid for rid, data in _request_cache.items()
            if (now - data["_ts"]).total_seconds() > _MAX_CACHE_AGE
        ]
        for rid in stale:
            del _request_cache[rid]


# ---- database ---------------------------------------------------------------


def _init_db() -> None:
    """Create the database and table if they don't exist."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_api_calls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                turn_id     TEXT,
                api_request_id TEXT,
                profile     TEXT DEFAULT '',
                workspace   TEXT DEFAULT '',
                worker      TEXT DEFAULT '',
                task_id     TEXT DEFAULT '',
                skill       TEXT DEFAULT '',
                model       TEXT NOT NULL,
                provider    TEXT NOT NULL,
                base_url    TEXT DEFAULT '',
                api_mode    TEXT DEFAULT '',
                prompt_tokens   INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens    INTEGER DEFAULT 0,
                cache_read_tokens  INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens   INTEGER DEFAULT 0,
                finish_reason   TEXT,
                api_duration    REAL DEFAULT 0.0,
                message_count   INTEGER DEFAULT 0,
                tool_count      INTEGER DEFAULT 0,
                approx_input_tokens INTEGER DEFAULT 0,
                request_char_count  INTEGER DEFAULT 0,
                assistant_tool_call_count INTEGER DEFAULT 0,
                raw_request     TEXT,
                raw_response    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_api_created_at ON llm_api_calls(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_api_session ON llm_api_calls(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_api_req_id ON llm_api_calls(api_request_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _flush_queue() -> None:
    """Write all queued records to the database in a single transaction."""
    global _flush_timer
    _flush_timer = None

    with _lock:
        records = list(_queue)
        _queue.clear()

    if not records:
        return

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            conn.executemany(
                """
                INSERT INTO llm_api_calls
                    (session_id, turn_id, api_request_id,
                     profile, workspace, worker, task_id, skill,
                     model, provider, base_url, api_mode,
                     prompt_tokens, completion_tokens, total_tokens,
                     cache_read_tokens, cache_write_tokens, reasoning_tokens,
                     finish_reason, api_duration, message_count,
                     tool_count, approx_input_tokens, request_char_count,
                     assistant_tool_call_count,
                     raw_request, raw_response, created_at)
                VALUES
                    (:session_id, :turn_id, :api_request_id,
                     :profile, :workspace, :worker, :task_id, :skill,
                     :model, :provider, :base_url, :api_mode,
                     :prompt_tokens, :completion_tokens, :total_tokens,
                     :cache_read_tokens, :cache_write_tokens, :reasoning_tokens,
                     :finish_reason, :api_duration, :message_count,
                     :tool_count, :approx_input_tokens, :request_char_count,
                     :assistant_tool_call_count,
                     :raw_request, :raw_response, :created_at)
                """,
                records,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _schedule_flush() -> None:
    """Start or restart the async flush timer."""
    global _flush_timer
    if _flush_timer is not None and _flush_timer.is_alive():
        _flush_timer.cancel()
    _flush_timer = threading.Timer(_FLUSH_INTERVAL, _flush_queue)
    _flush_timer.daemon = True
    _flush_timer.start()


def flush_now() -> None:
    """Force an immediate flush."""
    if _flush_timer is not None:
        _flush_timer.cancel()
    _flush_queue()


# ---- helpers ----------------------------------------------------------------


def _safe_json(val: Any, max_chars: int = 0) -> str | None:
    """Serialize a value to JSON string. Returns None on failure."""
    if val is None:
        return None
    try:
        s = json.dumps(val, ensure_ascii=False, default=str)
        if max_chars and len(s) > max_chars:
            s = s[:max_chars] + '...'
        return s
    except (TypeError, ValueError):
        return None


# ---- hook handlers ----------------------------------------------------------


def _on_pre_api_request(**kw: Any) -> None:
    """Cache request data for later correlation with response.

    Hook kwargs:
        api_request_id, session_id, turn_id, model, provider, base_url, api_mode,
        request_messages (list), request (sanitized payload),
        approx_input_tokens, request_char_count, message_count, tool_count,
        task_id, platform
    """
    api_request_id = kw.get("api_request_id", "")
    if not api_request_id:
        return

    _clean_stale_cache()

    with _cache_lock:
        _request_cache[api_request_id] = {
            "_ts": datetime.datetime.now(),
            "model": kw.get("model", ""),
            "provider": kw.get("provider", ""),
            "base_url": kw.get("base_url", ""),
            "api_mode": kw.get("api_mode", ""),
            "session_id": kw.get("session_id", ""),
            "turn_id": kw.get("turn_id", ""),
            "task_id": kw.get("task_id", ""),
            "platform": kw.get("platform", ""),
            "message_count": int(kw.get("message_count", 0)),
            "tool_count": int(kw.get("tool_count", 0)),
            "approx_input_tokens": int(kw.get("approx_input_tokens", 0)),
            "request_char_count": int(kw.get("request_char_count", 0)),
            "request_messages": _safe_json(kw.get("request_messages"), max_chars=100000),
            "raw_request": _safe_json(kw.get("request"), max_chars=100000),
        }


def _on_post_api_request(**kw: Any) -> None:
    """Combine cached request data with response data and write to DB."""
    usage: Any = kw.get("usage")
    if not usage or not isinstance(usage, dict):
        return

    api_request_id = kw.get("api_request_id", "")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check cache for request data
    req_data: dict[str, Any] = {}
    if api_request_id:
        with _cache_lock:
            cached = _request_cache.pop(api_request_id, None)
            if cached:
                req_data = cached

    record = {
        "session_id": req_data.get("session_id") or kw.get("session_id", ""),
        "turn_id": req_data.get("turn_id") or kw.get("turn_id", ""),
        "api_request_id": api_request_id or kw.get("api_request_id", ""),
        "profile": os.environ.get("HERMES_PROFILE", ""),
        "workspace": os.environ.get("HERMES_KANBAN_WORKSPACE", ""),
        "worker": os.environ.get("HERMES_KANBAN_TASK", ""),
        "task_id": req_data.get("task_id") or kw.get("task_id", ""),
        "skill": os.environ.get("HERMES_ACTIVE_SKILL", ""),
        "model": kw.get("model") or req_data.get("model", "unknown"),
        "provider": kw.get("provider") or req_data.get("provider", "unknown"),
        "base_url": req_data.get("base_url", ""),
        "api_mode": req_data.get("api_mode", ""),
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("output_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
        "cache_read_tokens": int(usage.get("cache_read_tokens", 0)),
        "cache_write_tokens": int(usage.get("cache_write_tokens", 0)),
        "reasoning_tokens": int(usage.get("reasoning_tokens", 0)),
        "finish_reason": kw.get("finish_reason", ""),
        "api_duration": float(kw.get("api_duration", 0)),
        "message_count": req_data.get("message_count") or int(kw.get("message_count", 0)),
        "tool_count": req_data.get("tool_count", 0),
        "approx_input_tokens": req_data.get("approx_input_tokens", 0),
        "request_char_count": req_data.get("request_char_count", 0),
        "assistant_tool_call_count": int(kw.get("assistant_tool_call_count", 0)),
        "raw_request": req_data.get("raw_request") or req_data.get("request_messages"),
        "raw_response": _safe_json(kw.get("response"), max_chars=50000),
        "created_at": now,
    }

    with _lock:
        _queue.append(record)

    _schedule_flush()


def _on_session_end(**kw: Any) -> None:
    """Flush any remaining records when the session ends."""
    flush_now()


# ---- plugin entry point -----------------------------------------------------


def register(ctx: Any) -> None:
    """Register both pre_api_request and post_api_request hooks."""
    _init_db()
    ctx.register_hook("pre_api_request", _on_pre_api_request)
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("on_session_end", _on_session_end)

    # Add migration columns
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        for col, col_type in (
            ("tool_count", "INTEGER DEFAULT 0"),
            ("approx_input_tokens", "INTEGER DEFAULT 0"),
            ("request_char_count", "INTEGER DEFAULT 0"),
            ("raw_request", "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE llm_api_calls ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()
    except Exception:
        pass
