#!/usr/bin/env python3
import sys
import os
import socket
import json
import re
import secrets
import tempfile
from datetime import datetime, timezone
import requests
import logging

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# Everything the script uses lives in this directory: the script itself, the
# config file, the exclude-patterns file, the position state and the reports.
# The log file is kept in /var/log (see LOG_FILE below).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The single configuration file. It holds ALL variables, including the API
# keys, so it must stay chmod 600 and owned by root. It is read once at
# import time; see load_config() for the file format.
CONFIG_FILE = os.path.join(BASE_DIR, "log_agent.conf")

# A plain-text file with ignore patterns. Matching lines never trigger a
# reaction (no AI analysis, no Pushover alert). See
# load_file_exclude_patterns() for the file format. The file lives next to
# this script and is re-read on every run, so you can silence noisy logs by
# editing it at any time.
EXCLUDE_FILE = os.path.join(BASE_DIR, "exclude_patterns.conf")

# JSON state with a separate byte position and inode for every log file.
POSITION_FILE = os.path.join(BASE_DIR, "log_agent.pos")
REPORT_DIRECTORY = os.path.join(BASE_DIR, "reports")

# Built-in default API base URLs per AI provider. "openai-compatible" has no
# default on purpose: it is a local/custom endpoint and must always be given
# explicitly with AI_BASE_URL (checked at start).
AI_PROVIDER_DEFAULTS = {
    "mammouth": "https://api.mammouth.ai/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openai-compatible": "",
}

# Diagnostic log written by this script itself.
SERVER_NAME = socket.gethostname()


def _coerce_config_value(value):
    """Return a config value as its native type when it is valid JSON.

    Numbers and true/false become int/bool, "...", {...} and [...] become
    their JSON counterparts (multi-line strings must use \\n escapes), and
    any other value is kept as plain text with optional surrounding quotes
    removed.
    """
    value = value.strip()
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value.strip('"').strip("'")


def load_config(config_file=CONFIG_FILE):
    """Load the whole configuration from a plain-text "key=value" file.

    One entry per line; lines starting with "#" are comments and blank lines
    are skipped. Optional surrounding quotes are stripped. A value that starts
    with "{" or "[" may span several lines - parsing continues until the JSON
    is balanced, which is how structured values such as LOG_SOURCES are stored.

    The file may not exist or be unreadable; an empty dict is returned and the
    script falls back on its built-in defaults.
    """
    loaded = {}
    if not os.path.exists(config_file):
        logging.warning(f"Config file not found: {config_file}")
        return loaded
    try:
        with open(config_file, "r", encoding="utf-8") as config_f:
            lines = config_f.readlines()
    except OSError as e:
        logging.warning(f"Could not read config file {config_file}: {str(e)}")
        return loaded

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or not key:
            continue

        # A value starting with "{" or "[" continues on the following lines
        # until the JSON object/array is balanced.
        if value.startswith(("{", "[")):
            depth = 0
            in_string = False
            escaped = False
            block = [value]
            while True:
                for ch in block[-1]:
                    if in_string:
                        if escaped:
                            escaped = False
                        elif ch == "\\":
                            escaped = True
                        elif ch == '"':
                            in_string = False
                    elif ch == '"':
                        in_string = True
                    elif ch in "{[":  # '{' and '[' are both counted in depth.
                        depth += 1
                    elif ch in "}]":
                        depth -= 1
                if depth <= 0 or index >= len(lines):
                    break
                next_line = lines[index].strip()
                index += 1
                block.append(next_line)
            value = " ".join(block)

        if not value:
            continue
        loaded[key] = _coerce_config_value(value)

    return loaded


def _resolve_ai_settings(config):
    """Map the AI_* config keys to a (provider, model, api_key, base_url,
    max_tokens) tuple, honouring the legacy MAMMOUTH_* keys as fallbacks.

    The provider name is lower-cased. A missing api_key stays empty and is
    reported at start; a legacy MAMMOUTH_URL ending in "/chat/completions"
    is reduced to its base URL. An empty base_url is replaced with the
    provider's built-in default (see AI_PROVIDER_DEFAULTS).
    """
    provider = str(config.get("AI_PROVIDER", "mammouth")).strip().lower()
    model = config.get("AI_MODEL", "") or config.get("MODEL_NAME", "gpt-4.1")
    api_key = config.get("AI_API_KEY", "") or config.get("MAMMOUTH_API_KEY", "")

    legacy_url = config.get("MAMMOUTH_URL", "") or ""
    if legacy_url.endswith("/chat/completions"):
        legacy_url = legacy_url[: -len("/chat/completions")]
    base_url = str(config.get("AI_BASE_URL", "") or legacy_url).strip()
    if not base_url:
        base_url = AI_PROVIDER_DEFAULTS.get(provider, "")

    try:
        max_tokens = int(config.get("AI_MAX_TOKENS", 300))
    except (TypeError, ValueError):
        max_tokens = 300

    return provider, model, api_key, base_url, max_tokens


