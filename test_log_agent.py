#!/usr/bin/env python3
import importlib.util
import io
import json
import os
import re
import stat
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone


SPEC = importlib.util.spec_from_file_location(
    "log_agent",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_agent.py"),
)
log_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(log_agent)


class ReportStorageTests(unittest.TestCase):
    def test_report_id_is_sortable_and_safe_for_file_names(self):
        now = datetime(2026, 10, 6, 20, 30, 40, tzinfo=timezone.utc)
        report_id = log_agent.generate_report_id(now)

        self.assertRegex(report_id, r"^20261006T203040Z-[0-9A-F]{6}$")

    def test_save_report_persists_logs_and_complete_initial_history(self):
        now = datetime(2026, 10, 6, 20, 30, 40, tzinfo=timezone.utc)
        messages = log_agent.build_analysis_messages("[syslog] ERROR disk")

        with tempfile.TemporaryDirectory() as parent:
            report_directory = os.path.join(parent, "reports")
            report_id, report_path = log_agent.save_report(
                raw_log_lines=["[syslog] ERROR disk", "[auth.log] Failed password"],
                messages=messages,
                ai_summary="The disk needs to be checked.",
                matched_line_count=2,
                report_directory=report_directory,
                report_id="20261006T203040Z-ABC123",
                now=now,
            )

            self.assertEqual(report_id, "20261006T203040Z-ABC123")
            self.assertEqual(stat.S_IMODE(os.stat(report_directory).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(report_path).st_mode), 0o600)

            with open(report_path, encoding="utf-8") as report_file:
                report = json.load(report_file)

            self.assertEqual(report["report_id"], report_id)
            self.assertEqual(report["status"], "open")
            self.assertEqual(report["matched_line_count"], 2)
            self.assertEqual(report["saved_log_line_count"], 2)
            self.assertEqual(len(report["messages"]), 3)
            self.assertEqual(report["messages"][-1], {
                "role": "assistant",
                "content": "The disk needs to be checked.",
            })
            self.assertIn("ERROR disk", report["messages"][1]["content"])
            self.assertEqual(report["raw_log_lines"][1], "[auth.log] Failed password")


