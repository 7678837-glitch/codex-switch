#!/usr/bin/env python3
"""Run a bounded, explicit Responses API function-call smoke check.

The command is deliberately independent of the user's Codex or provider
configuration. It only sends the fixed audit prompt and the model output that
the gateway returned for the second request.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


API_KEY_ENV = "CODEX_SWITCH_SMOKE_API_KEY"
AUDIT_MARKER = "AUDIT_TOOL_OK"
AUDIT_TOOL_NAME = "audit_echo"
PROMPT = "Call the audit_echo function exactly once with an empty object."
SECOND_INSTRUCTIONS = "Return only the fixed audit tool output AUDIT_TOOL_OK."
MAX_RESPONSE_BYTES = 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10

AUDIT_TOOL = {
    "type": "function",
    "name": AUDIT_TOOL_NAME,
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}


class SmokeError(Exception):
    """An expected failure represented only by a safe fixed classification."""

    def __init__(self, code: str, http_status: int | None = None, hint: str | None = None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.hint = hint

    def safe_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": "error", "error": self.code}
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload


class NoRedirect(HTTPRedirectHandler):
    """Keep credentials on the original origin by refusing all redirects."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


# Use a direct connection so an environment proxy cannot receive the bearer
# token or silently change the endpoint semantics under test.
OPENER = build_opener(NoRedirect(), ProxyHandler({}))


def validate_model(model: str | None) -> str | None:
    if model is None:
        return None
    if not model or len(model) > 256 or any(character in model for character in "\r\n"):
        raise SmokeError("invalid_model")
    return model


def is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_base_url(raw_url: str, allow_insecure_localhost: bool) -> str:
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise SmokeError("invalid_base_url") from error
    if not parsed.scheme or not parsed.netloc or hostname is None:
        raise SmokeError("invalid_base_url")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise SmokeError("url_credentials_rejected")
    if parsed.query or parsed.fragment or "?" in raw_url or "#" in raw_url:
        raise SmokeError("url_query_rejected")
    if parsed.scheme == "http":
        if not allow_insecure_localhost:
            raise SmokeError("insecure_transport_requires_flag")
        if not is_loopback(hostname):
            raise SmokeError("insecure_host_rejected")
    elif parsed.scheme != "https":
        raise SmokeError("unsupported_url_scheme")
    return raw_url.rstrip("/")


def endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url}/{suffix}"


def read_limited(response: Any) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise SmokeError("response_too_large")
        except ValueError as error:
            raise SmokeError("invalid_content_length") from error
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise SmokeError("response_too_large")
    return b"".join(chunks)


def request_bytes(
    url: str,
    *,
    method: str,
    api_key: str,
    body: bytes | None = None,
    accept: str = "application/json",
) -> tuple[int, bytes]:
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "codex-switch-provider-live-smoke",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            raw = read_limited(response)
            return status, raw
    except SmokeError:
        raise
    except HTTPError as error:
        status = int(error.code)
        try:
            error.close()
        finally:
            if 300 <= status < 400:
                raise SmokeError("redirect_rejected", status) from None
            raise SmokeError("upstream_http_error", status) from None
    except (TimeoutError, socket.timeout):
        raise SmokeError("network_timeout") from None
    except (URLError, OSError):
        raise SmokeError("network_error") from None


def decode_json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SmokeError("invalid_json") from None


def get_models(base_url: str, api_key: str) -> str:
    status, raw = request_bytes(endpoint(base_url, "models"), method="GET", api_key=api_key)
    if status != 200:
        raise SmokeError("models_http_error", status)
    payload = decode_json(raw)
    if not isinstance(payload, dict):
        raise SmokeError("models_shape_error")
    if isinstance(payload.get("data"), list):
        entries = payload["data"]
        key = "id"
    elif isinstance(payload.get("models"), list):
        entries = payload["models"]
        key = "slug"
    else:
        raise SmokeError("models_shape_error")
    candidates = [
        entry[key]
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get(key), str)
        and "luna" in entry[key].casefold()
    ]
    if len(candidates) != 1:
        raise SmokeError("model_selection_required", hint="pass --model")
    return validate_model(candidates[0]) or ""


def parse_sse(raw: bytes) -> Iterator[tuple[str | None, dict[str, object]]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SmokeError("invalid_sse") from None
    event: str | None = None
    data_lines: list[str] = []

    def flush() -> tuple[str | None, dict[str, object]] | None:
        nonlocal event, data_lines
        if not data_lines:
            event = None
            return None
        data = "\n".join(data_lines)
        data_lines = []
        current_event = event
        event = None
        if data.strip() == "[DONE]":
            return None
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            raise SmokeError("invalid_sse") from None
        if not isinstance(payload, dict):
            raise SmokeError("invalid_sse")
        return current_event, payload

    for line in text.splitlines():
        if not line:
            item = flush()
            if item is not None:
                yield item
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].lstrip(" ")
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        # id/retry and unknown SSE fields do not affect the response contract.
    item = flush()
    if item is not None:
        yield item


def completed_output(payload: dict[str, object]) -> list[dict[str, object]]:
    response = payload.get("response")
    if isinstance(response, dict):
        output = response.get("output")
    else:
        output = payload.get("output")
    if not isinstance(output, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("type"), str) for item in output
    ):
        raise SmokeError("response_output_invalid")
    return output