# Read the whole configuration at import time. Every variable has a safe
# built-in default, so a broken or partial config file can never leave the
# script half-initialized - secrets simply stay empty and the script reports
# them as missing on start.
_config = load_config()

DEBUG_MODE = _config.get("DEBUG_MODE", True)

# 1. Pushover API Configuration
PUSHOVER_TOKEN = _config.get("PUSHOVER_TOKEN", "")
PUSHOVER_USER = _config.get("PUSHOVER_USER", "")
PUSHOVER_URL = _config.get("PUSHOVER_URL", "https://api.pushover.net/1/messages.json")

# 2. AI Runtime Configuration
# AI_PROVIDER selects the AI used for the log analysis: "mammouth" (default),
# "openai" (ChatGPT), "anthropic" (Claude) or "openai-compatible" (OpenRouter,
# DeepSeek, Groq, Mistral, Ollama, LM Studio, ...; requires AI_BASE_URL).
# The legacy MAMMOUTH_API_KEY / MAMMOUTH_URL / MODEL_NAME keys still work as
# fallbacks and map to provider "mammouth"; see _resolve_ai_settings().
AI_PROVIDER, AI_MODEL, AI_API_KEY, AI_BASE_URL, AI_MAX_TOKENS = (
    _resolve_ai_settings(_config)
)
# Backwards-compatible alias used by older report files and callers.
MODEL_NAME = AI_MODEL

# Log sources and source-specific, case-insensitive filters. Kernel messages are
# already included in syslog, so kern.log is intentionally not read separately.
LOG_SOURCES = _config.get("LOG_SOURCES", {
    "/var/log/syslog": {
        "patterns": [
            r"\berror\b", r"\bcritical\b", r"\bsevere\b", r"\bpanic\b",
            r"\bunauthorized\b", r"\bfailed\b", r"\bfailure\b",
            r"segfault", r"out of memory", r"oom-killer", r"i/o error",
            r"read-only file system",
        ],
        "exclude_patterns": [r"networkd-dispatcher"],
    },
    "/var/log/auth.log": {
        "patterns": [
            r"failed password", r"invalid user", r"authentication failure",
            r"pam_unix\([^)]*:auth\):", r"maximum authentication attempts",
            r"connection closed by authenticating user", r"accepted password",
            r"accepted publickey", r"sudo:.*authentication failure",
            r"sudo:.*incorrect password", r"useradd(?:\[|:)",
            r"userdel(?:\[|:)", r"usermod(?:\[|:)", r"groupadd(?:\[|:)",
            r"groupdel(?:\[|:)", r"passwd(?:\[|:).*password changed",
        ],
        "exclude_patterns": [],
    },
})

# Source file names, used to scope patterns in the exclude-patterns file.
KNOWN_LOG_SOURCES = {os.path.basename(path) for path in LOG_SOURCES}

# Reports are kept for REPORT_RETENTION_DAYS days and capped at
# MAX_SAVED_REPORTS files (oldest are pruned first).
REPORT_RETENTION_DAYS = _config.get("REPORT_RETENTION_DAYS", 90)
MAX_SAVED_REPORTS = _config.get("MAX_SAVED_REPORTS", 200)

# Diagnostic log written by this script itself.
LOG_FILE = _config.get("LOG_FILE", "/var/log/log_agent.log")

SYSTEM_PROMPT = _config.get(
    "SYSTEM_PROMPT",
    "You are a Linux and security expert. Here are relevant lines from the "
    "server's system and authentication logs. Briefly summarise in English "
    "what has happened, what the main problems are, and whether anything "
    "requires immediate action. Keep the answer short and concise.",
)

