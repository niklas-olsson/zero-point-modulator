from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from zpm import installation_admission as admission
from zpm.models import ConfigurationError


FIXTURES = Path(__file__).parent / "fixtures" / "installation-admission"
PRODUCER_VECTORS = json.loads((FIXTURES / "openjules-vectors.json").read_text())
VECTORS = {vector["name"]: vector for vector in PRODUCER_VECTORS["vectors"]}
RECEIPT_ID = "0323b08b-2ce1-4ff7-9d85-3068bd5ae1aa"
KP_FIXTURES = json.loads((FIXTURES / "polyphony-definition-vectors.json").read_text())


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(name="root"):
    vector = copy.deepcopy(VECTORS[name])
    result = vector["expected"]
    child = name in {"children", "initial_outcome", "managed_public"}
    kp = copy.deepcopy(KP_FIXTURES["child" if child else "root"])
    inputs = {
        "openjules": {
            "configuration": vector["configuration_utf8"].encode("utf-8"),
            "scope_declarations": (vector["scope_declarations_utf8"].encode("utf-8")
                                   if vector["scope_declarations_utf8"] is not None else None),
        },
        "keystone-polyphony": {"managed_policy": b'{"synthetic":"policy"}',
                               "credentials": b'{"synthetic-private-token":{"role":"runtime"}}'},
    }
    selection = {
        "schema_version": 1, "deployment_id": "fixture-installation",
        "installation_plan_digest": digest(b"explicit synthetic installation plan"),
        "components": {
            "openjules": {"repository": "niklas-olsson/OpenJules", "source_revision": "77e1191bc373c9a7bd999eaf33bc5a0b54d8323c",
                           "validation_contract": "openjules-installation-v1", "contract_digest": result["contract_digest"]},
            "keystone-polyphony": {"repository": "niklas-olsson/keystone-polyphony", "source_revision": "d5f0cd14c1940e1bd55db1dc5d4569a5d14fb359",
                                   "validation_contract": "managed-installation-v1", "contract_digest": kp["contract_digest"]},
        },
        "profiles": [{key: kp["descriptor"]["profiles"][0][key]
                      for key in ("repository", "profile", "base", "profile_digest", "capabilities")}],
    }
    raw = encode(selection)
    receipt = admission.prepare_receipt(raw, inputs, receipt_id=RECEIPT_ID)
    observations = {}
    for service, native in (("openjules", result), ("keystone-polyphony", kp)):
        component = receipt["components"][service]
        observations[service] = {key: copy.deepcopy(component[key]) for key in ("source_revision", "contract_digest", "input_digests")}
        observations[service]["result"] = native
    return raw, inputs, receipt, observations


def compare(data):
    selection, inputs, receipt, observations = data
    return admission.compare_fixtures(admission.encode(receipt), selection, inputs,
                                     {service: None if value is None else encode(value) for service, value in observations.items()})


