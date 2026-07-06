# hermes-llm-api-call-logger

Hermes Agent plugin — log every LLM API call with full request/response details.

Correlates pre-request and post-request data via `api_request_id` and records
everything (tokens, tool calls, raw request/response payloads, profile,
workspace, task context) to a local SQLite database.

## Install

Symlink into your Hermes plugins directory:

```bash
ln -s "$PWD" ~/.hermes/plugins/llm-api-call-logger
```

Or into a specific profile's plugins:

```bash
ln -s "$PWD" ~/.hermes/profiles/<profile>/plugins/llm-api-call-logger
```

Then enable in `config.yaml`:

```yaml
plugins:
  enabled:
    - llm-api-call-logger
```

## Configuration

```yaml
observability:
  default:
    data_dir: ~/.hermes/personal    # shared data dir for all observability plugins
  llm-api-call-logger:
    data_dir: ~/.hermes/custom      # plugin-specific override (optional)
```

Priority: `LLM_API_CALL_DATA_DIR` env var → profile config → global
config (`~/.hermes/config.yaml`) → `~/.hermes`.

## Data

| Path | Content |
|------|---------|
| `<data_dir>/llm-call-log.db` | SQLite database of all LLM API calls |

## Usage

### Slash commands (in-session)

- `/calls status` — DB location, size, record count
- `/calls latest [N]` — last N API calls (default 5)
- `/calls summary [yesterday|2026-06-17]` — daily summary (default today)

## Schema

```sql
llm_api_calls (
    id                    INTEGER PRIMARY KEY,
    session_id            TEXT NOT NULL,
    turn_id               TEXT,
    api_request_id        TEXT,
    profile               TEXT,
    workspace             TEXT,
    worker                TEXT,
    task_id               TEXT,
    skill                 TEXT,
    model                 TEXT NOT NULL,
    provider              TEXT NOT NULL,
    base_url              TEXT,
    api_mode              TEXT,
    prompt_tokens         INTEGER,
    completion_tokens     INTEGER,
    total_tokens          INTEGER,
    cache_read_tokens     INTEGER,
    cache_write_tokens    INTEGER,
    reasoning_tokens      INTEGER,
    finish_reason         TEXT,
    api_duration          REAL,
    message_count         INTEGER,
    tool_count            INTEGER,
    approx_input_tokens   INTEGER,
    request_char_count    INTEGER,
    assistant_tool_call_count INTEGER,
    raw_request           TEXT,
    raw_response          TEXT,
    created_at            TEXT NOT NULL
)
```
