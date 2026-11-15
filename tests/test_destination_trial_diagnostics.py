"""Closed diagnostic fixtures only; no installed service, Docker or network run."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / "contracts"))
try:
    import check_destination_stack as trial
finally:
    sys.path.pop(0)


PHASE = "canonical readiness and lost original ensure reply"
PRIVATE = "PRIVATE_TOKEN_https://private.example/secret?credential=private"


class Unformattable:
    def __str__(self):
        raise AssertionError("diagnostic formatted private data")

    def __repr__(self):
        raise AssertionError("diagnostic represented private data")


class PrivateString(str):
    def __hash__(self):
        raise AssertionError("diagnostic hashed a string subclass")

    def __eq__(self, other):
        raise AssertionError("diagnostic compared a string subclass")

    def __len__(self):
        raise AssertionError("diagnostic measured a string subclass")


class DestinationDiagnosticTests(unittest.TestCase):
    def diagnostic(self, error, *, checkpoint="INITIAL_ENSURE", phase=PHASE):
        return trial.DestinationTrialFailure(
            phase, ["fixed completed check"], error, subcheckpoint=checkpoint,
        ).destination_diagnostic

    def test_existing_wait_timeout_is_classified_without_exporting_its_message(self):
        now = [0.0]

        def advance(seconds):
            now[0] += seconds

        processes = Mock()
        with patch.object(trial.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(trial.time, "sleep", side_effect=advance):
            with self.assertRaises(AssertionError) as raised:
                trial.eventually(lambda: False, processes, "both destination readiness checks", 0.5)
        original = raised.exception
        result = self.diagnostic(original, checkpoint="READINESS")
        self.assertEqual((result["error_type"], result["failure_code"], result["subcheckpoint"]),
                         ("AssertionError", "READINESS_TIMEOUT", "READINESS"))
        self.assertNotIn(str(original), json.dumps(result))

    def test_actual_assertion_messages_distinguish_uncertain_reservation_and_recovery(self):
        for message, checkpoint, expected in (
            ("lost reply did not retain the original uncertain reservation", "INITIAL_ENSURE",
             "UNCERTAIN_RESERVATION_MISMATCH"),
            ("restart recreated or rebound original capacity", "ORIGINAL_RECOVERY", "RECOVERY_STATE_MISMATCH"),
            ("a trial service exited; inspect retained service logs", "READINESS", "SERVICE_EXITED"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(AssertionError) as raised:
                    trial.require(False, message)
                result = self.diagnostic(raised.exception, checkpoint=checkpoint)
                self.assertEqual(result["failure_code"], expected)
                self.assertEqual(result["subcheckpoint"], checkpoint)
                self.assertNotIn(message, json.dumps(result))

    def test_private_exception_fields_and_class_names_never_enter_the_projection(self):
        private_class = type("PRIVATE_EXCEPTION_CLASS", (Exception,), {
            "__str__": Unformattable.__str__, "__repr__": Unformattable.__repr__,
        })
        for error, expected in (
            (AssertionError(PRIVATE), ("AssertionError", "ASSERTION")),
            (OSError(2, PRIVATE, PRIVATE), ("OSError", "OS_ERROR")),
            (ValueError(PRIVATE), ("ValueError", "VALUE_ERROR")),
            (KeyError(PRIVATE), ("LookupError", "LOOKUP_ERROR")),
            (subprocess.TimeoutExpired([PRIVATE], 3, output=PRIVATE, stderr=PRIVATE),
             ("TimeoutExpired", "SUBPROCESS_TIMEOUT")),
            (subprocess.CalledProcessError(1, [PRIVATE], output=PRIVATE, stderr=PRIVATE),
             ("SubprocessError", "SUBPROCESS_ERROR")),
            (private_class(Unformattable()), ("Exception", "EXCEPTION")),
            (KeyboardInterrupt(PRIVATE), ("KeyboardInterrupt", "INTERRUPTED")),
            (SystemExit(PRIVATE), ("SystemExit", "SYSTEM_EXIT")),
            (GeneratorExit(PRIVATE), ("BaseException", "CONTROL_EXIT")),
        ):
            with self.subTest(expected=expected):
                result = self.diagnostic(error)
                self.assertEqual((result["error_type"], result["failure_code"]), expected)
                self.assertNotIn("PRIVATE", json.dumps(result))
                self.assertLess(len(json.dumps(result)), 512)

    def test_only_exact_builtin_single_string_assertions_select_specific_codes(self):
        message = "lost reply did not retain the original uncertain reservation"

        class PrivateAssertion(AssertionError):
            def __getattribute__(self, name):
                if name == "args":
                    raise AssertionError("diagnostic accessed subclass args")
                return super().__getattribute__(name)

        for error in (AssertionError(), AssertionError(message, PRIVATE),
                      AssertionError(PrivateString(message)), AssertionError(Unformattable()),
                      AssertionError(PRIVATE * 10000), PrivateAssertion(message)):
            result = self.diagnostic(error)
            self.assertEqual(result["failure_code"], "ASSERTION")
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_unknown_checkpoint_values_are_closed_without_formatting(self):
        for value in (PRIVATE, None, [PRIVATE], {PRIVATE: PRIVATE},
                      Unformattable(), PrivateString("INITIAL_ENSURE")):
            result = self.diagnostic(AssertionError(PRIVATE), checkpoint=value)
            self.assertEqual(result["subcheckpoint"], "UNAVAILABLE")
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_later_phase_never_exports_a_stale_recovery_checkpoint(self):
        for phase in ("exact permanent refusal at the final native admission boundary",
                      "actual canonical task execution and retained captured result", "owned cleanup"):
            result = self.diagnostic(RuntimeError(PRIVATE), checkpoint="ORIGINAL_RECOVERY", phase=phase)
            self.assertEqual(result["phase"], phase)
            self.assertEqual(result["subcheckpoint"], "NOT_APPLICABLE")

    def test_classification_keeps_the_original_exception_and_cause_untouched(self):
        cause = ValueError(PRIVATE)
        original = AssertionError("lost reply did not retain the original uncertain reservation")
        original.__cause__ = cause
        original.add_note(PRIVATE)
        before = (original.args, original.__cause__, original.__traceback__, list(original.__notes__))
        result = self.diagnostic(original)
        self.assertEqual(result["failure_code"], "UNCERTAIN_RESERVATION_MISMATCH")
        self.assertEqual((original.args, original.__cause__, original.__traceback__, original.__notes__), before)
        self.assertIs(original.__cause__, cause)
        self.assertNotIn("PRIVATE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
