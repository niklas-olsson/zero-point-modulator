"""Producer-owned refusal facts never grant capacity or replace recovery keys."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zpm.controller import Controller
from zpm.models import UNSUPPORTED_AUTHORITY_REASON, capability_observation
from zpm.provider import ProviderError
from zpm.store import Store

from .support import FixtureProvider


FIXTURES = json.loads((Path(__file__).parent / "fixtures/capability-diagnostic-v1.json").read_text())
DIAGNOSTIC = FIXTURES["diagnostic"]
SCOPE = {
    "deployment_id": "diagnostic-deployment",
    "repository": "github:github.com/owner/project",
    "profile": "fixture",
}


def capabilities(*, typed=True, ready=False):
    value = FixtureProvider().capabilities(**SCOPE)
    value.update(repository_access=ready, managed_dispatch=ready, reason="untrusted-private-detail")
    if typed:
        value["diagnostic"] = deepcopy(DIAGNOSTIC)
    return value


class DiagnosticContractTests(unittest.TestCase):
    def test_shared_producer_vectors(self):
        expected = {
            "unsupported": (False, UNSUPPORTED_AUTHORITY_REASON, DIAGNOSTIC),
            "supported": (True, "ready", None),
            "unavailable": (False, "repository_access unavailable", None),
        }
        for name, response in FIXTURES["responses"].items():
            scope = {key: response[key] for key in ("deployment_id", "repository", "profile")}
            self.assertEqual(capability_observation(response, scope), expected[name])

    def test_mixed_versions_and_human_wording(self):
        for reason in (UNSUPPORTED_AUTHORITY_REASON, "different wording", "private-host/path", None, 7):
            value = capabilities() | {"reason": reason}
            self.assertEqual(capability_observation(value, SCOPE),
                             (False, UNSUPPORTED_AUTHORITY_REASON, DIAGNOSTIC))
        self.assertEqual(capability_observation(capabilities(typed=False, ready=True), SCOPE),
                         (True, "ready", None))
        for reason in (UNSUPPORTED_AUTHORITY_REASON, "unknown failure"):
            self.assertEqual(capability_observation(capabilities(typed=False) | {"reason": reason}, SCOPE),
                             (False, "repository_access unavailable", None))

    def test_present_invalid_diagnostic_blocks_positive_and_negative_responses(self):
        invalid = [
            None, False, "untrusted-private-detail", [], {},
            DIAGNOSTIC | {"schema_version": True},
            DIAGNOSTIC | {"schema_version": 1.0},
            DIAGNOSTIC | {"schema_version": 2},
            DIAGNOSTIC | {"producer": "other"},
            DIAGNOSTIC | {"dimension": "execution"},
            DIAGNOSTIC | {"code": "unknown"},
            DIAGNOSTIC | {"requirement": {"name": "child_initial_outcome", "version": True}},
            DIAGNOSTIC | {"requirement": {"name": "child_initial_outcome", "version": 1.0}},
            DIAGNOSTIC | {"requirement": {"name": "child_initial_outcome", "version": 2}},
            DIAGNOSTIC | {"requirement": {"name": "untrusted-private-detail", "version": 1}},
            DIAGNOSTIC | {"requirement": {"name": "child_initial_outcome", "version": 1, "extra": 1}},
            DIAGNOSTIC | {"extra": "untrusted-private-detail"},
            DIAGNOSTIC | {"code": "x" * 1025},
            DIAGNOSTIC | {"code": "\ud800"},
        ]
        for ready in (False, True):
            for diagnostic in invalid:
                with self.subTest(ready=ready, diagnostic=diagnostic):
                    result = capability_observation(capabilities(ready=ready) | {"diagnostic": diagnostic}, SCOPE)
                    self.assertEqual(result, (False, "unsupported provider capability diagnostic", None))
                    self.assertNotIn("untrusted-private-detail", json.dumps(result))

    def test_outer_contract_and_scope_precede_classification(self):
        mutations = [
            {"schema_version": True}, {"schema_version": 2},
            {"operations": ["ensure", "discover", "observe", "drain"]},
            {"operations": ["ensure", "discover", "observe", "drain", "stop", None]},
            {"operations": "ensure discover observe drain stop"},
            {"deployment_id": "foreign"}, {"repository": "github:github.com/foreign/repo"},
            {"profile": "foreign"}, {"deployment_id": None},
        ]
        for changes in mutations:
            with self.subTest(changes=changes):
                ready, reason, diagnostic = capability_observation(capabilities() | changes, SCOPE)
                self.assertFalse(ready)
                self.assertIsNone(diagnostic)
                self.assertNotEqual(reason, UNSUPPORTED_AUTHORITY_REASON)
        for field, values in (
            ("profile_supported", (False, 1, None)),
            ("repository_access", (True, 0, None)),
            ("managed_dispatch", (True, 0, None)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertEqual(capability_observation(capabilities() | {field: value}, SCOPE),
                                     (False, "provider capability diagnostic contradicts capability flags", None))

    def test_retained_diagnostic_is_a_separate_bounded_projection(self):
        value = capabilities()
        _, _, diagnostic = capability_observation(value, SCOPE)
        value["diagnostic"]["requirement"]["name"] = "untrusted-private-detail"
        self.assertEqual(diagnostic, DIAGNOSTIC)


class DiagnosticRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.store = Store(self.path, SCOPE["deployment_id"])
        self.store.enroll({"schema_version": 1, "repository": SCOPE["repository"],
                           "profile": SCOPE["profile"], "desired_workers": 1})
        self.provider = FixtureProvider()
        self.controller = Controller(self.store, self.provider, 2)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def reopen(self):
        self.store.close()
        self.store = Store(self.path, SCOPE["deployment_id"])
        self.controller = Controller(self.store, self.provider, 2)

    def test_observation_survives_reopen_and_is_cleared_by_current_evidence(self):
        for replacement in (capabilities(typed=False), capabilities(ready=True, typed=False),
                            capabilities() | {"diagnostic": None}):
            self.provider.capabilities = lambda *_: capabilities()
            validation = self.controller.check()["repositories"][0]["validation"]
            self.assertEqual(validation, {"ready": False, "reason": UNSUPPORTED_AUTHORITY_REASON,
                                          "diagnostic": DIAGNOSTIC})
            self.reopen()
            self.assertEqual(self.controller.plan()["repositories"][0]["validation"], validation)
            self.provider.capabilities = lambda *_: replacement
            self.assertNotIn("diagnostic", self.controller.check()["repositories"][0]["validation"])
            self.assertEqual(self.store.workers(), [])

    def test_transport_error_cannot_retain_an_old_classification(self):
        self.provider.capabilities = lambda *_: capabilities()
        self.controller.check()

        def unauthorized(*_):
            raise ProviderError("OpenJules returned HTTP 403")

        self.provider.capabilities = unauthorized
        self.assertEqual(self.controller.check()["repositories"][0]["validation"],
                         {"ready": False, "reason": "OpenJules returned HTTP 403"})

    def test_refusal_preserves_uncertain_launch_and_original_drain_stop_keys(self):
        self.provider.lose_launch_reply = True
        first = self.controller.tick()
        self.assertEqual(first["reserved"], 1)
        original = self.store.workers()[0]
        self.assertEqual(original["pending_action"], "ensure")
        key = original["launch_key"]
        self.provider.capabilities = lambda *_: capabilities()
        self.reopen()
        blocked = self.controller.tick()
        self.assertEqual(blocked["reserved"], 1)
        self.assertEqual(self.store.workers()[0]["launch_key"], key)
        self.assertEqual(len(self.provider.records), 1)
        self.assertEqual(len([call for call in self.provider.calls if call[0] == "ensure"]), 1)

        self.store.change(SCOPE["repository"], "target", 0)
        self.provider.lose_drain_reply = True
        self.controller.tick()
        pending = self.store.worker(key)
        self.assertEqual(pending["pending_action"], "drain")
        drain_key = pending["action_key"]
        self.reopen()
        self.assertEqual(self.controller.tick()["reserved"], 0)
        self.assertTrue(all(call[-1] == drain_key for call in self.provider.calls if call[0] == "drain"))
        self.assertEqual(self.store.workers()[0]["launch_key"], key)
        self.assertEqual(len(self.provider.records), 1)
        self.assertEqual(len([call for call in self.provider.calls if call[0] == "ensure"]), 1)


if __name__ == "__main__":
    unittest.main()