class InstallationAdmissionTests(unittest.TestCase):
    def assert_unqualified(self, value):
        self.assertEqual(value["phase"], "offline-prepared")
        self.assertIsNone(value["compatible"])
        for field in ("source_identity_verified", "native_validation_performed", "live_capabilities_checked", "execution_qualified"):
            self.assertIs(value[field], False)

    def test_exact_published_openjules_contract_and_input_vectors(self):
        self.assertEqual(digest((FIXTURES / "openjules-installation-v1.json").read_bytes()), admission.OPENJULES_CONTRACT_DIGEST)
        self.assertEqual(PRODUCER_VECTORS["contract_digest"], admission.OPENJULES_CONTRACT_DIGEST)
        for name, vector in VECTORS.items():
            with self.subTest(vector=name):
                self.assertEqual(digest(vector["configuration_utf8"].encode("utf-8")), vector["expected"]["configuration_sha256"])

    def test_root_model_and_child_contract_examples_never_qualify_native_validation(self):
        for name in ("root", "model", "children", "initial_outcome"):
            with self.subTest(vector=name):
                data = fixture(name)
                before = copy.deepcopy(data)
                result = compare(data)
                self.assertIs(result["fixture_compatible"], True)
                self.assertEqual(result["evidence"], "contract-fixture")
                self.assert_unqualified(result)
                self.assert_unqualified(data[2])
                for component in data[2]["components"].values():
                    self.assertIsNone(component["valid"])
                    self.assertEqual(component["errors"], [])
                self.assertEqual(data, before)
                self.assertEqual(result, compare(data))

    def test_public_conversation_requires_separate_missing_peer_evidence(self):
        result = compare(fixture("managed_public"))
        self.assertIsNone(result["fixture_compatible"])
        self.assertEqual(result["code"], "required_peer_unvalidated")
        self.assert_unqualified(result)

    def test_native_invalid_receipt_has_no_partial_descriptor_and_is_not_compatibility(self):
        result = compare(fixture("boolean_port"))
        self.assertFalse(result["fixture_compatible"])
        self.assertEqual(result["components"]["openjules"]["status"], "invalid")
        self.assert_unqualified(result)
        data = fixture("boolean_port")
        data[3]["openjules"]["result"]["descriptor"] = VECTORS["root"]["expected"]["descriptor"]
        self.assertIsNone(compare(data)["fixture_compatible"])

    def test_missing_unknown_or_disabled_authority_is_explicit(self):
        for service in admission.SERVICES:
            data = fixture()
            data[3][service] = None
            result = compare(data)
            self.assertIsNone(result["fixture_compatible"])
            self.assertEqual(result["components"][service]["status"], "missing")
        data = fixture()
        native = data[3]["keystone-polyphony"]["result"]
        native.update(managed_enabled=False, descriptor=None)
        self.assertFalse(compare(data)["fixture_compatible"])
        self.assertEqual(compare(data)["components"]["keystone-polyphony"]["status"], "disabled")
        native["validation_contract"] = "unadopted-v2"
        self.assertIsNone(compare(data)["fixture_compatible"])

    def test_both_component_envelopes_bind_source_contract_and_each_private_input(self):
        for service in admission.SERVICES:
            for field in ("source_revision", "contract_digest", *admission.INPUTS[service]):
                data = fixture()
                envelope = data[3][service]
                if field in admission.INPUTS[service]:
                    envelope["input_digests"][field] = "9" * 64
                else:
                    envelope[field] = "9" * (40 if field == "source_revision" else 64)
                with self.subTest(service=service, field=field):
                    report = compare(data)
                    self.assertIsNone(report["fixture_compatible"])
                    self.assertEqual(report["components"][service]["status"], "unvalidated")
                    self.assert_unqualified(report)

    def test_changed_exact_selected_bytes_cannot_reuse_receipt(self):
        for service in admission.SERVICES:
            for field in admission.INPUTS[service]:
                data = fixture()
                old = data[1][service][field]
                data[1][service][field] = b"{}" if old is None else old + b" "
                with self.subTest(service=service, field=field), self.assertRaisesRegex(ConfigurationError, "does not match selected inputs"):
                    compare(data)
        data = fixture()
        with self.assertRaises(ConfigurationError):
            compare((data[0] + b"\n", *data[1:]))
        for field in ("source_revision", "contract_digest"):
            data = fixture()
            selected = json.loads(data[0])
            selected["components"]["keystone-polyphony"][field] = "a" * (40 if field == "source_revision" else 64)
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                compare((encode(selected), *data[1:]))

    def test_actual_openjules_output_hash_cannot_disagree_with_wrapper(self):
        for field in ("configuration_sha256", "scope_declarations_sha256"):
            data = fixture()
            data[3]["openjules"]["result"][field] = "a" * 64
            result = compare(data)
            self.assertIsNone(result["fixture_compatible"])
            self.assertEqual(result["components"]["openjules"]["code"], "input_mismatch")

    def test_composition_differences_are_refused_without_normalization(self):
        for service, field, new, expected in (
            ("openjules", "base_commit", "0" * 40, "base_mismatch"),
            ("keystone-polyphony", "base", "0" * 40, "base_mismatch"),
            ("keystone-polyphony", "profile_digest", "0" * 64, "profile_digest_mismatch"),
            ("keystone-polyphony", "capabilities", ["different"], "capabilities_mismatch"),
            ("openjules", "repository", "github:github.com/example/other", "scope_mismatch"),
            ("keystone-polyphony", "profile", "other", "scope_mismatch"),
        ):
            data = fixture()
            data[3][service]["result"]["descriptor"]["profiles"][0][field] = new
            with self.subTest(service=service, field=field):
                result = compare(data)
                self.assertFalse(result["fixture_compatible"])
                self.assertEqual(result["code"], expected)
        data = fixture()
        data[3]["openjules"]["result"]["descriptor"]["profiles"][0]["repository"] = "Example/Project"
        self.assertIsNone(compare(data)["fixture_compatible"])

    def test_additional_cross_deployment_profile_cannot_be_lost_in_projection(self):
        data = fixture()
        profiles = data[3]["openjules"]["result"]["descriptor"]["profiles"]
        extra = copy.deepcopy(profiles[0])
        extra["deployment_id"] = "aaa-other-installation"
        profiles.insert(0, extra)
        result = compare(data)
        self.assertFalse(result["fixture_compatible"])
        self.assertEqual(result["code"], "deployment_mismatch")

    def test_missing_required_child_capability_is_not_runtime_qualification(self):
        data = fixture("children")
        profile = data[3]["keystone-polyphony"]["result"]["descriptor"]["profiles"][0]
        profile.update(child_grants=False, child_policy=None)
        self.assertFalse(compare(data)["fixture_compatible"])
        self.assertEqual(compare(data)["code"], "required_capability_mismatch")

    def test_all_numeric_version_and_boolean_result_aliases_are_refused(self):
        mutations = [
            ("openjules", ("schema_version",), True),
            ("openjules", ("valid",), 1),
            ("openjules", ("descriptor", "execution_destination_identity"), False),
            ("openjules", ("descriptor", "profiles", 0, "features", "model_broker"), True),
            ("openjules", ("descriptor", "profiles", 0, "required_peer_contracts", 0, "required_capabilities", "managed_dispatch"), 1),
            ("keystone-polyphony", ("schema_version",), 1.0),
            ("keystone-polyphony", ("managed_enabled",), 1),
            ("keystone-polyphony", ("descriptor", "profiles", 0, "child_initial_outcome"), True),
            ("keystone-polyphony", ("descriptor", "profiles", 0, "max_attempts"), True),
            ("keystone-polyphony", ("descriptor", "profiles", 0, "attempt_seconds"), True),
        ]
        for service, path, replacement in mutations:
            data = fixture()
            current = data[3][service]["result"]
            for step in path[:-1]:
                current = current[step]
            current[path[-1]] = replacement
            with self.subTest(service=service, path=path):
                self.assertIsNone(compare(data)["fixture_compatible"])

    def test_closed_result_and_no_implicit_partial_success(self):
        for service in admission.SERVICES:
            for field in list(fixture()[3][service]["result"]):
                data = fixture()
                data[3][service]["result"].pop(field)
                with self.subTest(service=service, missing=field):
                    self.assertIsNone(compare(data)["fixture_compatible"])
            data = fixture()
            data[3][service]["result"]["native_validated"] = True
            self.assertIsNone(compare(data)["fixture_compatible"])

    def test_oversized_numeric_duration_is_unvalidated_without_float_overflow(self):
        for field in ("attempt_seconds", "budget_seconds"):
            for value in (10**400, 604801, -10**400):
                data = fixture()
                data[3]["keystone-polyphony"]["result"]["descriptor"]["profiles"][0][field] = value
                with self.subTest(field=field, value=value):
                    report = compare(data)
                    self.assertIsNone(report["fixture_compatible"])
                    self.assertEqual(report["components"]["keystone-polyphony"]["code"], "result_malformed")

    def test_prepared_receipt_size_is_bounded_after_adding_private_bindings(self):
        data = fixture()
        selected = json.loads(data[0])
        template = selected["profiles"][0]
        # Keep selected bytes under the bound while expanded receipt fields
        # push the encoded result past it. Never return an unusable receipt.
        selected["profiles"] = []
        for index in range(4):
            profile = copy.deepcopy(template)
            profile["profile"] = "fixture-" + str(index)
            profile["capabilities"] = [str(n).zfill(3) + "x" * 504 for n in range(63)]
            selected["profiles"].append(profile)
        raw = encode(selected)
        self.assertLess(len(raw), admission.MAX_SELECTION_BYTES)
        while len(raw) < admission.MAX_SELECTION_BYTES - 200:
            selected["profiles"][-1]["capabilities"].append("y" * min(512, admission.MAX_SELECTION_BYTES - len(raw) - 203))
            raw = encode(selected)
        self.assertLessEqual(len(raw), admission.MAX_SELECTION_BYTES)
        with self.assertRaises(ConfigurationError):
            admission.prepare_receipt(raw, data[1], receipt_id=RECEIPT_ID)

    def test_credential_diagnostics_never_disclose_map_keys(self):
        data = fixture()
        result = data[3]["keystone-polyphony"]["result"]
        result.update(valid=False, managed_enabled=None, descriptor=None,
                      errors=[{"code": "credential_invalid", "path": "/credentials"}])
        report = compare(data)
        self.assertFalse(report["fixture_compatible"])
        result["errors"][0]["path"] = "/credentials/private_token"
        report = compare(data)
        self.assertIsNone(report["fixture_compatible"])
        self.assertNotIn("private_token", admission.encode(report).decode())
        self.assertNotIn("credential_invalid", admission.encode(report).decode())

    def test_public_summary_is_fixed_allowlist_and_omits_all_private_binding_data(self):
        data = fixture()
        summary = admission.public_summary(compare(data))
        output = admission.encode(summary).decode()
        for forbidden in ("configuration_sha256", "scope_declarations", "input_digests", "source_revision",
                          "selection_sha256", "installation_plan_digest", "profile_digest", "synthetic-private-token",
                          data[2]["selection_sha256"], data[2]["installation_plan_digest"]):
            self.assertNotIn(forbidden, output)
        self.assertEqual(summary["receipt_id"], RECEIPT_ID)
        summary["phase"] = "native-config-validated"
        with self.assertRaises(ConfigurationError):
            admission.public_summary(summary)
        summary = compare(data)
        summary["components"]["openjules"]["code"] = "private_value"
        with self.assertRaises(ConfigurationError):
            admission.public_summary(summary)

    def test_prepared_receipt_cannot_be_promoted_by_caller_flags(self):
        for field, new in (("phase", "native-config-validated"), ("compatible", True),
                           ("native_validation_performed", True), ("source_identity_verified", True),
                           ("execution_qualified", True), ("live_capabilities_checked", True)):
            data = fixture()
            data[2][field] = new
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                compare(data)
        data = fixture()
        data[2]["components"]["keystone-polyphony"]["valid"] = True
        with self.assertRaises(ConfigurationError):
            compare(data)

    def test_no_files_network_subprocess_environment_or_clock_work_is_needed(self):
        data = fixture()
        selection, inputs, receipt, observations = data
        encoded = {service: encode(value) for service, value in observations.items()}
        encoded_receipt = admission.encode(receipt)
        fail = AssertionError("external effect is forbidden")
        with patch("builtins.open", side_effect=fail), patch("pathlib.Path.open", side_effect=fail), \
                patch("socket.socket", side_effect=fail), patch("subprocess.Popen", side_effect=fail), \
                patch("os.getenv", side_effect=fail), patch("time.time", side_effect=fail):
            rebuilt = admission.prepare_receipt(selection, inputs, receipt_id=RECEIPT_ID)
            report = admission.compare_fixtures(encoded_receipt, selection, inputs, encoded)
            self.assertEqual(rebuilt, receipt)
            self.assertTrue(admission.public_summary(report)["fixture_compatible"])

    def test_json_ambiguity_bounds_and_deep_data_are_rejected_without_echo(self):
        data = fixture()
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"private":"\xff"}',
                    b'{"private":NaN}', b'{"private":1e999}', b'{"private":"\\ud800"}',
                    b"[" * 30 + b"0" + b"]" * 30, b" " * (admission.MAX_SELECTION_BYTES + 1)):
            with self.subTest(prefix=raw[:30]), self.assertRaises(ConfigurationError) as caught:
                admission.prepare_receipt(raw, data[1], receipt_id=RECEIPT_ID)
            self.assertNotIn("private", str(caught.exception))
        for raw in (b'{"result":null,"result":null}', b" " * (admission.MAX_RESULT_BYTES + 1)):
            report = admission.compare_fixtures(admission.encode(data[2]), data[0], data[1],
                                                {"openjules": raw, "keystone-polyphony": None})
            self.assertIsNone(report["fixture_compatible"])
            self.assertEqual(report["components"]["openjules"]["code"], "result_malformed")


if __name__ == "__main__":
    unittest.main()
