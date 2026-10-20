# Project rules for this repository

## Stack and scope

- This project is a Python utility, not a TypeScript or frontend app.
- Keep changes focused on the Linux log-monitoring workflow in `log_agent.py` and the corresponding regression tests in `test_log_agent.py`.
- Prefer small, surgical edits over broad refactors.

## Documentation and behavior

- Use the canonical project docs in [README.md](README.md) for deployment and configuration details.
- Preserve the stateful log-tracking behavior: each run must read only new log data and keep `log_agent.pos` consistent across restarts.
- Keep secrets out of source control; configuration values belong in `log_agent.conf` and should remain protected in production.
- When changing matching, parsing, AI-provider handling, or report behavior, update the relevant regression tests in [test_log_agent.py](test_log_agent.py).

## Validation

- Run the project regression suite before claiming a fix or behavior change:

```sh
python3 test_log_agent.py
```

- For a manual local check without sending a Pushover notification:

```sh
python3 log_agent.py --cli
```

## Generic conventions

- Do not add new dependencies unless the task explicitly requires them.
- Do not apply frontend conventions or JavaScript assumptions to this repository.
- Keep comments limited to complex or non-obvious logic; prefer clear names and small functions.