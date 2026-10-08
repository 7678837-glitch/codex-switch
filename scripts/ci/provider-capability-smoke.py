#!/usr/bin/env python3
"""Exercise a real Codex CLI against a loopback-only Responses/MCP fixture.

No model service or real credentials are used. The mock emits deterministic tool
calls; this checks transport/configuration, not an LLM's ability to choose tools.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REQUEST_LIMIT = 16
COMMAND_TIMEOUT_SECONDS = 45
PROCESS_CLEANUP_TIMEOUT_SECONDS = 10
CHILD_START_TIMEOUT_SECONDS = 10
CHILD_RESPONSE_TIMEOUT_SECONDS = 30
V2_OPERATIONS = ("spawn_agent", "send_message", "followup_task", "interrupt_agent")
LOCAL_AUDIT_PROMPT = "Run the deterministic local audit."
CLEANUP_PROMPT = "Run the deterministic cleanup launch."
RESUME_PROMPT = "Resume the deterministic local audit."
V2_MESSAGE_MARKER = "AUDIT_V2_MESSAGE"
V2_FOLLOWUP_MARKER = "AUDIT_V2_FOLLOWUP"


def extract_thread_id(stdout):
    """Return the only exact thread.started id in a Codex JSONL stream."""
    found = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "thread.started" and isinstance(item.get("thread_id"), str):
            if item["thread_id"].strip():
                found.append(item["thread_id"])
    if len(found) != 1:
        raise ValueError(f"expected exactly one thread.started record, found {len(found)}")
    return found[0]


def check_request_budget(request_count, limit=REQUEST_LIMIT):
    if request_count > limit:
        raise ValueError(f"Responses request limit exceeded: {request_count} > {limit}")


def _has_function_call(body, call_id):
    return any(
        isinstance(item, dict)
        and item.get("type") == "function_call"
        and item.get("call_id") == call_id
        for item in body.get("input", [])
    )


def _function_call_output(body, call_id):
    for item in body.get("input", []):
        if (
            isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        ):
            return item
    return None


def _decode_tool_output(item):
    if not item:
        return None
    output = item.get("output")
    if isinstance(output, str):
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output
    if isinstance(output, list):
        texts = []
        for part in output:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        joined = "".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined
    return output


def _child_text(child):
    return child.get("text", "") if isinstance(child, dict) else ""


def _child_has_input_markers(child):
    if not isinstance(child, dict):
        return False
    markers = child.get("input_markers")
    return isinstance(markers, list) and {V2_MESSAGE_MARKER, V2_FOLLOWUP_MARKER}.issubset(markers)


def _successful_empty_tool_result(result):
    return (
        isinstance(result, dict)
        and result.get("observed") is True
        and result.get("text") == ""
        and not result.get("error")
    )


def validate_v2_lifecycle(evidence):
    """Validate ordered V2 results from real tool outputs and child turns.

    The fixture builds this normalized evidence only from observed Responses
    ``function_call_output`` items, the parent's ``agent_message`` for the
    initial child turn, and an active child request whose input carries both
    lifecycle markers.  A tool name, a function call, or a fixture-authored
    SSE item is therefore insufficient.
    """
    result = {operation: False for operation in V2_OPERATIONS}
    state = "initial"
    for event in evidence:
        if not isinstance(event, dict):
            state = "invalid"
            continue
        operation = event.get("operation")
        tool_result = event.get("result")
        child = event.get("child")
        child_status = child.get("status") if isinstance(child, dict) else None
        child_text = _child_text(child)
        child_observed = bool(child.get("observed")) if isinstance(child, dict) else False
        if state == "initial" and operation == "spawn_agent":
            task_name = tool_result.get("task_name") if isinstance(tool_result, dict) else None
            parent_received = bool(child.get("parent_received")) if isinstance(child, dict) else False
            if (
                task_name
                and child_observed
                and child_status == "completed"
                and "AUDIT_V2_CHILD_READY" in child_text
                and parent_received
            ):
                result[operation] = True
                state = "spawned"
            else:
                state = "invalid"
        elif state == "spawned" and operation == "send_message":
            if (
                _successful_empty_tool_result(tool_result)
                and child_observed
                and child.get("active") is True
                and child.get("http_opened") is True
                and _child_has_input_markers(child)
            ):
                result[operation] = True
                state = "messaged"
            else:
                state = "invalid"
        elif state == "messaged" and operation == "followup_task":
            if (
                _successful_empty_tool_result(tool_result)
                and child_observed
                and child.get("active") is True
                and child.get("http_opened") is True
                and _child_has_input_markers(child)
            ):
                result[operation] = True
                state = "followed_up"
            else:
                state = "invalid"
        elif state == "followed_up" and operation == "interrupt_agent":
            previous_status = (
                tool_result.get("previous_status") if isinstance(tool_result, dict) else None
            )
            if (
                previous_status == "running"
                and child_observed
                and child.get("active") is True
                and child.get("http_opened") is True
                and _child_has_input_markers(child)
            ):
                result[operation] = True
                state = "complete"
            else:
                state = "invalid"
        else:
            state = "invalid"
    return result


def mcp_server():
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "provider-audit", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{
                "name": "echo", "description": "Return a deterministic audit marker.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
            }]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "AUDIT_MCP_OK"}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


def tool_specs(body):
    found = []

    def visit(value, namespace=None):
        if isinstance(value, dict):
            if value.get("type") == "namespace":
                namespace = value.get("name")
            if value.get("type") in ("function", "custom"):
                found.append((namespace, value))
            for child in value.values():
                visit(child, namespace)
        elif isinstance(value, list):
            for child in value:
                visit(child, namespace)

    visit(body.get("tools", []))
    for item in body.get("input", []):
        if item.get("type") == "additional_tools":
            visit(item)
    return found


def message(text):
    return {"type": "message", "id": "msg_audit", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


class Fixture:
    def __init__(self, child_model=None, v2_lifecycle=False):
        self.requests = []
        self.lock = threading.Lock()
        self.child_model = child_model
        self.v2_lifecycle = v2_lifecycle
        self.child_initial_sent = False
        self.child_initial_event = None
        self.child_parent_received = False
        self.child_active_event = None
        self.child_item_sent = threading.Event()
        self.child_release = threading.Event()
        self.child_events = []
        self.interrupt_observed = False
        self.tool_outputs = {}
        self.request_budget_exceeded = False

    def output(self, body, headers):
        # A fork includes the parent history, so use the protocol's child marker.
        if headers.get("x-openai-subagent"):
            if not self.v2_lifecycle or self.interrupt_observed:
                return message("AUDIT_CHILD_OK")
            if not self.child_initial_sent:
                self.child_initial_sent = True
                return message("AUDIT_V2_CHILD_READY")
            child_input = json.dumps(body.get("input", []), ensure_ascii=False)
            input_markers = [
                marker
                for marker in (V2_MESSAGE_MARKER, V2_FOLLOWUP_MARKER)
                if marker in child_input
            ]
            if len(input_markers) == 2:
                self.child_active_event = {
                    "observed": True,
                    "active": False,
                    "http_opened": False,
                    "input_markers": input_markers,
                }
                return PendingChildResponse(self)
            return message("AUDIT_V2_CHILD_WAITING")

        specs = tool_specs(body)
        body_input = json.dumps(body.get("input", []), ensure_ascii=False)
        if CLEANUP_PROMPT in body_input:
            return message("AUDIT_CLEANUP_OK")
        if RESUME_PROMPT in body_input:
            return message("AUDIT_V2_PARENT_OK" if self.v2_lifecycle else "AUDIT_PARENT_OK")
        if not _has_function_call(body, "audit_mcp"):
            selected = next(((ns, spec) for ns, spec in specs
                             if "echo" in spec.get("name", "")), None)
            if selected:
                return self.call("audit_mcp", selected, {})
        if not _has_function_call(body, "audit_spawn"):
            selected = next(((ns, spec) for ns, spec in specs
                             if spec.get("name") == "spawn_agent"), None)
            if selected:
                properties = selected[1].get("parameters", {}).get("properties", {})
                args = {"message": "Return AUDIT_CHILD_OK. Do not call any tools."}
                if "task_name" in properties:
                    args["task_name"] = "audit_child"
                if "fork_turns" in properties:
                    args["fork_turns"] = "none"
                if self.child_model:
                    args["model"] = self.child_model
                return self.call("audit_spawn", selected, args)

        if not _has_function_call(body, "audit_wait"):
            selected = next(((ns, spec) for ns, spec in specs
                             if spec.get("name") in ("wait", "wait_agent")), None)
            if selected:
                properties = selected[1].get("parameters", {}).get("properties", {})
                args = {}
                ids_key = next((key for key in ("targets", "agent_ids", "ids") if key in properties), None)
                if ids_key:
                    for item in body.get("input", []):
                        if item.get("type") == "function_call_output" and item.get("call_id") == "audit_spawn":
                            output = item.get("output", "{}")
                            if isinstance(output, str):
                                result = json.loads(output)
                                if result.get("agent_id"):
                                    args[ids_key] = [result["agent_id"]]
                    if not args.get(ids_key):
                        return message("AUDIT_SPAWN_FAILED")
                if "timeout_ms" in properties:
                    args["timeout_ms"] = 10000
                return self.call("audit_wait", selected, args)

        if self.v2_lifecycle:
            if not _has_function_call(body, "audit_message"):
                selected = next(((ns, spec) for ns, spec in specs
                                 if spec.get("name") == "send_message"), None)
                if selected:
                    return self.call(
                        "audit_message",
                        selected,
                        {"target": "/root/audit_child", "message": V2_MESSAGE_MARKER},
                    )
            if not _has_function_call(body, "audit_followup"):
                selected = next(((ns, spec) for ns, spec in specs
                                 if spec.get("name") == "followup_task"), None)
                if selected:
                    return self.call(
                        "audit_followup",
                        selected,
                        {"target": "/root/audit_child", "message": V2_FOLLOWUP_MARKER},
                    )
            if not _has_function_call(body, "audit_interrupt"):
                selected = next(((ns, spec) for ns, spec in specs
                                 if spec.get("name") == "interrupt_agent"), None)
                if selected:
                    # The child request must have opened its HTTP turn before
                    # interrupt is requested; an already completed child is not
                    # evidence that interrupt_agent executed.
                    self.child_item_sent.wait(CHILD_START_TIMEOUT_SECONDS)
                    return self.call(
                        "audit_interrupt",
                        selected,
                        {"target": "/root/audit_child"},
                    )

            interrupt_output = _function_call_output(body, "audit_interrupt")
            if interrupt_output is not None:
                decoded = _decode_tool_output(interrupt_output)
                previous_status = decoded.get("previous_status") if isinstance(decoded, dict) else None
                self.interrupt_observed = (
                    previous_status == "running"
                    and self.child_active_event is not None
                    and self.child_active_event.get("active") is True
                )
                if self.child_active_event is not None and self.child_active_event.get("active") is True:
                    self.child_release.set()
                return message("AUDIT_V2_PARENT_OK")
        return message("AUDIT_PARENT_OK")

    def mark_pending_child_item(self):
        if self.child_active_event is not None:
            self.child_active_event["active"] = True
            self.child_active_event["http_opened"] = True
        self.child_item_sent.set()

    def complete_pending_child(self, released):
        if self.child_active_event is not None:
            self.child_active_event["released_by_interrupt"] = bool(released)

    def record_tool_outputs(self, body):
        for operation in ("audit_spawn", "audit_message", "audit_followup", "audit_interrupt"):
            item = _function_call_output(body, operation)
            if item is not None:
                self.tool_outputs[operation] = item
        for item in body.get("input", []):
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and "AUDIT_V2_CHILD_READY" in json.dumps(item, ensure_ascii=False)
                and not self.child_parent_received
            ):
                self.child_parent_received = True
                self.child_initial_event = {
                    "observed": True,
                    "status": "completed",
                    "text": "AUDIT_V2_CHILD_READY",
                    "parent_received": True,
                }
                self.child_events.append(self.child_initial_event)

    def lifecycle_evidence(self):
        spawn = _decode_tool_output(self.tool_outputs.get("audit_spawn"))
        message_output = _decode_tool_output(self.tool_outputs.get("audit_message"))
        followup_output = _decode_tool_output(self.tool_outputs.get("audit_followup"))
        interrupt_output = _decode_tool_output(self.tool_outputs.get("audit_interrupt"))
        return [
            {
                "operation": "spawn_agent",
                "result": spawn if isinstance(spawn, dict) else {},
                "child": self.child_initial_event or {},
            },
            {
                "operation": "send_message",
                "result": {
                    "observed": "audit_message" in self.tool_outputs,
                    "text": message_output if isinstance(message_output, str) else message_output,
                    "error": message_output != "" if isinstance(message_output, str) else True,
                },
                "child": self.child_active_event or {},
            },
            {
                "operation": "followup_task",
                "result": {
                    "observed": "audit_followup" in self.tool_outputs,
                    "text": followup_output if isinstance(followup_output, str) else followup_output,
                    "error": followup_output != "" if isinstance(followup_output, str) else True,
                },
                "child": self.child_active_event or {},
            },
            {
                "operation": "interrupt_agent",
                "result": interrupt_output if isinstance(interrupt_output, dict) else {},
                "child": self.child_active_event or {},
            },
        ]

    @staticmethod
    def call(call_id, selected, arguments):
        namespace, spec = selected
        item = {"type": "function_call", "call_id": call_id,
                "name": spec["name"], "arguments": json.dumps(arguments)}
        if namespace:
            item["namespace"] = namespace
        return item


class PendingChildResponse:
    """Hold a real child request open until the parent interrupt result arrives."""

    def __init__(self, fixture):
        self.fixture = fixture
        self.item = message("AUDIT_V2_CHILD_ACTIVE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True, type=Path)
    parser.add_argument("--switch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--catalog", choices=("generic", "native"), default="generic")
    parser.add_argument("--child-model", action="store_true", help="Select a second saved model for the child")
    parser.add_argument(
        "--v2-lifecycle",
        action="store_true",
        help="Exercise native V2 send_message, followup_task and interrupt_agent",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Run an independent cleanup launch, then resume the first exact thread id",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = Fixture(
        "audit-child-model" if args.child_model else None,
        v2_lifecycle=args.v2_lifecycle,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            catalog = {"data": [{"id": "audit-model", "object": "model"}]}
            if args.catalog == "native":
                catalog = {"models": [{
                    "slug": "audit-model", "display_name": "Audit model",
                    "model_messages": {"instructions_template": "Complete the isolated capability audit."},
                    "supported_reasoning_levels": [], "context_window": 32768,
                    "input_modalities": ["text", "image"], "apply_patch_tool_type": "freeform",
                    "multi_agent_version": "v2", "use_responses_lite": True,
                }]}
            if args.child_model:
                if args.catalog == "native":
                    catalog["models"].append(dict(catalog["models"][0], slug="audit-child-model"))
                else:
                    catalog["data"].append({"id": "audit-child-model", "object": "model"})
            body = json.dumps(catalog).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            headers = dict((key.lower(), value) for key, value in self.headers.items())
            with fixture.lock:
                fixture.requests.append({"path": self.path, "headers": headers, "body": body})
                (args.output / "requests.json").write_text(json.dumps(fixture.requests, indent=2), encoding="utf-8")
                model_request_count = sum("input" in request["body"] for request in fixture.requests)
            if model_request_count > REQUEST_LIMIT:
                fixture.request_budget_exceeded = True
                failure = {
                    "error": "Responses request limit exceeded",
                    "request_count": model_request_count,
                    "limit": REQUEST_LIMIT,
                }
                (args.output / "budget-error.json").write_text(
                    json.dumps(failure, indent=2), encoding="utf-8"
                )
                payload = json.dumps({"error": failure}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("x-should-retry", "false")
                self.end_headers()
                self.wfile.write(payload)
                return
            if "input" not in body:
                payload = json.dumps({"error": {"message": "input is required"}}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
                return
            if not headers.get("x-openai-subagent"):
                fixture.record_tool_outputs(body)
            item = fixture.output(body, headers)
            if isinstance(item, PendingChildResponse):
                head_events = [
                    {"type": "response.created", "response": {"id": "resp_audit_child"}},
                    {"type": "response.output_item.done", "item": item.item},
                ]
                head_payload = "".join(
                    "data: " + json.dumps(event) + "\n\n" for event in head_events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(head_payload)
                self.wfile.flush()
                fixture.mark_pending_child_item()
                released = fixture.child_release.wait(CHILD_RESPONSE_TIMEOUT_SECONDS)
                fixture.complete_pending_child(released)
                tail_event = {
                    "type": "response.completed",
                    "response": {"id": "resp_audit_child",
                                 "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
                }
                try:
                    self.wfile.write(("data: " + json.dumps(tail_event) + "\n\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Interrupt closes the client stream before this fixture releases it.
                    pass
                return
            events = [
                {"type": "response.created", "response": {"id": "resp_audit"}},
                {"type": "response.output_item.done", "item": item},
                {"type": "response.completed", "response": {"id": "resp_audit",
                 "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
            ]
            payload = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="provider-capability-") as temp:
            root = Path(temp)
            codex_home = root / ".codex"
            codex_home.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            if os.name == "nt":
                (bin_dir / "codex.cmd").write_text(f'@echo off\n"{args.codex.resolve()}" %*\n')
            else:
                (bin_dir / "codex").symlink_to(args.codex.resolve())
            env = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("CODEX_", "CS_", "OPENAI_"))
                   and key.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
            env.update(HOME=str(root), USERPROFILE=str(root), CODEX_HOME=str(codex_home),
                       CODEX_SWITCH_HOME=str(root / ".codex-switch"), NO_PROXY="127.0.0.1,localhost",
                       PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""))
            config = '\n'.join([
                'web_search = "disabled"',
                '[features]',
                'plugins = false',
                'apps = false',
                '[mcp_servers.audit]',
                'default_tools_approval_mode = "approve"',
                'command = ' + json.dumps(sys.executable),
                'args = ' + json.dumps([str(Path(__file__).resolve()), "--mcp"]),
            ]) + '\n'
            (codex_home / "config.toml").write_text(config, encoding="utf-8")

            def run(name, command, stdin=None):
                stdout_path = args.output / f"{name}.stdout"
                stderr_path = args.output / f"{name}.stderr"
                with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=stdout,
                        stderr=stderr,
                        text=True,
                        encoding="utf-8",
                        env=env,
                        cwd=root,
                        start_new_session=os.name != "nt",
                    )
                    try:
                        process.communicate(stdin, timeout=COMMAND_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           capture_output=True,
                                           timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
                        else:
                            process_group = process.pid
                            try:
                                os.killpg(process_group, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        try:
                            process.wait(timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
                        except subprocess.TimeoutExpired:
                            pass
                        if os.name != "nt":
                            # The parent may exit on SIGTERM while descendants
                            # remain in this start_new_session process group.
                            # Always issue SIGKILL to this captured group after
                            # the grace period, even when wait() already reaped
                            # the parent.
                            try:
                                os.killpg(process_group, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            try:
                                process.wait(timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
                            except subprocess.TimeoutExpired:
                                pass
                        raise RuntimeError(
                            f"{name} exceeded {COMMAND_TIMEOUT_SECONDS}s and its process tree was terminated"
                        )
                result = subprocess.CompletedProcess(command, process.returncode,
                            stdout_path.read_text(encoding="utf-8", errors="replace"),
                            stderr_path.read_text(encoding="utf-8", errors="replace"))
                if result.returncode:
                    raise RuntimeError(f"{name} exited {result.returncode}: {result.stderr[-2000:]}")
                return result

            switch = str(args.switch.resolve())
            run("add", [switch, "provider", "add", "audit", "--base-url",
                        f"http://127.0.0.1:{server.server_port}/v1", "--fetch-models",
                        "--allow-insecure-http", "--metadata-fallback", "none",
                        "--set", 'model_providers.audit.http_headers.X-Audit-Key="audit-header-secret"',
                        "--set", "model_providers.audit.request_max_retries=0",
                        "--set", "model_providers.audit.stream_max_retries=0",
                        "--api-key-stdin"], "audit-fake-key\n")
            run("probe", [switch, "--json", "provider", "probe", "audit"])
            before_diagnose = len(fixture.requests)
            diagnostic = json.loads(run("diagnose", [switch, "--json", "provider", "diagnose", "audit",
                "--child-model", "audit-child-model" if args.child_model else "audit-model"]).stdout)
            diagnostic_offline = len(fixture.requests) == before_diagnose and not diagnostic["network_checked"]
            result = run("launch", [switch, "launch", "audit", "--", "exec",
                         "--skip-git-repo-check", "--json", "-s", "read-only",
                         LOCAL_AUDIT_PROMPT])
            thread_id = extract_thread_id(result.stdout) if args.resume else None
            cleanup_result = None
            resume_result = None
            resumed_thread_id = None
            resume_target_id = None
            if args.resume:
                cleanup_result = run("cleanup", [
                    switch,
                    "launch",
                    "audit",
                    "--",
                    "exec",
                    "--skip-git-repo-check",
                    "--json",
                    "-s",
                    "read-only",
                    CLEANUP_PROMPT,
                ])
                resume_command = [
                    switch,
                    "launch",
                    "audit",
                    "--",
                    "exec",
                    "resume",
                    "--skip-git-repo-check",
                    "--json",
                    thread_id,
                    RESUME_PROMPT,
                ]
                resume_target_id = resume_command[8]
                resume_result = run("resume", resume_command)
                resumed_thread_id = extract_thread_id(resume_result.stdout)
            (args.output / "requests.json").write_text(json.dumps(fixture.requests, indent=2), encoding="utf-8")
            model_requests = [r for r in fixture.requests if "input" in r["body"]]
            check_request_budget(len(model_requests))
            v2_evidence = fixture.lifecycle_evidence() if args.v2_lifecycle else []
            v2_status = validate_v2_lifecycle(v2_evidence) if args.v2_lifecycle else {}
            version_result = run("version", [str(args.codex), "--version"])
            summary = {
                "codex_version": version_result.stdout.strip(),
                "catalog": args.catalog,
                "responses_lite_seen": any(item.get("type") == "additional_tools"
                    for r in model_requests for item in r["body"].get("input", [])),
                "requests": len(model_requests),
                "models": sorted({r["body"].get("model", "") for r in model_requests}),
                "tools": sorted({(ns + "." if ns else "") + spec["name"]
                                 for r in model_requests for ns, spec in tool_specs(r["body"])}),
                "child_requests": sum(bool(r["headers"].get("x-openai-subagent")) for r in model_requests),
                "child_result_returned": any(
                    item.get("type") in ("function_call_output", "agent_message")
                    and any(marker in json.dumps(item) for marker in (
                        "AUDIT_CHILD_OK", "AUDIT_V2_CHILD_READY",
                    ))
                    for r in model_requests if not r["headers"].get("x-openai-subagent")
                    for item in r["body"].get("input", [])),
                "mcp_roundtrip": any("AUDIT_MCP_OK" in json.dumps(r["body"].get("input", [])) for r in model_requests),
                "parent_completed": any(marker in result.stdout for marker in (
                    "AUDIT_PARENT_OK", "AUDIT_V2_PARENT_OK",
                )),
                "base_config_unchanged": (codex_home / "config.toml").read_text(encoding="utf-8") == config,
                "auth_untouched": not (codex_home / "auth.json").exists(),
                "all_requests_authenticated": all(r["headers"].get("authorization") == "Bearer audit-fake-key" for r in model_requests),
                "all_requests_have_private_header": all(r["headers"].get("x-audit-key") == "audit-header-secret" for r in model_requests),
                "diagnostic_offline": diagnostic_offline,
                "catalog_provenance_correct": diagnostic["catalog"]["source"] == ("native_gateway" if args.catalog == "native" else "generic_gateway"),
                "child_model_routed": any(r["headers"].get("x-openai-subagent")
                    and r["body"].get("model") == ("audit-child-model" if args.child_model else "audit-model")
                    for r in model_requests),
                "request_budget_ok": (
                    not fixture.request_budget_exceeded and len(model_requests) <= REQUEST_LIMIT
                ),
                "resume_requested": args.resume,
                "cleanup_completed": bool(
                    cleanup_result and "AUDIT_CLEANUP_OK" in cleanup_result.stdout
                ),
                "resume_completed": bool(
                    resume_result
                    and resumed_thread_id
                    and ("AUDIT_PARENT_OK" in resume_result.stdout
                         or "AUDIT_V2_PARENT_OK" in resume_result.stdout)
                ),
                "resume_requested_id": thread_id,
                "resume_thread_id": resumed_thread_id,
                "resume_matches_original": bool(
                    thread_id
                    and resume_target_id == thread_id
                    and resumed_thread_id == thread_id
                ),
            }
            if args.v2_lifecycle:
                summary["child_result_returned"] = fixture.child_parent_received
                summary["v2_lifecycle"] = v2_status
                summary["v2_spawn_agent"] = v2_status["spawn_agent"]
                summary["v2_send_message"] = v2_status["send_message"]
                summary["v2_followup_task"] = v2_status["followup_task"]
                summary["v2_interrupt_agent"] = v2_status["interrupt_agent"]
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
            required = ("mcp_roundtrip", "child_result_returned", "parent_completed",
                        "base_config_unchanged", "auth_untouched", "all_requests_authenticated",
                        "child_model_routed", "all_requests_have_private_header", "diagnostic_offline",
                        "catalog_provenance_correct", "request_budget_ok")
            if not all(summary[key] for key in required):
                raise RuntimeError("Capability smoke did not satisfy every round-trip assertion")
            if args.catalog == "native" and not summary["responses_lite_seen"]:
                raise RuntimeError("Native fixture did not exercise Responses Lite")
            if args.v2_lifecycle and not all(v2_status.values()):
                raise RuntimeError(f"V2 lifecycle evidence incomplete: {v2_status}")
            if args.resume and not summary["resume_completed"]:
                raise RuntimeError("Exact session resume did not return a successful parent result")
            if args.resume:
                if not summary["cleanup_completed"]:
                    raise RuntimeError("Independent cleanup launch did not return its marker")
                if not summary["resume_matches_original"]:
                    raise RuntimeError("Resume command did not return the exact original thread id")
    finally:
        fixture.child_release.set()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--mcp"]:
        mcp_server()
    else:
        main()
