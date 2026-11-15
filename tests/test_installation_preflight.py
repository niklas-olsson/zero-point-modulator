"""Production custody regressions; mocked native results are explicit fixtures.

Actual installed command qualification belongs to the contract trials. These
tests isolate ordering, identity, recovery and refusal boundaries without
asserting that a synthetic producer is a qualified installation.
"""

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

from zpm import deployment, installation_admission as admission, installation_context
from zpm import installation_preflight as preflight
from zpm.models import ConfigurationError
from tests import test_deployment
from tests.test_installation_admission import FIXTURES, fixture


class PairedPreflightTests(unittest.TestCase):
    setUp = test_deployment.DeploymentTests.setUp
    tearDown = test_deployment.DeploymentTests.tearDown
    read = test_deployment.DeploymentTests.read

    def prepared(self):
        self.config["installation_admission"] = preflight.CONTRACT
        deployment.prepare(self.config, self.bundle)
        self.data = self.read("manifest.json")["configuration"]
        return self.read(preflight.SELECTION)

    def bootstrapped_fixture(self):
        selected = self.prepared()
        for service, source in self.data["sources"].items():
            directory = self.bundle / "environments" / service
            directory.mkdir(mode=0o700)
            installation_context.publish_private(directory / "bootstrap-complete.json", admission.encode({
                "revision": source["revision"], "python": sys.version,
            }))
        installation_context.publish_selector(self.bundle)
        self.sources_seen = {
            service: {"revision": selected["components"][service]["source_revision"],
                      "tree": "1" * 40, "static_artifact_sha256": spec["digest"]}
            for service, spec in preflight._SPECS.items()
        }
        self.interpreters_seen = {
            service: {"path": "/fixture/" + service, "resolved": "/fixture/python", "sha256": "2" * 64}
            for service in admission.SERVICES
        }
        observations = fixture()[3]
        self.native_results = {service: deepcopy(value["result"]) for service, value in observations.items()}
        for service, value in self.native_results.items():
            value["contract_digest"] = selected["components"][service]["contract_digest"]
        runtime = self.native_results["openjules"]
        runtime["configuration_sha256"] = selected["components"]["openjules"]["input_digests"]["configuration"]
        runtime["scope_declarations_sha256"] = selected["components"]["openjules"]["input_digests"]["scope_declarations"]
        original_root = runtime["descriptor"]["profiles"][0]
        runtime["descriptor"]["profiles"] = [dict(deepcopy(original_root),
            repository=item["repository"], profile=item["profile"], base_commit=item["base"],
            deployment_id=selected["deployment_id"], image=self.data["image"])
            for item in selected["profiles"]]
        authority = self.native_results["keystone-polyphony"]["descriptor"]
        authority["deployment_id"] = selected["deployment_id"]
        original_authority = authority["profiles"][0]
        authority["profiles"] = [dict(deepcopy(original_authority), **item, profile_digest="e" * 64)
                                 for item in selected["profiles"]]
        return selected

    @contextmanager
    def native_fixtures(self, **replacements):
        def source(checkout, revision, service, budget):
            return deepcopy(self.sources_seen[service])

        def interpreter(root, source_name, service):
            return deepcopy(self.interpreters_seen[service])

        def probe(root, checkout, work, service, budget):
            return {"fixture": "origin-" + service}

        def producer(root, checkout, work, service, component, inputs, budget):
            return preflight._observe(admission.encode(self.native_results[service]), service, component, 0)

        defaults = {"_source": source, "_interpreter": interpreter, "_probe": probe,
                    "_run_producer": producer}
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(preflight, name, side_effect=replacements.get(name, value)))
                     for name, value in defaults.items()}
            yield mocks

    def test_omitted_option_preserves_preparation_and_rejects_preflight_as_unselected(self):
        deployment.prepare(self.config, self.bundle)
        self.assertNotIn("installation_admission", self.read("manifest.json")["configuration"])
        self.assertFalse((self.bundle / preflight.SELECTION).exists())
        with patch.object(preflight, "guard", side_effect=AssertionError("legacy guard")), \
                patch.object(preflight, "preflight", side_effect=AssertionError("legacy native call")), \
                patch.object(deployment, "_bootstrap_locked", return_value={"installed": True}):
            self.assertEqual(deployment._bootstrap(self.bundle, self.config), {"installed": True})
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(deployment.manage(self.bundle, ["preflight"]), 2)
        self.assertEqual(json.loads(error.getvalue())["errors"], [{"owner": "zpm", "code": "not_selected"}])

    def test_explicit_option_rejects_unknown_values_and_role_schema(self):
        for value in (None, True, 1, {}, [], "next-version"):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                deployment.parse_configuration(self.config | {"installation_admission": value})
        with self.assertRaisesRegex(ConfigurationError, "single-host schemas 1 and 2"):
            deployment.parse_configuration({"schema_version": 3, "installation_admission": preflight.CONTRACT})
        self.assertFalse(self.bundle.exists())

    def test_explicit_json_rejects_duplicates_and_nonfinite_without_changing_legacy_decoder(self):
        for tail in ('"max_workers":8,"max_workers":8', '"extra":NaN', '"extra":1e999'):
            raw = '{"installation_admission":"' + preflight.CONTRACT + '",' + tail + '}'
            with self.subTest(tail=tail), self.assertRaises(ConfigurationError):
                deployment._decode_configuration_json(raw)
        self.assertEqual(deployment._decode_configuration_json('{"value":1,"value":2}'), {"value": 2})

    def test_offline_prepare_retains_private_identity_and_exact_sealed_inputs(self):
        with patch.object(preflight, "_run", side_effect=AssertionError("prepare executed producer")), \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("prepare contacted network")):
            first = self.prepared()
            raw = (self.bundle / preflight.SELECTION).read_bytes()
            deployment.prepare(self.config, self.bundle)
            self.assertEqual((self.bundle / preflight.SELECTION).read_bytes(), raw)
            self.assertTrue(deployment.verify(self.bundle)["valid"])
        self.assertEqual((self.bundle / preflight.SELECTION).stat().st_mode & 0o777, 0o600)
        self.assertNotIn("profile_digest", first["profiles"][0])
        self.assertFalse((self.bundle / preflight.RECEIPT).exists())
        self.assertFalse((self.bundle / preflight.STATUS).exists())
        self.assertEqual(self.read(preflight.SCOPES)["scopes"], [{
            "deployment_id": "installation-test", "repository": "github:github.com/owner/project"}])

    def test_bootstrap_guard_and_native_order_are_under_existing_manager_lock(self):
        self.prepared()
        events = []
        @contextmanager
        def locked(root):
            events.append("lock")
            yield 27
            events.append("unlock")
        with patch.object(deployment, "_manager_lock", locked), \
                patch.object(preflight, "guard", side_effect=lambda *_: events.append("guard")), \
                patch.object(deployment, "_bootstrap_locked", side_effect=lambda *_: events.append("bootstrap") or {"installed": True}), \
                patch.object(preflight, "preflight", side_effect=lambda *_: events.append("native") or {"fixture": True}):
            report = deployment._bootstrap(self.bundle, self.data)
        self.assertEqual(events, ["lock", "guard", "bootstrap", "native", "unlock"])
        self.assertEqual(report["installation_admission"], {"fixture": True})

    def test_guard_refuses_before_existing_context_probe_and_start_never_launches(self):
        self.prepared()
        failure = preflight.PreflightError("openjules", "source_bytes")
        with patch.object(preflight, "guard", side_effect=failure), \
                patch.object(installation_context, "probe") as context, \
                patch.object(deployment.subprocess, "Popen") as service:
            with self.assertRaises(preflight.PreflightError) as caught:
                deployment._start(self.bundle, self.data)
        self.assertIs(caught.exception, failure)
        context.assert_not_called()
        service.assert_not_called()

    def test_native_refusal_occurs_after_context_but_before_first_service(self):
        self.prepared()
        events = []
        def refused(*_):
            events.append("native")
            raise preflight.PreflightError("zpm", "required_peer_unvalidated")
        with patch.object(deployment, "_installation_context", side_effect=lambda *_: events.append("context")), \
                patch.object(preflight, "preflight", side_effect=refused), \
                patch.object(deployment.subprocess, "Popen") as service:
            with self.assertRaises(preflight.PreflightError):
                deployment._start(self.bundle, self.data)
        self.assertEqual(events, ["context", "native"])
        service.assert_not_called()

    def test_repeat_uses_native_kp_digest_and_preserves_original_receipt_bytes(self):
        selected = self.bootstrapped_fixture()
        state = (self.bundle / "state/zpm.sqlite3").read_bytes()
        with self.native_fixtures() as calls:
            first = preflight.preflight(self.bundle, self.data)
            raw = (self.bundle / preflight.RECEIPT).read_bytes()
            second = preflight.preflight(self.bundle, self.data)
        self.assertEqual(first, second)
        self.assertEqual(first["receipt_id"], selected["receipt_id"])
        self.assertEqual((self.bundle / preflight.RECEIPT).read_bytes(), raw)
        self.assertEqual(json.loads(raw)["profiles"][0]["profile_digest"], "e" * 64)
        self.assertEqual(calls["_run_producer"].call_count, 4)
        self.assertEqual((self.bundle / "state/zpm.sqlite3").read_bytes(), state)
        self.assertEqual(list((self.bundle / "state/openjules").iterdir()), [])
        self.assertEqual(list((self.bundle / "state/polyphony").iterdir()), [])
        self.assertFalse(first["services_started"])
        self.assertFalse(first["live_capabilities_checked"])
        self.assertFalse(first["execution_qualified"])
        self.assertNotIn("receipt_established", first)
        self.assertTrue(self.read(preflight.STATUS)["receipt_established"])

    def test_interruption_before_first_receipt_reuses_original_prepared_uuid(self):
        selected = self.bootstrapped_fixture()
        original = (self.bundle / preflight.SELECTION).read_bytes()
        with self.native_fixtures(_run_producer=preflight.PreflightError("openjules", "process_timeout")):
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.preflight(self.bundle, self.data)
        self.assertEqual(caught.exception.report["receipt_id"], selected["receipt_id"])
        self.assertFalse((self.bundle / preflight.RECEIPT).exists())
        self.assertEqual((self.bundle / preflight.SELECTION).read_bytes(), original)
        with self.native_fixtures():
            report = preflight.preflight(self.bundle, self.data)
        self.assertEqual(report["receipt_id"], selected["receipt_id"])

    def test_later_component_drift_is_refused_before_any_producer_probe(self):
        self.bootstrapped_fixture()
        with self.native_fixtures():
            preflight.preflight(self.bundle, self.data)
        original = (self.bundle / preflight.RECEIPT).read_bytes()
        for mapping, field in ((self.sources_seen, "tree"), (self.interpreters_seen, "sha256")):
            value = mapping["keystone-polyphony"][field]
            mapping["keystone-polyphony"][field] = "f" * len(value)
            with self.subTest(field=field), self.native_fixtures() as calls:
                with self.assertRaises(preflight.PreflightError) as caught:
                    preflight.preflight(self.bundle, self.data)
                self.assertEqual(caught.exception.code, "retained_receipt_mismatch")
                calls["_probe"].assert_not_called()
                calls["_run_producer"].assert_not_called()
                self.assertEqual((self.bundle / preflight.RECEIPT).read_bytes(), original)
            mapping["keystone-polyphony"][field] = value
        with self.native_fixtures():
            self.assertTrue(preflight.preflight(self.bundle, self.data)["compatible"])

    def test_stale_kp_bootstrap_marker_refuses_before_any_producer(self):
        self.bootstrapped_fixture()
        marker = self.bundle / "environments/polyphony/bootstrap-complete.json"
        original = marker.read_bytes()
        marker.write_bytes(admission.encode({"revision": "0" * 40, "python": sys.version}))
        with self.native_fixtures() as calls:
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.preflight(self.bundle, self.data)
        self.assertEqual((caught.exception.owner, caught.exception.code), ("keystone-polyphony", "bootstrap_required"))
        calls["_probe"].assert_not_called()
        calls["_run_producer"].assert_not_called()
        self.assertFalse((self.bundle / preflight.RECEIPT).exists())
        marker.write_bytes(original)
        with self.native_fixtures():
            self.assertTrue(preflight.preflight(self.bundle, self.data)["compatible"])

    def test_changed_sealed_input_refuses_and_restoration_keeps_original_identity(self):
        self.bootstrapped_fixture()
        with self.native_fixtures():
            first = preflight.preflight(self.bundle, self.data)
        receipt = (self.bundle / preflight.RECEIPT).read_bytes()
        path = self.bundle / "config/openjules.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.native_fixtures() as calls:
            with self.assertRaises(preflight.PreflightError):
                preflight.preflight(self.bundle, self.data)
            calls["_probe"].assert_not_called()
            calls["_run_producer"].assert_not_called()
        path.write_bytes(original)
        with self.native_fixtures():
            self.assertEqual(preflight.preflight(self.bundle, self.data), first)
        self.assertEqual((self.bundle / preflight.RECEIPT).read_bytes(), receipt)

    def test_known_lost_receipt_cannot_adopt_changed_interpreter_or_later_failure_status(self):
        self.bootstrapped_fixture()
        with self.native_fixtures():
            preflight.preflight(self.bundle, self.data)
        original = (self.bundle / preflight.RECEIPT).read_bytes()
        (self.bundle / preflight.RECEIPT).unlink()
        self.interpreters_seen["keystone-polyphony"]["sha256"] = "f" * 64
        for _ in range(2):
            with self.native_fixtures() as calls:
                with self.assertRaises(preflight.PreflightError) as caught:
                    preflight.preflight(self.bundle, self.data)
                self.assertEqual(caught.exception.code, "retained_receipt_mismatch")
                calls["_probe"].assert_not_called()
                calls["_run_producer"].assert_not_called()
            self.assertTrue(self.read(preflight.STATUS)["receipt_established"])
            self.assertFalse((self.bundle / preflight.RECEIPT).exists())
        preflight._write_new(self.bundle / preflight.RECEIPT, original)
        self.interpreters_seen["keystone-polyphony"]["sha256"] = "2" * 64
        with self.native_fixtures():
            self.assertTrue(preflight.preflight(self.bundle, self.data)["compatible"])

    def test_unsupported_required_peer_preserves_valid_native_status_without_receipt(self):
        selected = self.bootstrapped_fixture()
        runtime = self.native_results["openjules"]["descriptor"]["profiles"][0]
        runtime["features"]["children"] = "managed-children-v1"
        # The producer shape is tested separately. This fixture deliberately
        # supplies already parsed descriptors at the compatibility boundary.
        runtime["required_peer_contracts"].append({"contract": "public-conversation-v1", "required_capabilities": {}})
        def parsed(root, checkout, work, service, component, inputs, budget):
            value = deepcopy(self.native_results[service])
            return value, value["descriptor"]
        with self.native_fixtures(_run_producer=parsed):
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.preflight(self.bundle, self.data)
        report = caught.exception.report
        self.assertEqual(report["receipt_id"], selected["receipt_id"])
        self.assertEqual(report["errors"], [{"owner": "zpm", "code": "required_peer_unvalidated"}])
        self.assertEqual(report["components"], {service: {"status": "valid"} for service in admission.SERVICES})
        self.assertTrue(report["native_validation_performed"])
        self.assertIsNone(report["compatible"])
        self.assertFalse((self.bundle / preflight.RECEIPT).exists())

    def test_new_wal_or_top_level_journal_during_validation_cannot_publish_receipt(self):
        self.bootstrapped_fixture()
        for name in ("zpm.sqlite3-wal", "unexpected-journal", ".installation-validation-unowned"):
            artifact = self.bundle / "state" / name
            def mutate(root, checkout, work, service, component, inputs, budget):
                artifact.write_text("synthetic journal mutation")
                return preflight._observe(admission.encode(self.native_results[service]), service, component, 0)
            with self.subTest(name=name), self.native_fixtures(_run_producer=mutate):
                with self.assertRaises(preflight.PreflightError) as caught:
                    preflight.preflight(self.bundle, self.data)
                self.assertEqual(caught.exception.code, "installation_changed")
                self.assertFalse((self.bundle / preflight.RECEIPT).exists())
            artifact.unlink()


