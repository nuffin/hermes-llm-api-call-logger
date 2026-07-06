# hermes-llm-api-call-logger

A Hermes Agent plugin that records every LLM API call with full request
and response details, including tokens, tool calls, and raw payloads.

## Installation

```bash
git clone https://github.com/nuffin/hermes-llm-api-call-logger.git
cp -r hermes-llm-api-call-logger ~/.hermes/plugins/llm-api-call-logger
```

Enable the plugin in your Hermes `config.yaml`:

```yaml
plugins:
  enabled:
    - llm-api-call-logger
```

## Configuration

```yaml
observability:
  default:
    data_dir: ~/.hermes/personal
  llm-api-call-logger:
    data_dir: ~/.hermes/custom   # optional override
```

Priority: `LLM_API_CALL_DATA_DIR` env var → profile config →
global config (`~/.hermes/config.yaml`) → `~/.hermes`.

## Usage

In-session slash commands:

- `/calls status` — database path, size, record count
- `/calls latest [N]` — last N API calls (default: 5)
- `/calls summary [date]` — daily summary (default: today)

## License

MIT