def output_text(output: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for item in output:
        if item.get("type") == "output_text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                parts.append(part["text"])
    return "".join(parts)


def parse_response(raw: bytes, *, require_echo: bool) -> tuple[list[dict[str, object]], str]:
    statuses: list[str] = []
    final_output: list[dict[str, object]] | None = None
    for event, payload in parse_sse(raw):
        top_status = payload.get("status")
        response = payload.get("response")
        nested_status = response.get("status") if isinstance(response, dict) else None
        for status_value in (top_status, nested_status):
            if isinstance(status_value, str):
                statuses.append(status_value)
                if status_value in {"error", "failed", "incomplete", "cancelled"}:
                    raise SmokeError("response_not_completed")
        payload_type = payload.get("type")
        if event in {"error", "response.error", "response.failed", "response.incomplete"}:
            raise SmokeError("response_not_completed")
        if isinstance(payload_type, str) and payload_type in {
            "error",
            "response.error",
            "response.failed",
            "response.incomplete",
        }:
            raise SmokeError("response_not_completed")
        is_completed = event == "response.completed" or payload_type == "response.completed"
        if is_completed:
            completion_status = nested_status if isinstance(nested_status, str) else top_status
            if completion_status != "completed":
                raise SmokeError("response_not_completed")
            final_output = completed_output(payload)
    if final_output is None or not statuses or statuses[-1] != "completed":
        raise SmokeError("response_not_completed")
    if require_echo:
        calls = [item for item in final_output if item.get("type") == "function_call"]
        matching = [
            item
            for item in calls
            if item.get("name") == AUDIT_TOOL_NAME and valid_empty_arguments(item.get("arguments"))
        ]
        if len(calls) != 1 or len(matching) != 1:
            raise SmokeError("audit_function_call_invalid")
    return final_output, output_text(final_output)


def valid_empty_arguments(arguments: object) -> bool:
    if isinstance(arguments, dict):
        return arguments == {}
    if not isinstance(arguments, str):
        return False
    try:
        return json.loads(arguments) == {}
    except json.JSONDecodeError:
        return False


def post_response(base_url: str, api_key: str, payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode()
    status, raw = request_bytes(
        endpoint(base_url, "responses"),
        method="POST",
        api_key=api_key,
        body=body,
        accept="text/event-stream",
    )
    if status != 200:
        raise SmokeError("responses_http_error", status)
    return raw


def first_request(model: str) -> dict[str, object]:
    return {
        "model": model,
        "input": PROMPT,
        "tools": [AUDIT_TOOL],
        "tool_choice": {"type": "function", "name": AUDIT_TOOL_NAME},
        "stream": True,
        "store": False,
        "parallel_tool_calls": False,
        "max_output_tokens": 128,
    }


def second_request(model: str, final_output: list[dict[str, object]], call_id: str) -> dict[str, object]:
    return {
        "model": model,
        "instructions": SECOND_INSTRUCTIONS,
        "input": final_output
        + [{"type": "function_call_output", "call_id": call_id, "output": AUDIT_MARKER}],
        "tool_choice": "none",
        "stream": True,
        "store": False,
        "parallel_tool_calls": False,
        "max_output_tokens": 128,
    }


def run_smoke(base_url: str, model: str | None) -> tuple[int, int]:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise SmokeError("missing_api_key")
    if any(character in api_key for character in "\r\n"):
        raise SmokeError("invalid_api_key")
    models_requests = 0
    if model is None:
        models_requests = 1
        selected_model = get_models(base_url, api_key)
    else:
        selected_model = model
    first_raw = post_response(base_url, api_key, first_request(selected_model))
    first_output, _ = parse_response(first_raw, require_echo=True)
    calls = [item for item in first_output if item.get("type") == "function_call"]
    if len(calls) != 1 or not isinstance(calls[0].get("call_id"), str) or not calls[0]["call_id"]:
        raise SmokeError("audit_function_call_invalid")
    second_raw = post_response(
        base_url,
        api_key,
        second_request(selected_model, first_output, calls[0]["call_id"]),
    )
    _, text = parse_response(second_raw, require_echo=False)
    if AUDIT_MARKER not in text:
        raise SmokeError("audit_marker_missing")
    return models_requests, 2


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--base-url", required=True)
    command.add_argument("--model")
    command.add_argument("--run", action="store_true")
    command.add_argument("--allow-insecure-localhost", action="store_true")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        base_url = validate_base_url(args.base_url, args.allow_insecure_localhost)
        model = validate_model(args.model)
        if not args.run:
            print(
                json.dumps(
                    {
                        "status": "dry_run",
                        "network": False,
                        "requests": 0,
                        "models_requests": 0,
                        "responses_requests": 0,
                        "model": model or "unique_luna_required",
                    },
                    separators=(",", ":"),
                )
            )
            return 0
        models_requests, responses_requests = run_smoke(base_url, model)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "http_status": 200,
                    "requests": models_requests + responses_requests,
                    "models_requests": models_requests,
                    "responses_requests": responses_requests,
                    "audit_function_call": True,
                    "audit_marker": True,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except SmokeError as error:
        print(json.dumps(error.safe_payload(), separators=(",", ":")), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, Exception):
        print(json.dumps({"status": "error", "error": "internal_error"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