# ==============================================================================
# LOGGING SETUP
# ==============================================================================
log_level = logging.DEBUG if DEBUG_MODE else logging.INFO
logging.basicConfig(
    filename=LOG_FILE,
    level=log_level,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def load_file_exclude_patterns(exclude_file=EXCLUDE_FILE):
    """Load ignore patterns from a plain-text file.

    Format: one case-insensitive regular expression per line. Lines starting
    with "#" are comments and blank lines are skipped. A pattern applies to
    every log source unless it is prefixed with "source:" where source is the
    log file name (syslog, auth.log, ...) or "*" for every source. The prefix
    is only treated as a scope when it matches a known source name, so regexes
    containing ":" still work as global patterns.

    The file may not exist; an empty dict is returned and the script simply
    falls back on the per-source exclude_patterns defined in LOG_SOURCES. The
    file is re-read on every run so silence can be added without restarting.
    """
    patterns = {}
    if not exclude_file or not os.path.exists(exclude_file):
        return patterns
    try:
        with open(exclude_file, "r", encoding="utf-8") as exclude_f:
            for raw_line in exclude_f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                scope, separator, pattern = line.partition(":")
                scope = scope.strip()
                pattern = pattern.strip()
                if separator and pattern and (scope == "*" or scope in KNOWN_LOG_SOURCES):
                    patterns.setdefault(scope, []).append(pattern)
                else:
                    patterns.setdefault("*", []).append(line)
    except OSError as e:
        logging.warning(f"Could not read exclude-patterns file {exclude_file}: {str(e)}")
    return patterns


def line_matches(line, source_config, source_name="", file_excludes=None):
    """Return True when a line should trigger a reaction.

    Exclusions are checked first: patterns from the exclude-patterns file
    (global scope and patterns scoped to the current source name), then the
    per-source exclude_patterns defined in LOG_SOURCES. Any exclusion wins
    over the positive patterns, so ignored lines never reach Mammouth or
    Pushover.
    """
    flags = re.IGNORECASE
    if file_excludes:
        for scope in ("*", source_name):
            for pattern in file_excludes.get(scope, ()):
                if re.search(pattern, line, flags):
                    return False
    if any(re.search(pattern, line, flags) for pattern in source_config["exclude_patterns"]):
        return False
    return any(re.search(pattern, line, flags) for pattern in source_config["patterns"])


def load_positions(position_file, log_sources):
    """Load per-file positions, including the legacy single-integer format."""
    if not os.path.exists(position_file):
        return {}

    try:
        with open(position_file, "r", encoding="utf-8") as pos_f:
            raw_state = pos_f.read().strip()

        try:
            state = json.loads(raw_state)
            files = state.get("files", {})
            if not isinstance(files, dict):
                raise ValueError("'files' must be an object")
            return files
        except (json.JSONDecodeError, AttributeError):
            # Old versions stored only the byte position for syslog.
            legacy_position = int(raw_state)
            syslog_path = "/var/log/syslog"
            if syslog_path in log_sources and os.path.exists(syslog_path):
                logging.info("Migrating the legacy syslog position to multi-file state.")
                return {
                    syslog_path: {
                        "position": legacy_position,
                        "inode": os.stat(syslog_path).st_ino,
                    }
                }
            return {}
    except Exception as e:
        logging.warning(f"Could not read position file; new sources start at EOF: {str(e)}")
        return {}


def save_positions(position_file, positions):
    """Atomically save positions so an interrupted write cannot corrupt state."""
    temporary_file = f"{position_file}.tmp"
    state = {"version": 1, "files": positions}
    with open(temporary_file, "w", encoding="utf-8") as pos_f:
        json.dump(state, pos_f, indent=2, sort_keys=True)
        pos_f.write("\n")
    os.replace(temporary_file, position_file)


def read_matching_lines(path, start_position, source_config, source_name, file_excludes=None):
    """Read matching lines from a byte offset and return matches and new offset."""
    matches = []
    with open(path, "rb") as log_f:
        log_f.seek(start_position)
        for raw_line in log_f:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if line and line_matches(line, source_config, source_name, file_excludes):
                matches.append(f"[{source_name}] {line}")
        return matches, log_f.tell()


def collect_new_matches(log_sources=LOG_SOURCES, position_file=POSITION_FILE,
                        exclude_file=EXCLUDE_FILE):
    """Collect new matching lines and maintain an independent cursor per file."""
    positions = load_positions(position_file, log_sources)
    file_excludes = load_file_exclude_patterns(exclude_file)
    matched_lines = []
    available_sources = 0

    for path, source_config in log_sources.items():
        source_name = os.path.basename(path)
        if not os.path.exists(path):
            logging.warning(f"Log source does not exist and is skipped: {path}")
            continue

        available_sources += 1
        current_stat = os.stat(path)
        previous = positions.get(path)

        # A newly configured source starts at EOF to avoid alerting on its full
        # history. Every later run starts at the position saved here.
        if previous is None:
            positions[path] = {
                "position": current_stat.st_size,
                "inode": current_stat.st_ino,
            }
            logging.info(f"Initialized new log source at EOF: {path}")
            continue

        try:
            previous_position = int(previous.get("position", 0))
            previous_inode = int(previous.get("inode", 0))
            start_position = previous_position

            if previous_inode != current_stat.st_ino:
                # logrotate normally keeps the previous uncompressed file as .1.
                # Read its remaining bytes before starting the new active file.
                rotated_path = f"{path}.1"
                if os.path.exists(rotated_path) and os.stat(rotated_path).st_ino == previous_inode:
                    rotated_matches, _ = read_matching_lines(
                        rotated_path, previous_position, source_config, source_name, file_excludes
                    )
                    matched_lines.extend(rotated_matches)
                    logging.info(f"Read remaining lines from rotated log: {rotated_path}")
                else:
                    logging.warning(f"Log rotation detected but old inode was unavailable: {path}")
                start_position = 0
            elif current_stat.st_size < previous_position:
                logging.info(f"Truncation detected; resetting position to 0: {path}")
                start_position = 0

            source_matches, new_position = read_matching_lines(
                path, start_position, source_config, source_name, file_excludes
            )
            matched_lines.extend(source_matches)
            positions[path] = {
                "position": new_position,
                "inode": current_stat.st_ino,
            }
            logging.debug(f"Saved position for {path}: {new_position} bytes")
        except Exception as e:
            logging.error(f"Failed to process {path}: {str(e)}")

    if available_sources == 0:
        raise RuntimeError("None of the configured log sources are available")

    save_positions(position_file, positions)
    return matched_lines


def build_analysis_messages(log_batch):
    """Build the initial conversation so it can be persisted and resumed later."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Here are the log lines:\n\n{log_batch}"},
    ]


def _anthropic_payload(messages):
    """Convert OpenAI-style messages into an Anthropic Messages API payload.

    Anthropic expects a flat "system" string (not a system message) and a
    "messages" list without system entries; this function performs that
    split.
    """
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    body = [m for m in messages if m.get("role") != "system"]
    payload = {
        "model": AI_MODEL,
        "max_tokens": AI_MAX_TOKENS,
        "messages": body,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


def _call_openai_compatible(messages):
    """Chat-completions call used by "mammouth", "openai" and "openai-compatible"."""
    endpoint = f"{AI_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": AI_MAX_TOKENS
    }

    logging.debug(f"Sending payload to {AI_PROVIDER} AI at {endpoint}: {payload}")

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        logging.debug(f"{AI_PROVIDER} AI raw response status: {response.status_code}")

        if response.status_code == 200:
            ai_text = response.json()["choices"][0]["message"]["content"].strip()
            logging.debug(f"{AI_PROVIDER} AI parsed text: {ai_text}")
            return ai_text
        else:
            logging.error(f"Error from {AI_PROVIDER} API (Status {response.status_code}): {response.text}")
            return None
    except Exception as e:
        logging.error(f"AI analysis failed due to network error: {str(e)}")
        return None


def _call_anthropic(messages):
    """Anthropic Messages API call used for Claude."""
    endpoint = f"{AI_BASE_URL.rstrip('/')}/messages"
    headers = {
        "x-api-key": AI_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    payload = _anthropic_payload(messages)

    logging.debug(f"Sending payload to anthropic AI at {endpoint}: {payload}")

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        logging.debug(f"anthropic AI raw response status: {response.status_code}")

        if response.status_code == 200:
            body = response.json()
            ai_text = "".join(
                block.get("text", "") for block in body.get("content", [])
            ).strip()
            logging.debug(f"anthropic AI parsed text: {ai_text}")
            return ai_text
        else:
            logging.error(f"Error from anthropic API (Status {response.status_code}): {response.text}")
            return None
    except Exception as e:
        logging.error(f"AI analysis failed due to network error: {str(e)}")
        return None


def analyze_with_ai(messages):
    """Ask the configured AI provider to summarise the matched log lines."""
    if AI_PROVIDER == "anthropic":
        return _call_anthropic(messages)
    return _call_openai_compatible(messages)


def generate_report_id(now=None):
    """Return a sortable, human-readable ID with collision protection."""
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3).upper()}"


def prune_reports(report_directory=REPORT_DIRECTORY, now=None):
    """Remove expired reports and cap the total number of saved reports."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - (REPORT_RETENTION_DAYS * 86400)
    report_files = []

    for entry in os.scandir(report_directory):
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        try:
            stat = entry.stat()
            if stat.st_mtime < cutoff:
                os.remove(entry.path)
            else:
                report_files.append((stat.st_mtime, entry.path))
        except OSError as e:
            logging.warning(f"Could not inspect or prune report {entry.path}: {str(e)}")

    report_files.sort(reverse=True)
    for _, path in report_files[MAX_SAVED_REPORTS:]:
        try:
            os.remove(path)
        except OSError as e:
            logging.warning(f"Could not prune excess report {path}: {str(e)}")


def save_report(raw_log_lines, messages, ai_summary, matched_line_count,
                report_directory=REPORT_DIRECTORY, report_id=None, now=None):
    """Atomically persist an AI report and its resumable conversation history."""
    now = now or datetime.now(timezone.utc)
    report_id = report_id or generate_report_id(now)
    os.makedirs(report_directory, mode=0o700, exist_ok=True)
    os.chmod(report_directory, 0o700)

    conversation = [dict(message) for message in messages]
    conversation.append({"role": "assistant", "content": ai_summary})
    report = {
        "schema_version": 1,
        "report_id": report_id,
        "server": SERVER_NAME,
        "model": AI_MODEL,
        "status": "open",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "matched_line_count": matched_line_count,
        "saved_log_line_count": len(raw_log_lines),
        "raw_log_lines": list(raw_log_lines),
        "messages": conversation,
    }
    final_path = os.path.join(report_directory, f"{report_id}.json")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=report_directory,
            prefix=f".{report_id}.", suffix=".tmp", delete=False
        ) as report_file:
            temporary_path = report_file.name
            os.chmod(temporary_path, 0o600)
            json.dump(report, report_file, ensure_ascii=False, indent=2)
            report_file.write("\n")
            report_file.flush()
            os.fsync(report_file.fileno())
        os.replace(temporary_path, final_path)
        temporary_path = None
        prune_reports(report_directory, now)
        return report_id, final_path
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)


