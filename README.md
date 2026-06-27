# hermes-llm-api-call-logger

Hermes plugin: log every LLM API call with full request/response details.

Hooks into `pre_api_request` and `post_api_request` to correlate request
and response data via `api_request_id`, recording full payloads to SQLite.

## Data Location

Configured via `observability.data_dir` in Hermes `config.yaml`.
Defaults to `~/.hermes/` (database: `<data_dir>/llm-call-log.db`).

## Installation

```bash
ln -sf /path/to/hermes-llm-api-call-logger ~/.hermes/plugins/llm-api-call-logger
hermes plugins enable llm-api-call-logger
```
