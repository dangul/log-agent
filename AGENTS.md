# AGENTS.md

## Project snapshot

This repository contains a Python log-monitoring utility, `log_agent.py`, which watches Linux logs for suspicious activity, filters noisy lines, sends an AI-generated summary via a configured provider, and persists a timestamped JSON report. The canonical project documentation is [README.md](README.md).

## Key files and responsibilities

- [log_agent.py](log_agent.py): main runtime. It loads config from `log_agent.conf`, tracks per-file read positions in `log_agent.pos`, and writes alert reports under [reports/](reports/).
- [test_log_agent.py](test_log_agent.py): regression tests for parsing, filtering, persistence, AI provider dispatch, and Pushover behavior. Treat this as the source of truth for expected behavior.
- [log_agent.conf.example](log_agent.conf.example): example runtime config. Copy it to `log_agent.conf` for local use.
- [exclude_patterns.conf.example](exclude_patterns.conf.example): example ignore-pattern rules.
- [reports/](reports/): generated JSON reports. Keep them out of feature work unless a test specifically exercises them.

## Working conventions

- Keep changes minimal and Pythonic. This project is intentionally compact and stateful; prefer surgical edits over broad refactors.
- Preserve the behavior of saved offsets and inode tracking. New runs must only process newly appended log lines; `log_agent.pos` must remain consistent after restarts.
- Keep secrets in `log_agent.conf` and do not hardcode API keys or tokens. The project expects the config file to be protected (root-owned, mode 600 in production).
- Always create a pull request before merging any change into `main`. Do not commit directly to `main`; use a feature branch and merge only after the PR is open and the relevant checks pass.
- When changing matching, filtering, parsing, or report-generation logic, update or add the corresponding regression tests in [test_log_agent.py](test_log_agent.py).
- Prefer the repo’s existing patterns over introducing new dependencies or external state.
- For release work, keep the release summary short and in English. A short description with a few bullets is preferred over a long narrative.

## Validation commands

Run the project’s regression suite before claiming a fix or behavior change:

```sh
python3 test_log_agent.py
```

For a manual local check without sending a Pushover notification:

```sh
python3 log_agent.py --cli
```

## Important project notes

- The project is configured around `LOG_SOURCES`, `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `PUSHOVER_*`, and `REPORT_RETENTION_DAYS` values in `log_agent.conf`.
- The repository does not rely on a web app or TypeScript stack; do not apply frontend conventions or JavaScript-only assumptions here.
- Use [README.md](README.md) for deployment and configuration details, and use [test_log_agent.py](test_log_agent.py) for behavioral expectations.
