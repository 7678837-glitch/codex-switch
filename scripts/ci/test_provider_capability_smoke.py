#!/usr/bin/env python3
"""Contract tests for the local provider capability smoke fixture.

These tests intentionally exercise the small, deterministic evidence helpers
used by ``provider-capability-smoke.py``.  They do not start Codex or contact
an external model service.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("provider-capability-smoke.py")
SPEC = importlib.util.spec_from_file_location("provider_capability_smoke", SCRIPT)
assert SPEC and SPEC.loader
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


THREAD_ID = "01a11961-8f5f-70c3-b3d2-1004c5b6d203"
CHILD_TASK = "/root/audit_child"


def valid_v2_evidence() -> list[dict[str, object]]:
    """Return evidence for one complete V2 lifecycle.

    The child-result markers model actual child turns.  A tool-call item or a
    function name by itself is deliberately insufficient evidence.
    """

    return [
        {
            "operation": "spawn_agent",
            "result": {"task_name": CHILD_TASK},
            "child": {
                "observed": True,
                "status": "completed",
                "text": "AUDIT_V2_CHILD_READY",
                "parent_received": True,
            },
        },
        {
            "operation": "send_message",
            "result": {"observed": True, "text": ""},
            "child": {
                "observed": True,
                "active": True,
                "http_opened": True,
                "input_markers": ["AUDIT_V2_MESSAGE", "AUDIT_V2_FOLLOWUP"],
            },
        },
        {
            "operation": "followup_task",
            "result": {"observed": True, "text": ""},
            "child": {
                "observed": True,
                "active": True,
                "http_opened": True,
                "input_markers": ["AUDIT_V2_MESSAGE", "AUDIT_V2_FOLLOWUP"],
            },
        },
        {
            "operation": "interrupt_agent",
            "result": {"previous_status": "running"},
            "child": {
                "observed": True,
                "active": True,
                "http_opened": True,
                "input_markers": ["AUDIT_V2_MESSAGE", "AUDIT_V2_FOLLOWUP"],
            },
        },
    ]


class ProviderCapabilitySmokeContracts(unittest.TestCase):
    def test_v2_lifecycle_reports_each_operation_after_successful_child_evidence(self) -> None:
        result = SMOKE.validate_v2_lifecycle(valid_v2_evidence())

        self.assertEqual(
            result,
            {
                "spawn_agent": True,
                "send_message": True,
                "followup_task": True,
                "interrupt_agent": True,
            },
        )

    def test_v2_lifecycle_rejects_completed_interrupt_after_valid_prior_operations(self) -> None:
        evidence = valid_v2_evidence()
        evidence[3]["result"] = {"previous_status": "completed"}

        result = SMOKE.validate_v2_lifecycle(evidence)

        self.assertTrue(result["spawn_agent"])
        self.assertTrue(result["send_message"])
        self.assertTrue(result["followup_task"])
        self.assertFalse(result["interrupt_agent"])

    def test_v2_lifecycle_rejects_pseudo_success_and_non_active_interrupt(self) -> None:
        evidence = valid_v2_evidence()
        evidence[1]["child"] = {
            "observed": True,
            "active": False,
            "http_opened": False,
            "input_markers": [],
        }
        evidence[2].pop("child")
        evidence[3]["result"] = {"previous_status": "completed"}

        result = SMOKE.validate_v2_lifecycle(evidence)

        self.assertFalse(result["send_message"])
        self.assertFalse(result["followup_task"])
        self.assertFalse(result["interrupt_agent"])

    def test_v2_lifecycle_rejects_error_function_output_even_with_child_marker(self) -> None:
        evidence = valid_v2_evidence()
        evidence[1]["result"] = {"observed": True, "text": "target missing", "error": True}
        evidence[2]["result"] = {"observed": True, "text": "child unavailable", "error": True}

        result = SMOKE.validate_v2_lifecycle(evidence)

        self.assertFalse(result["send_message"])
        self.assertFalse(result["followup_task"])

    def test_v2_lifecycle_requires_child_input_markers_for_message_and_followup(self) -> None:
        evidence = valid_v2_evidence()
        evidence[1]["child"]["input_markers"] = []
        evidence[2]["child"]["input_markers"] = []

        result = SMOKE.validate_v2_lifecycle(evidence)

        self.assertFalse(result["send_message"])
        self.assertFalse(result["followup_task"])

    def test_v2_lifecycle_requires_parent_observed_spawn_final(self) -> None:
        evidence = valid_v2_evidence()
        evidence[0]["child"]["parent_received"] = False

        result = SMOKE.validate_v2_lifecycle(evidence)

        self.assertFalse(result["spawn_agent"])

    def test_v2_lifecycle_requires_ordered_state_progression(self) -> None:
        evidence = valid_v2_evidence()
        evidence[0], evidence[3] = evidence[3], evidence[0]

        result = SMOKE.validate_v2_lifecycle(evidence)

        self.assertFalse(result["interrupt_agent"])
        self.assertFalse(result["spawn_agent"])

    def test_extract_thread_id_uses_only_exact_thread_started_record(self) -> None:
        stdout = "\n".join(
            [
                "Launching Codex with provider 'audit' (audit-model)...",
                '{"type":"thread.started","thread_id":"%s"}' % THREAD_ID,
                '{"type":"turn.started"}',
            ]
        )

        self.assertEqual(SMOKE.extract_thread_id(stdout), THREAD_ID)

    def test_extract_thread_id_rejects_missing_or_ambiguous_records(self) -> None:
        with self.assertRaises(ValueError):
            SMOKE.extract_thread_id('{"type":"message","thread_id":"%s"}' % THREAD_ID)
        with self.assertRaises(ValueError):
            SMOKE.extract_thread_id(
                '\n'.join(
                    [
                        '{"type":"thread.started","thread_id":"%s"}' % THREAD_ID,
                        '{"type":"thread.started","thread_id":"01a11961-8fa6-7e62-b878-4f97679fe05b"}',
                    ]
                )
            )

    def test_request_budget_allows_limit_and_rejects_over_limit(self) -> None:
        SMOKE.check_request_budget(16, 16)

        with self.assertRaises(ValueError):
            SMOKE.check_request_budget(17, 16)


if __name__ == "__main__":
    unittest.main()