class ExcludePatternFileTests(unittest.TestCase):
    """Tests for filtering away logs before they reach the AI/Pushover."""

    def setUp(self):
        self.source_config = {
            "patterns": [r"\berror\b"],
            "exclude_patterns": [],
        }

    def test_global_file_exclude_silences_any_source(self):
        file_excludes = {"*": [r"known-noise-transmitter"]}
        self.assertFalse(log_agent.line_matches(
            "Oct  6 20:00:00 host known-noise-transmitter ERROR disk",
            self.source_config, "syslog", file_excludes,
        ))
        self.assertTrue(log_agent.line_matches(
            "Oct  6 20:00:00 host something-else ERROR disk",
            self.source_config, "syslog", file_excludes,
        ))

    def test_source_scoped_file_exclude_only_silences_that_source(self):
        file_excludes = {"auth.log": [r"cron"]}
        self.assertFalse(log_agent.line_matches(
            "Oct  6 20:00:00 host cron ERROR",
            self.source_config, "auth.log", file_excludes,
        ))
        self.assertTrue(log_agent.line_matches(
            "Oct  6 20:00:00 host cron ERROR",
            self.source_config, "syslog", file_excludes,
        ))

    def test_built_in_exclude_patterns_still_work_alongside_file_excludes(self):
        source_config = {
            "patterns": [r"\berror\b"],
            "exclude_patterns": [r"networkd-dispatcher"],
        }
        self.assertFalse(log_agent.line_matches(
            "Oct  6 20:00:00 host networkd-dispatcher ERROR",
            source_config, "syslog", {"*": [r"nothing"]},
        ))

    def test_load_ignore_file_parses_comments_scopes_and_global_patterns(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                "# comment\n"
                "\n"
                "known-noise\n"
                "syslog:^kernel:.*nfc\n"
                "auth.log:^sudo:.*incorrect password\n"
                "timeout: something\n"
            )
            exclude_file = f.name
        try:
            patterns = log_agent.load_file_exclude_patterns(exclude_file)
        finally:
            os.unlink(exclude_file)

        self.assertEqual(patterns["*"], ["known-noise", "timeout: something"])
        self.assertEqual(patterns["syslog"], [r"^kernel:.*nfc"])
        self.assertEqual(patterns["auth.log"], [r"^sudo:.*incorrect password"])

    def test_missing_ignore_file_is_treated_as_empty(self):
        self.assertEqual(
            log_agent.load_file_exclude_patterns("/nonexistent/excludes.conf"), {}
        )

    def test_collect_new_matches_honors_file_excludes_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "syslog")
            log_sources = {log_path: {"patterns": [r"\berror\b"], "exclude_patterns": []}}
            # Create the (initially empty) log file before the first run.
            with open(log_path, "w", encoding="utf-8"):
                pass
            position_file = os.path.join(tmp, "log_agent.pos")
            exclude_file = os.path.join(tmp, "exclude_patterns.conf")
            with open(exclude_file, "w", encoding="utf-8") as exclude_f:
                exclude_f.write("known-noise\n")

            # First run initializes a new source at EOF without alerting on
            # its history, exactly like the production script does.
            self.assertEqual(log_agent.collect_new_matches(
                log_sources=log_sources,
                position_file=position_file,
                exclude_file=exclude_file,
            ), [])

            with open(log_path, "a", encoding="utf-8") as log_f:
                log_f.write("Oct  6 20:00:00 host ERROR disk full\n")
                log_f.write("Oct  6 20:00:01 host ERROR known-noise-thing\n")

            matches = log_agent.collect_new_matches(
                log_sources=log_sources,
                position_file=position_file,
                exclude_file=exclude_file,
            )
            self.assertEqual(len(matches), 1)
            self.assertIn("disk full", matches[0])
            self.assertNotIn("known-noise", matches[0])

            # Nothing new on the next run; the cursor still advances correctly.
            matches_again = log_agent.collect_new_matches(
                log_sources=log_sources,
                position_file=position_file,
                exclude_file=exclude_file,
            )
            self.assertEqual(matches_again, [])


