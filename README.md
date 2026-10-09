# log-agent

Automated Linux log monitoring: scans configured log files for critical or
suspicious lines, has an AI summarise what happened, sends a Pushover
notification, and saves a timestamped JSON report for later review.

## Features

- Watches multiple log sources (e.g. `syslog`, `auth.log`) with per-source
  regex patterns and exclude patterns to silence noisy lines.
- Remembers the last-read byte position and inode of every log file, so
  restarts never resend old events.
- Sends a Pushover alert with an AI-generated summary when critical lines
  are found, or an "All OK" notification when nothing was detected.
- Saves a JSON report per alert with retention and pruning.
- Configurable AI backend: Mammouth, OpenAI, Claude (Anthropic) or any
  OpenAI-compatible endpoint (OpenRouter, DeepSeek, Groq, Mistral, Ollama,
  LM Studio, ...).

## Requirements

- Python 3.8+ with the `requests` package
- A Pushover account (application token + user key)
- An API key for one of the AI providers

## Installation

1. Copy the files to the target server (e.g. `/opt/log-agent/`).
2. Rename the example configs and edit them:

   ```sh
   cp log_agent.conf.example log_agent.conf
   cp exclude_patterns.conf.example exclude_patterns.conf
   ```

3. Fill in at least `PUSHOVER_TOKEN`, `PUSHOVER_USER` and `AI_API_KEY` in
   `log_agent.conf`, and adjust the log paths/patterns to match your system.
4. Protect the config file — it holds your API keys:

   ```sh
   chown root:root log_agent.conf
   chmod 600 log_agent.conf
   ```

5. Run it once to verify, then add a cron job, e.g. every 10 minutes:

   ```sh
   */10 * * * * /opt/log-agent/log_agent.py
   ```

## Configuration reference

All settings live in a single `log_agent.conf` (one `KEY=value` per line;
valid JSON values are parsed automatically).

| Key | Description |
|---|---|
| `DEBUG_MODE` | `true` = log every event, `false` = only events/warnings/errors |
| `PUSHOVER_TOKEN` | Pushover application token |
| `PUSHOVER_USER` | Pushover user key |
| `PUSHOVER_URL` | Pushover API endpoint (default is fine) |
| `AI_PROVIDER` | `mammouth`, `openai`, `anthropic` or `openai-compatible` |
| `AI_MODEL` | Model ID, e.g. `gpt-4.1`, `claude-sonnet-4-20250514`, `deepseek-chat` |
| `AI_API_KEY` | API key for the provider |
| `AI_BASE_URL` | Optional base URL override (see defaults below) |
| `AI_MAX_TOKENS` | Maximum number of tokens in the AI answer |
| `LOG_SOURCES` | JSON map: log path -> `patterns` / `exclude_patterns` |
| `REPORT_RETENTION_DAYS` | How many days a saved report is kept |
| `MAX_SAVED_REPORTS` | Maximum number of saved reports (oldest pruned first) |
| `LOG_FILE` | Diagnostic log written by the script itself |
| `SYSTEM_PROMPT` | Instructions sent to the AI |

### AI providers

Default endpoints used when `AI_BASE_URL` is empty:

- `mammouth` -> `https://api.mammouth.ai/v1`
- `openai` -> `https://api.openai.com/v1`
- `anthropic` -> `https://api.anthropic.com/v1`
- `openai-compatible` -> no default; `AI_BASE_URL` is required (e.g.
  `http://localhost:11434/v1` for Ollama). Note that local endpoints also
  need a non-empty `AI_API_KEY` — a placeholder such as `ollama` works.

The legacy keys `MAMMOUTH_API_KEY`, `MAMMOUTH_URL` and `MODEL_NAME` still
work as fallbacks (provider `mammouth`) when the matching `AI_*` key is
absent.

### Exclude patterns

Lines matching `exclude_patterns.conf` never trigger an analysis or alert.
Format: one regex per line (`#` = comment). Prefix a pattern with
`source:` to limit it to one log file, or `*:` for all sources.

## How it works

1. Each run reads the configured log files from the saved byte position
   (`log_agent.pos`) and collects the lines that match `LOG_SOURCES`.
2. The matching lines are sent to the AI, which summarises the events.
3. A JSON report is saved under `reports/` and a Pushover notification is
   sent (error alert with the summary, or an "All OK" notification).
4. Position state is saved atomically, so restarts are safe.

## Security

- `log_agent.conf` contains API keys — keep it owned by root with mode `600`.
- Reports may contain sensitive log excerpts; the script creates the
  `reports/` directory with mode `700`.

## Tests

`test_log_agent.py` is a regression test suite for `log_agent.py` (19 tests).
It verifies the script's core behaviour without touching real services: all
AI and Pushover calls are mocked, so no API keys, network traffic or
notifications are needed to run it.

```sh
python3 test_log_agent.py
```

Coverage includes:

- **Config parsing** — `KEY=value` syntax with comments, quotes, numbers and
  booleans, multi-line JSON values (`LOG_SOURCES`) and escaped newlines.
- **Exclude patterns** — global vs `source:`-scoped rules in
  `exclude_patterns.conf`, missing-file handling and end-to-end filtering.
- **Report storage** — sortable/file-safe report IDs, saved reports with the
  full message history and correct permissions (`700` directory, `600` file).
- **AI providers** — key precedence (`AI_*` over legacy `MAMMOUTH_*`),
  built-in base-URL defaults, OpenAI-compatible and Anthropic request
  building, provider dispatch and error handling.
