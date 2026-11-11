"""Bounded child-capture diagnostics; these fixtures do not qualify Docker."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    from check_children_stack import child_capture_observation, configured_verification_output_observation
finally:
    sys.path.pop(0)


MISSING_OUTPUT = {
    "available": False, "output_kind": "missing", "output_bytes": None,
    "scanned_bytes": 0, "scan_limited": False,
    "exception_classes": [], "fixed_script_lines": [], "details_limited": False,
}


class ChildCaptureDiagnosticTests(unittest.TestCase):
    def test_missing_marker_and_missing_event_remain_distinguishable(self):
        marker = "CHILD_STACK_BOUNDARY_VERIFIED uid=60000"
        event = json.dumps({
            "event": "configured_verification", "state": "exited", "exit_code": 0,
        })
        for content, marker_present, event_present, count in (
            (marker, True, False, 0),
            (event, False, True, 1),
            (marker + "\n" + event, True, True, 1),
            ("CHILD_STACK_BOUNDARY_VERIFIED uid=60001\n" + event, False, True, 1),
        ):
            with self.subTest(marker=marker_present, event=event_present):
                result = child_capture_observation("left", 60000, content)
                self.assertIs(result["expected_uid_marker_present"], marker_present)
                self.assertIs(result["configured_verification_literal_present"], event_present)
                self.assertEqual(result["configured_verification_events_observed"], count)
                self.assertEqual(result["log_sha256"], hashlib.sha256(content.encode()).hexdigest())
                self.assertEqual(result["log_bytes"], len(content.encode()))
                self.assertFalse(result["scan_limited"])
                if count:
                    self.assertEqual(result["configured_verification_details"], [{
                        "state": "exited", "exit_code_kind": "integer", "exit_code": 0,
                        "output_observation": MISSING_OUTPUT,
                    }])
        compact = json.dumps({
            "event": "configured_verification", "state": "exited", "exit_code": 3,
        }, separators=(",", ":"))
        result = child_capture_observation("right", 60001, compact)
        self.assertFalse(result["configured_verification_literal_present"])
        self.assertEqual(result["configured_verification_events_observed"], 1)
        self.assertEqual(result["configured_verification_details"][0]["exit_code"], 3)

    def test_guest_fields_never_become_exported_text_or_implicit_success(self):
        secret = "private provider credential and child summary"
        for state, code in (
            (secret, True), ({"private": secret}, secret),
            (["exited"], 0.0), ("exited", 100000), (None, None),
        ):
            with self.subTest(state=type(state).__name__, code=type(code).__name__):
                content = json.dumps({
                    "event": "configured_verification", "state": state, "exit_code": code,
                    "output": secret, "summary": secret, secret: secret,
                }) + "\n" + json.dumps({"event": secret, "output": secret})
                result = child_capture_observation("left", 60000, content)
                serialized = json.dumps(result)
                self.assertNotIn(secret, serialized)
                self.assertNotIn('"output"', serialized)
                self.assertNotIn('"summary"', serialized)
                detail = result["configured_verification_details"][0]
                self.assertEqual(set(detail), {"state", "exit_code_kind", "exit_code", "output_observation"})
                self.assertEqual(detail["state"], "exited" if state == "exited" else "unrecognized")
                self.assertEqual(detail["exit_code_kind"], "null" if code is None else "unrecognized")
                self.assertIsNone(detail["exit_code"])
        for state, code in (("exited", -9), ("timed_out", -9), ("output_limit", 255), ("unknown", 1)):
            result = child_capture_observation("left", 60000, json.dumps({
                "event": "configured_verification", "state": state, "exit_code": code,
            }))
            self.assertEqual(result["configured_verification_details"], [{
                "state": state, "exit_code_kind": "integer", "exit_code": code,
                "output_observation": MISSING_OUTPUT,
            }])
        result = child_capture_observation("left", 60000, '{"event": "configured_verification"}')
        self.assertEqual(result["configured_verification_details"], [{
            "state": "unrecognized", "exit_code_kind": "missing", "exit_code": None,
            "output_observation": MISSING_OUTPUT,
        }])

    def test_utf8_input_line_and_detail_bounds_are_explicit(self):
        event = json.dumps({
            "event": "configured_verification", "state": "exited", "exit_code": 3,
        })
        content = (event + "\n") * 10 + "漢" * 30000 + "\nCHILD_STACK_BOUNDARY_VERIFIED uid=60000"
        result = child_capture_observation("left", 60000, content)
        self.assertEqual(result["log_bytes"], len(content.encode("utf-8")))
        self.assertEqual(result["log_sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())
        self.assertEqual(result["scanned_bytes"], 65536)
        self.assertTrue(result["scan_limited"])
        self.assertTrue(result["expected_uid_marker_present"])
        self.assertEqual(result["configured_verification_events_observed"], 10)
        self.assertEqual(len(result["configured_verification_details"]), 4)
        self.assertTrue(result["verification_details_limited"])
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 4096)
        content = "{}\n" * 256 + event
        result = child_capture_observation("left", 60000, content)
        self.assertTrue(result["scan_limited"])
        self.assertTrue(result["configured_verification_literal_present"])
        self.assertEqual(result["configured_verification_events_observed"], 0)
        self.assertEqual(result["configured_verification_details"], [])

    def test_malformed_json_and_foreign_identity_cannot_hide_or_export_text(self):
        content = "\n".join([
            "private non-JSON output", "[", "[" * 2000 + "]" * 2000,
            json.dumps({"event": "configured_verification", "state": "exited", "exit_code": 1}),
            json.dumps(["configured_verification", "private"]),
        ])
        result = child_capture_observation("right", 60001, content)
        self.assertEqual(result["configured_verification_events_observed"], 1)
        self.assertEqual(result["configured_verification_details"], [{
            "state": "exited", "exit_code_kind": "integer", "exit_code": 1,
            "output_observation": MISSING_OUTPUT,
        }])
        self.assertNotIn("private", json.dumps(result))
        for actor, uid in (("private actor", 60000), ("left", 60001), ("right", True)):
            with self.subTest(uid=uid):
                with self.assertRaisesRegex(AssertionError, "fixed trial actor"):
                    child_capture_observation(actor, uid, content)


    def test_output_projection_keeps_only_fixed_exception_classes_and_script_frames(self):
        secret = "private credentials paths summaries and tools"
        output = "\n".join([
            "Traceback (most recent call last):",
            '  File "<string>", line 21, in <module>',
            '  File "<string>", line 13, in deny_socket',
            '  File "/private/' + secret + '", line 4, in private_function',
            '  File "<string>", line 999, in <module>',
            '  File "<string>", line 5, in private_function',
            "TimeoutError: " + secret,
            "PrivateException: " + secret,
        ])
        result = child_capture_observation("left", 60000, json.dumps({
            "event": "configured_verification", "state": "exited", "exit_code": 1,
            "output": output,
        }))
        observed = result["configured_verification_details"][0]["output_observation"]
        self.assertEqual(observed, {
            "available": True, "output_kind": "text", "output_bytes": len(output.encode()),
            "scanned_bytes": len(output.encode()), "scan_limited": False,
            "exception_classes": ["TimeoutError"], "fixed_script_lines": [21, 13],
            "details_limited": False,
        })
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("PrivateException", json.dumps(result))
        self.assertNotIn("private_function", json.dumps(result))
        self.assertEqual(configured_verification_output_observation(True), MISSING_OUTPUT | {"output_kind": "nontext"})
        self.assertEqual(configured_verification_output_observation({"output": secret}),
                         MISSING_OUTPUT | {"output_kind": "nontext"})
        self.assertEqual(configured_verification_output_observation(None, present=False), MISSING_OUTPUT)
        self.assertEqual(configured_verification_output_observation(""), MISSING_OUTPUT | {
            "available": True, "output_kind": "text", "output_bytes": 0,
        })
        output = "\n".join([
            "AssertionError: " + secret, "TimeoutError: " + secret,
            "ConnectionRefusedError: " + secret,
        ] + ['  File "<string>", line 13, in deny_socket'] * 5)
        observed = configured_verification_output_observation(output)
        self.assertEqual(observed["exception_classes"], ["AssertionError", "TimeoutError"])
        self.assertEqual(observed["fixed_script_lines"], [13] * 4)
        self.assertTrue(observed["details_limited"])
        observed = configured_verification_output_observation("漢" * 3000 + "\nTimeoutError: " + secret)
        self.assertTrue(observed["scan_limited"])
        self.assertEqual(observed["scanned_bytes"], 8192)
        self.assertEqual(observed["exception_classes"], [])
        observed = configured_verification_output_observation("\n" * 128 + "AssertionError")
        self.assertTrue(observed["scan_limited"])
        self.assertEqual(observed["exception_classes"], [])



if __name__ == "__main__":
    unittest.main()