class ProducerBoundaryTests(unittest.TestCase):
    def test_native_exit_codes_disabled_receipts_and_private_malformed_output(self):
        _, _, receipt, observations = fixture("boolean_port")
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight._observe(admission.encode(observations["openjules"]["result"]), "openjules", receipt["components"]["openjules"], 2)
        self.assertEqual((caught.exception.code, caught.exception.component_status), ("producer_invalid", "invalid"))
        for service, invalid_exit in (("openjules", 2), ("keystone-polyphony", 1)):
            _, _, receipt, observations = fixture()
            component = receipt["components"][service]
            native = observations[service]["result"]
            with self.subTest(service=service), self.assertRaises(preflight.PreflightError) as caught:
                preflight._observe(admission.encode(native), service, component, invalid_exit)
            self.assertEqual(caught.exception.code, "exit_receipt_mismatch")
        native = observations["keystone-polyphony"]["result"]
        invalid = deepcopy(native)
        invalid.update(valid=False, managed_enabled=None, descriptor=None,
                       errors=[{"code": "policy_invalid", "path": ""}])
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight._observe(admission.encode(invalid), "keystone-polyphony", component, 1)
        self.assertEqual((caught.exception.code, caught.exception.component_status), ("producer_invalid", "invalid"))
        native.update(managed_enabled=False, descriptor=None)
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight._observe(admission.encode(native), "keystone-polyphony", component, 0)
        self.assertEqual((caught.exception.code, caught.exception.component_status), ("producer_disabled", "disabled"))
        for raw in (b'{"private":"never-publish"}', b'{"valid":true,"valid":false}', b'NaN', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(preflight.PreflightError) as caught:
                preflight._observe(raw, "keystone-polyphony", component, 0)
            self.assertNotIn("never-publish", str(caught.exception) + json.dumps(caught.exception.report))

    def test_bounded_process_timeout_overflow_and_signal_never_become_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            for script, limit, seconds, expected in (
                ("import time;time.sleep(10)", 100, 0.1, "process_timeout"),
                ("print('private-output-'*10000)", 100, 3, "process_output_bound"),
                ("import os,signal;os.kill(os.getpid(),signal.SIGTERM)", 100, 3, "process_signal"),
            ):
                with self.subTest(expected=expected), self.assertRaises(preflight.PreflightError) as caught:
                    preflight._run([sys.executable, "-I", "-c", script], Path(temporary), "openjules", preflight._Budget(), limit=limit, seconds=seconds)
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private-output", str(caught.exception))

    def test_environment_and_command_exclude_inherited_credentials_and_bytecode(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "private-canary", "PYTHONPATH": "/untrusted",
                                    "GIT_CONFIG_COUNT": "99", "HTTP_PROXY": "private-canary"}):
            environment = preflight.environment()
        self.assertNotIn("private-canary", json.dumps(environment))
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
        for service in admission.SERVICES:
            command = preflight._command(Path("/installed/python"), Path("/selected"), Path("/private-stage"), service, probe=True)
            self.assertEqual(command[1:6], ["-I", "-B", "-X", "pycache_prefix=/private-stage/unused-bytecode", "-c"])
            self.assertIn("sys.path.append", command[6])

    def test_supported_cli_origin_probe_does_not_require_private_contract_constant(self):
        self.assertEqual(preflight._SPECS["keystone-polyphony"]["module"], "src.liminal_bridge.managed_cli")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            work = root / "work"
            work.mkdir()
            for service, spec in preflight._SPECS.items():
                checkout = root / spec["source"]
                package = checkout / spec["package"].replace(".", "/")
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("")
                if service == "openjules":
                    (checkout / "src/__init__.py").write_text("")
                module = checkout / (spec["module"].replace(".", "/") + ".py")
                module.write_text("# Supported entrypoint; no private CONTRACT_PATH export.\n")
                venv.EnvBuilder(with_pip=False).create(root / "environments" / spec["source"])
                observed = preflight._probe(root, checkout, work, service, preflight._Budget())
                self.assertEqual(observed["module"], str(module))
                self.assertNotIn("contract", observed)


class SourceAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.checkout = Path(self.temporary.name).resolve()
        self.module = self.checkout / "src/worker/installation_validation.py"
        self.module.parent.mkdir(parents=True)
        self.module.write_text("original = True\n")
        contract = self.checkout / preflight._SPECS["openjules"]["artifact"]
        contract.parent.mkdir()
        contract.write_bytes((FIXTURES / "openjules-installation-v1.json").read_bytes())
        (self.checkout / ".gitignore").write_text("__pycache__/\njson.py\n")
        self.git("init", "-q")
        self.commit()
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.tree = self.git("rev-parse", "HEAD^{tree}").strip()

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.checkout), *args], text=True, stderr=subprocess.DEVNULL)

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")

    def observe(self):
        return preflight._source(self.checkout, self.revision, "openjules", preflight._Budget())

    def test_git_replacement_cannot_substitute_tree_or_working_bytes(self):
        self.module.write_text("original = False\n")
        self.commit()
        replacement = self.git("rev-parse", "HEAD").strip()
        replacement_tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.git("checkout", "-q", "--detach", self.revision)
        self.git("replace", self.revision, replacement)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.revision)
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}").strip(), replacement_tree)
        self.assertEqual(self.observe()["tree"], self.tree)
        self.module.write_text("original = False\n")
        with self.assertRaises(preflight.PreflightError) as caught:
            self.observe()
        self.assertEqual(caught.exception.code, "source_bytes")

    def test_ignored_import_shadow_is_refused_even_when_git_status_is_clean(self):
        (self.checkout / "json.py").write_text("raise RuntimeError('private-import-canary')\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        with self.assertRaises(preflight.PreflightError) as caught:
            self.observe()
        self.assertEqual(caught.exception.code, "source_inventory")
        self.assertNotIn("private-import-canary", str(caught.exception))

    def test_tracked_nonimport_symlink_and_gitlink_are_accepted_without_initialization(self):
        (self.checkout / "launcher").symlink_to("src/worker/installation_validation.py")
        self.git("add", "launcher")
        self.git("update-index", "--add", "--cacheinfo", "160000," + self.revision + ",third_party/neutral")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "nonimport links")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.observe()["revision"], self.revision)
        self.assertFalse((self.checkout / "third_party/neutral").exists())


if __name__ == "__main__":
    unittest.main()
