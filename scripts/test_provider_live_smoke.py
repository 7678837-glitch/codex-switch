#!/usr/bin/env python3
"""Behavior contracts for the explicit provider live-smoke entry point."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provider-live-smoke.py"
API_KEY = "test-live-smoke-key"
UPSTREAM_SECRET = "upstream-private-error"


def sse(*events: tuple[str | None, dict[str, object]]) -> bytes:
    """Encode standard event/data frames with CRLF separators."""

    frames: list[str] = []
    for event, payload in events:
        lines = []
        if event is not None:
            lines.append(f"event: {event}")
        lines.append(f"data: {json.dumps(payload, separators=(',', ':'))}")
        frames.append("\r\n".join(lines))
    return ("\r\n\r\n".join(frames) + "\r\n\r\n").encode()


def completed_function_call() -> bytes:
    call = {
        "id": "fc_audit",
        "status": "completed",
        "type": "function_call",
        "name": "audit_echo",
        "call_id": "call_audit_echo",
        "arguments": "{}",
    }
    return sse(
        ("response.output_item.added", {"type": "response.output_item.added", "item": {
            **call, "status": "in_progress", "arguments": ""
        }}),
        ("response.output_item.done", {"type": "response.output_item.done", "item": call}),
        (
            "response.completed",
            {"type": "response.completed", "response": {"status": "completed", "output": [call]}},
        ),
    )


def completed_tool_result() -> bytes:
    return sse(
        (
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": "AUDIT_TOOL_OK"},
        ),
        (
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "AUDIT_TOOL_OK"}],
                        }
                    ],
                },
            },
        ),
    )


def failed_response() -> bytes:
    return sse(
        (
            "response.failed",
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {"message": UPSTREAM_SECRET},
                },
            },
        ),
    )


def incomplete_response() -> bytes:
    return sse(
        (
            "response.incomplete",
            {"type": "response.incomplete", "response": {"status": "incomplete"}},
        ),
    )


def function_call_without_completion() -> bytes:
    call = {
        "type": "function_call",
        "name": "audit_echo",
        "call_id": "call_audit_echo",
        "arguments": "{}",
    }
    return sse(("response.output_item.done", {"type": "response.output_item.done", "item": call}))


class LocalFixture:
    def __init__(
        self,
        *,
        models: list[dict[str, object]] | None = None,
        catalog_key: str = "data",
        responses: list[bytes] | None = None,
        redirect_first_response: bool = False,
    ) -> None:
        self.models = models if models is not None else [{"id": "audit-luna"}]
        self.catalog_key = catalog_key
        self.responses = list(responses or [completed_function_call(), completed_tool_result()])
        self.redirect_first_response = redirect_first_response
        self.requests: list[dict[str, object]] = []
        self._lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                fixture.handle_get(self)

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                fixture.handle_post(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "LocalFixture":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self.requests.append(
                {"method": "GET", "path": handler.path, "headers": dict(handler.headers)}
            )
        if handler.path != "/v1/models":
            self.send_json(handler, 404, {"error": "unexpected path"})
            return
        self.send_json(handler, 200, {self.catalog_key: self.models})

    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length", "0"))
        raw_body = handler.rfile.read(length)
        try:
            body: object = json.loads(raw_body)
        except json.JSONDecodeError:
            body = raw_body.decode("utf-8", errors="replace")
        request = {
            "method": "POST",
            "path": handler.path,
            "headers": dict(handler.headers),
            "body": body,
        }
        with self._lock:
            self.requests.append(request)
            post_number = sum(item["method"] == "POST" for item in self.requests)

        if handler.path != "/v1/responses":
            self.send_json(handler, 404, {"error": "unexpected path"})
            return
        if self.redirect_first_response and post_number == 1:
            handler.send_response(302)
            handler.send_header("Location", "/v1/leak")
            handler.send_header("Content-Length", str(len(UPSTREAM_SECRET)))
            handler.end_headers()
            handler.wfile.write(UPSTREAM_SECRET.encode())
            return
        index = post_number - 1
        payload = self.responses[index] if index < len(self.responses) else failed_response()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    @staticmethod
    def send_json(handler: BaseHTTPRequestHandler, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


def run_cli(
    base_url: str,
    *extra: str,
    api_key: str | None = API_KEY,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("CODEX_SWITCH_SMOKE_API_KEY", None)
    if api_key is not None:
        environment["CODEX_SWITCH_SMOKE_API_KEY"] = api_key
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base-url", base_url, *extra],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


class ProviderLiveSmokeContracts(unittest.TestCase):
    def test_dry_run_does_not_read_key_or_make_network_request(self) -> None:
        with LocalFixture() as fixture:
            result = run_cli(
                fixture.base_url,
                "--allow-insecure-localhost",
                "--model",
                "explicit-model",
                api_key=None,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.requests, [])
            self.assertIn("dry_run", result.stdout)
            self.assertIn("explicit-model", result.stdout)
            self.assertNotIn(API_KEY, result.stdout + result.stderr)

    def test_explicit_model_runs_exact_two_requests_and_replays_tool_output(self) -> None:
        with LocalFixture() as fixture:
            result = run_cli(
                fixture.base_url,
                "--allow-insecure-localhost",
                "--run",
                "--model",
                "explicit-model",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            requests = fixture.requests
            self.assertEqual([request["method"] for request in requests], ["POST", "POST"])
            first, second = (request["body"] for request in requests)
            self.assert_post_contract(first, expected_model="explicit-model", require_echo_tool=True)
            self.assert_post_contract(second, expected_model="explicit-model", require_echo_tool=False)
            self.assertEqual(first["tool_choice"], {"type": "function", "name": "audit_echo"})
            self.assertEqual(second["tool_choice"], "none")
            self.assertEqual(
                second["instructions"],
                "Return only the fixed audit tool output AUDIT_TOOL_OK.",
            )
            input_items = second["input"]
            function_calls = [item for item in input_items if item.get("type") == "function_call"]
            self.assertEqual(len(function_calls), 1)
            self.assertEqual(function_calls[0]["arguments"], "{}")
            self.assertEqual(function_calls[0]["status"], "completed")
            tool_outputs = [item for item in input_items if item.get("type") == "function_call_output"]
            self.assertEqual(len(tool_outputs), 1)
            self.assertEqual(tool_outputs[0]["output"], "AUDIT_TOOL_OK")
            success = json.loads(result.stdout)
            self.assertTrue(success["audit_marker"])
            self.assertEqual(success["requests"], 2)
            self.assertEqual(success["models_requests"], 0)
            self.assertEqual(success["responses_requests"], 2)
            self.assertEqual(requests[0]["headers"]["Authorization"], f"Bearer {API_KEY}")

    def test_unique_luna_model_is_selected_after_one_models_request(self) -> None:
        for catalog_key, models, expected_model in (
            ("data", [{"id": "audit-luna"}, {"id": "audit-sol"}], "audit-luna"),
            ("models", [{"slug": "audit-luna"}, {"slug": "audit-sol"}], "audit-luna"),
        ):
            with self.subTest(catalog_key=catalog_key), LocalFixture(
                models=models, catalog_key=catalog_key
            ) as fixture:
                result = run_cli(fixture.base_url, "--allow-insecure-localhost", "--run")

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [request["method"] for request in fixture.requests], ["GET", "POST", "POST"]
                )
                self.assertEqual(fixture.requests[1]["body"]["model"], expected_model)
                self.assertEqual(fixture.requests[2]["body"]["model"], expected_model)
                success = json.loads(result.stdout)
                self.assertEqual(success["requests"], 3)
                self.assertEqual(success["models_requests"], 1)
                self.assertEqual(success["responses_requests"], 2)

    def test_default_model_rejects_zero_or_multiple_luna_candidates(self) -> None:
        for models in ([], [{"id": "audit-luna"}, {"id": "other-luna"}]):
            with self.subTest(models=models), LocalFixture(models=models) as fixture:
                result = run_cli(fixture.base_url, "--allow-insecure-localhost", "--run")

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([request["method"] for request in fixture.requests], ["GET"])
                self.assertIn("--model", result.stdout + result.stderr)
                self.assertNotIn(API_KEY, result.stdout + result.stderr)

    def test_first_response_failure_stops_before_replay_and_sanitizes_output(self) -> None:
        with LocalFixture(responses=[failed_response()]) as fixture:
            result = run_cli(
                fixture.base_url,
                "--allow-insecure-localhost",
                "--run",
                "--model",
                "explicit-model",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual([request["method"] for request in fixture.requests], ["POST"])
            self.assertNotIn(API_KEY, result.stdout + result.stderr)
            self.assertNotIn(UPSTREAM_SECRET, result.stdout + result.stderr)

    def test_incomplete_or_missing_completed_event_stops_before_replay(self) -> None:
        for first_response in (incomplete_response(), function_call_without_completion()):
            with self.subTest(first_response=first_response), LocalFixture(
                responses=[first_response, completed_tool_result()]
            ) as fixture:
                result = run_cli(
                    fixture.base_url,
                    "--allow-insecure-localhost",
                    "--run",
                    "--model",
                    "explicit-model",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([request["method"] for request in fixture.requests], ["POST"])

    def test_redirect_is_rejected_without_forwarding_secret(self) -> None:
        with LocalFixture(redirect_first_response=True) as fixture:
            result = run_cli(
                fixture.base_url,
                "--allow-insecure-localhost",
                "--run",
                "--model",
                "explicit-model",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual([request["path"] for request in fixture.requests], ["/v1/responses"])
            self.assertNotIn(API_KEY, result.stdout + result.stderr)
            self.assertNotIn(UPSTREAM_SECRET, result.stdout + result.stderr)

    def test_http_requires_explicit_allow_insecure_localhost(self) -> None:
        with LocalFixture() as fixture:
            result = run_cli(fixture.base_url, "--run", "--model", "explicit-model")

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fixture.requests, [])
            self.assertNotIn(API_KEY, result.stdout + result.stderr)

    def assert_post_contract(
        self, body: object, *, expected_model: str, require_echo_tool: bool
    ) -> None:
        self.assertIsInstance(body, dict)
        self.assertEqual(body["model"], expected_model)
        self.assertTrue(body["stream"])
        self.assertFalse(body["store"])
        self.assertFalse(body["parallel_tool_calls"])
        self.assertEqual(body["max_output_tokens"], 128)
        if require_echo_tool:
            self.assertEqual(
                body["tools"],
                [
                    {
                        "type": "function",
                        "name": "audit_echo",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