def send_pushover(title, message, priority=0):
    token = PUSHOVER_TOKEN.strip()
    user = PUSHOVER_USER.strip()
    notification_title = f"{title} [{SERVER_NAME}]"
    
    payload = {
        "token": token,
        "user": user,
        "title": notification_title,
        "message": str(message),
        "priority": priority
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux server agent)",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    logging.debug(f"Preparing Pushover API request to {PUSHOVER_URL}")
    
    try:
        res = requests.post(PUSHOVER_URL, data=payload, headers=headers, allow_redirects=False, timeout=10)
        logging.debug(f"Pushover raw response status: {res.status_code} - Response text: {res.text}")
        if res.status_code != 200:
            logging.error(f"Pushover notification failed (Status {res.status_code}): {res.text}")
    except Exception as e:
        logging.error(f"Could not send to Pushover: {str(e)}")

if __name__ == "__main__":
    if not (PUSHOVER_TOKEN and PUSHOVER_USER and AI_API_KEY):
        logging.error(
            f"API keys missing. Fill in {CONFIG_FILE} with "
            "PUSHOVER_TOKEN, PUSHOVER_USER and AI_API_KEY."
        )
        sys.exit(1)
    if AI_PROVIDER == "openai-compatible" and not AI_BASE_URL:
        logging.error(
            f"AI_BASE_URL must be set in {CONFIG_FILE} when "
            "AI_PROVIDER=openai-compatible."
        )
        sys.exit(1)
    logging.info("Cron job started: Parsing new lines in configured log sources...")
    try:
        matched_lines = collect_new_matches()
    except Exception as e:
        logging.error(f"Failed to process configured log sources: {str(e)}")
        sys.exit(1)

    # Decision logic for notifications
    if matched_lines:
        analysis_lines = matched_lines[-50:]  # Limit AI context, but save all matches.
        log_batch = "\n".join(analysis_lines)
        logging.info(f"Found {len(matched_lines)} new matching log lines. Sending to AI...")

        messages = build_analysis_messages(log_batch)
        ai_summary = analyze_with_ai(messages)
        if ai_summary:
            try:
                report_id, report_path = save_report(
                    matched_lines, messages, ai_summary, len(matched_lines)
                )
            except Exception as e:
                logging.error(f"Could not save AI report; notification is still sent: {str(e)}")
                report_id = "COULD-NOT-SAVE"
                report_path = None

            logging.info(
                f"AI analysis completed. Report {report_id} saved at {report_path}. "
                "Sending alert to Pushover."
            )
            report_msg = (
                f"Report ID: {report_id}\n\n"
                f"Errors detected since the last run:\n\nAI Analysis:\n{ai_summary}"
            )
            send_pushover("⚠️ Server Report: Errors Found", report_msg, priority=1)
    else:
        logging.info("No critical log lines found since last run. Sending OK notification.")
        send_pushover("✅ Server Report: All OK", "No critical errors or security anomalies have been detected in the monitored logs since the last run.", priority=0)