class ConfigFileTests(unittest.TestCase):
    """Tests for the key=value config parser used for log_agent.conf."""

    def test_config_parses_comments_quotes_numbers_and_booleans(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                "# comment\n"
                "\n"
                "DEBUG_MODE=false\n"
                'MODEL_NAME="gpt-4.1"\n'
                "PUSHOVER_TOKEN='0123456789abcdef0123456789abcdef'\n"
                "REPORT_RETENTION_DAYS=90\n"
                "EMPTY_VALUE=\n"
            )
            config_file = f.name
        try:
            config = log_agent.load_config(config_file)
        finally:
            os.unlink(config_file)

        self.assertIs(config["DEBUG_MODE"], False)
        self.assertEqual(config["MODEL_NAME"], "gpt-4.1")
        self.assertEqual(config["PUSHOVER_TOKEN"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(config["REPORT_RETENTION_DAYS"], 90)
        self.assertNotIn("EMPTY_VALUE", config)

    def test_config_parses_multiline_json_object(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                "LOG_SOURCES={\n"
                '  "/var/log/syslog": {\n'
                '    "patterns": ["\\\\berror\\\\b"],\n'
                '    "exclude_patterns": ["networkd-dispatcher"]\n'
                "  }\n"
                "}\n"
            )
            config_file = f.name
        try:
            config = log_agent.load_config(config_file)
        finally:
            os.unlink(config_file)

        sources = config["LOG_SOURCES"]
        self.assertEqual(sources["/var/log/syslog"]["patterns"], [r"\berror\b"])
        self.assertEqual(
            sources["/var/log/syslog"]["exclude_patterns"],
            ["networkd-dispatcher"],
        )

    def test_config_json_string_decodes_escaped_newlines(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write('SYSTEM_PROMPT="line one\\nline two"\n')
            config_file = f.name
        try:
            config = log_agent.load_config(config_file)
        finally:
            os.unlink(config_file)

        self.assertEqual(config["SYSTEM_PROMPT"], "line one\nline two")

    def test_missing_config_file_is_treated_as_empty(self):
        self.assertEqual(
            log_agent.load_config("/nonexistent/log_agent.conf"), {}
        )


class _FakeResponse:
    """Minimal stand-in for a requests.Response in AI provider tests."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.json_body = body
        self.text = json.dumps(body)

    def json(self):
        return self.json_body


class AIProviderTests(unittest.TestCase):
    """Tests for the pluggable AI provider (AI_* keys with MAMMOUTH_* fallback)."""

    def setUp(self):
        self.original = {
            name: getattr(log_agent, name)
            for name in (
                "AI_PROVIDER", "AI_MODEL", "AI_API_KEY",
                "AI_BASE_URL", "AI_MAX_TOKENS",
            )
        }

    def tearDown(self):
        for name, value in self.original.items():
            setattr(log_agent, name, value)

    def test_resolve_ai_settings_prefers_new_keys_over_legacy(self):
        config = {
            "AI_PROVIDER": "openai",
            "AI_MODEL": "gpt-4o-mini",
            "AI_API_KEY": "sk-ny",
            "AI_BASE_URL": "https://api.openai.com/v1",
            "AI_MAX_TOKENS": "400",
            "MAMMOUTH_API_KEY": "sk-gammal",
            "MODEL_NAME": "gpt-4.1",
        }
        provider, model, api_key, base_url, max_tokens = log_agent._resolve_ai_settings(config)
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o-mini")
        self.assertEqual(api_key, "sk-ny")
        self.assertEqual(base_url, "https://api.openai.com/v1")
        self.assertEqual(max_tokens, 400)

    def test_resolve_ai_settings_falls_back_to_legacy_mammouth_keys(self):
        config = {
            "MAMMOUTH_API_KEY": "sk-gammal",
            "MAMMOUTH_URL": "https://api.mammouth.ai/v1/chat/completions",
            "MODEL_NAME": "gpt-4.1",
        }
        provider, model, api_key, base_url, max_tokens = log_agent._resolve_ai_settings(config)
        self.assertEqual(provider, "mammouth")
        self.assertEqual(model, "gpt-4.1")
        self.assertEqual(api_key, "sk-gammal")
        self.assertEqual(base_url, "https://api.mammouth.ai/v1")
        self.assertEqual(max_tokens, 300)

    def test_resolve_ai_settings_uses_built_in_provider_default_url(self):
        _, _, _, base_url, _ = log_agent._resolve_ai_settings(
            {"AI_PROVIDER": "anthropic", "AI_API_KEY": "sk-ant"}
        )
        self.assertEqual(base_url, "https://api.anthropic.com/v1")

    def test_openai_compatible_call(self):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, payload=json, headers=headers, timeout=timeout)
            return _FakeResponse(
                200, {"choices": [{"message": {"content": "  Summary.  "}}]}
            )

        log_agent.AI_PROVIDER = "openai"
        log_agent.AI_MODEL = "gpt-4o-mini"
        log_agent.AI_API_KEY = "sk-test"
        log_agent.AI_BASE_URL = "https://api.openai.com/v1"
        log_agent.AI_MAX_TOKENS = 300

        with mock.patch.object(log_agent.requests, "post", side_effect=fake_post):
            result = log_agent._call_openai_compatible(
                [{"role": "user", "content": "Analyze"}]
            )

        self.assertEqual(result, "Summary.")
        self.assertEqual(captured["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(captured["payload"]["model"], "gpt-4o-mini")
        self.assertEqual(captured["payload"]["max_tokens"], 300)

    def test_anthropic_call(self):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, payload=json, headers=headers)
            return _FakeResponse(
                200, {"content": [{"type": "text", "text": "Claude reply."}]}
            )

        log_agent.AI_PROVIDER = "anthropic"
        log_agent.AI_MODEL = "claude-sonnet-4-20250514"
        log_agent.AI_API_KEY = "sk-ant-test"
        log_agent.AI_BASE_URL = "https://api.anthropic.com/v1"
        log_agent.AI_MAX_TOKENS = 200

        with mock.patch.object(log_agent.requests, "post", side_effect=fake_post):
            result = log_agent._call_anthropic([
                {"role": "system", "content": "Reply in English."},
                {"role": "user", "content": "Analyze the logs."},
            ])

        self.assertEqual(result, "Claude reply.")
        self.assertEqual(captured["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(captured["headers"]["x-api-key"], "sk-ant-test")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["payload"]["system"], "Reply in English.")
        self.assertEqual(len(captured["payload"]["messages"]), 1)
        self.assertEqual(captured["payload"]["max_tokens"], 200)

    def test_analyze_with_ai_dispatches_on_provider(self):
        log_agent.AI_PROVIDER = "anthropic"
        with mock.patch.object(log_agent, "_call_anthropic", return_value="reply") as anthropic_mock, \
             mock.patch.object(log_agent, "_call_openai_compatible", return_value="reply") as openai_mock:
            log_agent.analyze_with_ai([{"role": "user", "content": "x"}])
        anthropic_mock.assert_called_once()
        openai_mock.assert_not_called()

    def test_ai_error_status_returns_none(self):
        def fake_post(url, json=None, headers=None, timeout=None):
            return _FakeResponse(500, {})

        log_agent.AI_PROVIDER = "openai"
        log_agent.AI_API_KEY = "sk-test"
        log_agent.AI_BASE_URL = "https://api.openai.com/v1"

        with mock.patch.object(log_agent.requests, "post", side_effect=fake_post):
            result = log_agent._call_openai_compatible(
                [{"role": "user", "content": "Analyze"}]
            )
        self.assertIsNone(result)


class PushoverNotificationTests(unittest.TestCase):
    """Tests for --cli mode: notifications print to stdout instead of Pushover."""

    def setUp(self):
        self.original = {
            name: getattr(log_agent, name)
            for name in ("SERVER_NAME", "PUSHOVER_TOKEN", "PUSHOVER_USER", "PUSHOVER_URL")
        }
        log_agent.SERVER_NAME = "testhost"

    def tearDown(self):
        for name, value in self.original.items():
            setattr(log_agent, name, value)

    def test_cli_mode_prints_notification_and_skips_the_api(self):
        log_agent.PUSHOVER_TOKEN = "pushover-token"
        log_agent.PUSHOVER_USER = "pushover-user"

        with mock.patch.object(
            log_agent.requests, "post",
            side_effect=AssertionError("requests.post must not be called in CLI mode"),
        ) as post_mock, mock.patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            log_agent.send_pushover(
                "⚠️ Server Report: Errors Found",
                "Report ID: 20261006T203040Z-ABC123\n\nAI Analysis:\nDisk error detected.",
                priority=1,
                cli_mode=True,
            )

        post_mock.assert_not_called()
        output = fake_stdout.getvalue()
        self.assertIn("Server Report: Errors Found [testhost]", output)
        self.assertIn("priority 1", output)
        self.assertIn("Report ID: 20261006T203040Z-ABC123", output)
        self.assertIn("AI Analysis:", output)

    def test_normal_mode_posts_the_notification_to_pushover(self):
        log_agent.PUSHOVER_TOKEN = "pushover-token"
        log_agent.PUSHOVER_USER = "pushover-user"
        log_agent.PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

        captured = {}

        def fake_post(url, data=None, headers=None, allow_redirects=False, timeout=None):
            captured.update(
                url=url, payload=data, headers=headers,
                allow_redirects=allow_redirects, timeout=timeout,
            )
            return _FakeResponse(200, {"status": 1})

        with mock.patch.object(log_agent.requests, "post", side_effect=fake_post):
            log_agent.send_pushover("Test title", "Test message", priority=0)

        self.assertEqual(captured["url"], "https://api.pushover.net/1/messages.json")
        self.assertEqual(captured["payload"]["token"], "pushover-token")
        self.assertEqual(captured["payload"]["user"], "pushover-user")
        self.assertEqual(captured["payload"]["title"], "Test title [testhost]")
        self.assertEqual(captured["payload"]["message"], "Test message")
        self.assertEqual(captured["payload"]["priority"], 0)
        self.assertEqual(captured["timeout"], 10)


if __name__ == "__main__":
    unittest.main()