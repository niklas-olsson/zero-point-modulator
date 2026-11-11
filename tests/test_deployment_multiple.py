"""Behavioral installation checks for schema 2, without service/runtime claims.

These fixtures contain local Git repositories and real generated SQLite state.
They do not bootstrap OpenJules/Polyphony, contact a provider or run Docker.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from zpm.deployment import (
    CHILD_ENTRYPOINTS, MANAGED_ENTRYPOINTS, MODEL_ENTRYPOINTS, SOURCE_FILES,
    _environment, parse_configuration, prepare, verify,
)
from zpm.models import Binding, ConfigurationError
from zpm.store import Store


REPOSITORIES = ("github:github.com/owner/alpha", "github:github.com/owner/beta")


class MultipleRepositoryDeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "installation"
        self.sources = {}
        for name in SOURCE_FILES:
            files = set(MANAGED_ENTRYPOINTS[name])
            files.update(CHILD_ENTRYPOINTS.get(name, ()))
            if name == "openjules":
                files.update(MODEL_ENTRYPOINTS)
            self.sources[name] = self.checkout(name, files)
        self.entries = []
        for index, name in enumerate(("alpha", "beta")):
            self.entries.append({
                "binding": {
                    "schema_version": 1, "repository": "Owner/" + name,
                    "profile": "approved", "desired_workers": index,
                },
                "source": self.checkout("task-" + name, {"README.md"}),
                "image": "sha256:" + str(index + 1) * 64,
                "command": ["python3", "-c", "print(" + repr(name) + ")"],
                "policy": {
                    "checks": ["tests"], "reviewers": ["operator"],
                    "max_attempts": 2 + index, "budget_seconds": 300 + index * 60,
                    "attempt_seconds": 60 + index, "capabilities": ["python"],
                },
            })
        self.configuration = {
            "schema_version": 2, "deployment_id": "multiple-installation",
            "max_workers": 8, "sources": deepcopy(self.sources),
            "repositories": deepcopy(self.entries),
        }

    def checkout(self, name, files):
        directory = self.root / name
        directory.mkdir()
        for filename in files:
            path = directory / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("local source fixture: " + name + "\n")
        for arguments in (
            ["init", "-q"], ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
        ):
            subprocess.run(["git", "-C", str(directory), *arguments],
                           check=True, capture_output=True)
        revision = subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True,
        ).strip()
        return {"checkout": str(directory), "revision": revision}

    def read(self, name, bundle=None):
        return json.loads(((bundle or self.bundle) / name).read_text())

    def sealed(self, bundle=None):
        root = bundle or self.bundle
        manifest = self.read("manifest.json", root)
        return {
            name: (root / name).read_bytes()
            for name in ("manifest.json", *manifest["files"])
        }

    def retained_rows(self, bundle=None):
        path = (bundle or self.bundle) / "state/zpm.sqlite3"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as database:
            return {
                table: database.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                for table in ("metadata", "bindings", "workers")
            }

    def model_policy(self, name):
        return {
            "endpoint": "https://provider.example/v1/chat/completions",
            "model": "approved-" + name, "token_ref": "env:PROVIDER_" + name.upper(),
            "max_calls": 4, "max_output_tokens": 64, "max_total_output_tokens": 256,
            "request_timeout_seconds": 5,
        }

    def assert_no_reference_values_written(self, values):
        for path in self.bundle.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                for value in values:
                    self.assertNotIn(value.encode(), content, str(path))

    def test_normalization_is_canonical_and_reordering_is_equivalent(self):
        original = deepcopy(self.configuration)
        normalized = parse_configuration(original)
        self.assertEqual(original, self.configuration)
        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(
            [item["binding"]["repository"] for item in normalized["repositories"]],
            list(REPOSITORIES),
        )
        reordered = deepcopy(original)
        reordered["repositories"].reverse()
        reordered["repositories"][0]["binding"]["repository"] = REPOSITORIES[1]
        self.assertEqual(parse_configuration(reordered), normalized)
        self.assertNotIn("binding", normalized)
        self.assertNotIn("source", normalized)
        self.assertEqual(normalized["sources"], self.sources)
        for entry in normalized["repositories"]:
            self.assertEqual(entry["runtime"]["uid"], 65532)
            self.assertEqual(entry["environment_refs"], {})

    def test_schema_and_cardinality_errors_cannot_publish_a_partial_bundle(self):
        cases = []
        too_many = []
        for index in range(101):
            entry = deepcopy(self.entries[0])
            entry["binding"]["repository"] = "owner/overflow-" + str(index).zfill(3)
            too_many.append(entry)
        # Every entry is otherwise valid and identities are distinct, so a
        # duplicate-identity rejection cannot conceal a missing scope limit.
        for value in ([], [self.entries[0]], too_many, {}):
            cases.append(self.configuration | {"repositories": value})
        cases.extend((
            self.configuration | {"schema_version": True},
            self.configuration | {"binding": self.entries[0]["binding"]},
            self.configuration | {"runtime": {"uid": 65532}},
        ))
        for field, value in (("max_workers", 2), ("ports", {"openjules": 8001}), ("unexpected", "value")):
            bad = deepcopy(self.configuration)
            bad["repositories"][1][field] = value
            cases.append(bad)
        duplicate = deepcopy(self.configuration)
        duplicate["repositories"][1]["binding"]["repository"] = REPOSITORIES[0]
        cases.append(duplicate)
        missing = deepcopy(self.configuration)
        del missing["repositories"][1]["source"]
        cases.append(missing)
        for index, configuration in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ConfigurationError):
                prepare(configuration, self.bundle)
            self.assertFalse(self.bundle.exists())

    def test_hundred_repository_limit_fits_the_single_runtime_principal(self):
        configuration = deepcopy(self.configuration)
        configuration["repositories"] = []
        for index in range(100):
            entry = deepcopy(self.entries[0])
            entry["binding"]["repository"] = "owner/repository-" + str(index).zfill(3)
            configuration["repositories"].append(entry)
        # Reusing an explicitly approved immutable checkout is supported.
        report = prepare(configuration, self.bundle)
        self.assertEqual(len(report["repositories"]), 100)
        credentials = self.read("secrets/polyphony.json")
        runtime = [value for value in credentials.values() if value["role"] == "runtime"]
        self.assertEqual(len(runtime), 1)
        self.assertEqual(len(runtime[0]["repositories"]), 100)
        self.assertEqual(len(set(runtime[0]["repositories"])), 100)
        self.assertEqual(len(self.read("config/openjules.json")["profiles"]), 100)
        self.assertLessEqual((self.bundle / "config/openjules.json").stat().st_size, 1_000_000)

    def test_preparation_renders_one_service_pair_with_exact_repository_scopes(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("preparation contacted network")):
            report = prepare(self.configuration, self.bundle)
            self.assertTrue(verify(self.bundle)["valid"])
        self.assertFalse(report["services_started"])
        self.assertFalse(report["remote_access_checked"])
        authority = self.read("config/polyphony.json")
        runtime = self.read("config/openjules.json")
        self.assertEqual(authority["schema_version"], 1)
        self.assertEqual(runtime["schema_version"], 1)
        self.assertEqual(set(authority["repositories"]), set(REPOSITORIES))
        self.assertEqual(runtime["host"], "127.0.0.1")
        self.assertEqual(runtime["authority"]["endpoint"], "http://127.0.0.1:8766")
        profiles = {value["repository"]: value for value in runtime["profiles"]}
        self.assertEqual(len(runtime["profiles"]), 2)
        self.assertEqual(set(profiles), set(REPOSITORIES))
        for repository, original in zip(REPOSITORIES, self.entries):
            generated = profiles[repository]
            self.assertEqual(generated["deployment_id"], "multiple-installation")
            self.assertEqual(generated["name"], "approved")
            self.assertEqual(generated["source_path"], original["source"]["checkout"])
            self.assertEqual(generated["base_commit"], original["source"]["revision"])
            self.assertEqual(generated["image"], original["image"])
            self.assertEqual(generated["command"], original["command"])
            policy = authority["repositories"][repository]
            self.assertEqual(policy["max_attempts"], original["policy"]["max_attempts"])
            self.assertEqual(policy["profiles"]["approved"]["base"], original["source"]["revision"])
            self.assertEqual(policy["profiles"]["approved"]["attempt_seconds"], original["policy"]["attempt_seconds"])
        self.assertEqual(runtime["state_directory"], str(self.bundle / "state/openjules/runtime"))
        self.assertEqual(runtime["lifecycle_database"], str(self.bundle / "state/openjules/lifecycle.sqlite3"))
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            self.assertEqual([row["repository"] for row in store.bindings()], list(REPOSITORIES))
            self.assertEqual([row["desired_workers"] for row in store.bindings()], [0, 1])
        credentials = self.read("secrets/polyphony.json")
        self.assertEqual(len(credentials), 6)
        roles = {}
        for token, principal in credentials.items():
            self.assertGreaterEqual(len(token), 32)
            roles.setdefault(principal["role"], []).append((token, principal))
            if principal["role"] != "owner":
                self.assertEqual(principal["deployment_id"], "multiple-installation")
        self.assertEqual(set(roles), {"owner", "runtime", "ingress", "reviewer"})
        self.assertEqual(len(roles["owner"]), 1)
        self.assertEqual(len(roles["runtime"]), 1)
        self.assertNotIn("repositories", roles["owner"][0][1])
        self.assertEqual(roles["runtime"][0][1]["repositories"], list(REPOSITORIES))
        for role in ("ingress", "reviewer"):
            self.assertEqual(sorted(value["repositories"] for _, value in roles[role]), [[r] for r in REPOSITORIES])
        controllers = self.read("secrets/openjules.json")
        self.assertEqual(len(controllers), 1)
        controller, grants = next(iter(controllers.items()))
        self.assertEqual(grants, [{"deployment_id": "multiple-installation", "repository": r} for r in REPOSITORIES])
        self.assertNotIn(controller, credentials)
        environment = self.read("secrets/environment.json")
        self.assertEqual(environment["OPENJULES_WORKER_TOKEN"], controller)
        self.assertEqual(environment["POLYPHONY_RUNTIME_TOKEN"], roles["runtime"][0][0])
        operator = self.read("secrets/operator.json")
        self.assertEqual(operator["schema_version"], 2)
        self.assertEqual(operator["owner"], roles["owner"][0][0])
        self.assertEqual(set(operator["repositories"]), set(REPOSITORIES))
        reports = {item["repository"]: item for item in report["repositories"]}
        self.assertEqual(set(reports), set(REPOSITORIES))
        self.assertFalse((self.bundle / "config/repository.json").exists())
        for repository in REPOSITORIES:
            suffix = hashlib.sha256(repository.encode()).hexdigest()
            reference = "config/repository-" + suffix + ".json"
            self.assertEqual(reports[repository]["binding_file"], reference)
            self.assertEqual(self.read(reference)["repository"], repository)
            details = operator["repositories"][repository]
            self.assertEqual(details["request_key_prefix"], "repository-" + suffix + "-")
            for role in ("ingress", "reviewer"):
                self.assertEqual(credentials[details[role]]["role"], role)
                self.assertEqual(credentials[details[role]]["repositories"], [repository])
        # Distinct credentials establish scopes, not independent reviewers.
        self.assertEqual({p["identity"] for _, p in roles["reviewer"]}, {"operator"})
        public = json.dumps(report) + (self.bundle / "manifest.json").read_text()
        public += "".join(path.read_text() for path in (self.bundle / "config").iterdir())
        for token in (*credentials, *controllers):
            self.assertNotIn(token, public)
        for path in (self.bundle / "secrets").iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o700)

    def test_reuse_keeps_each_repositorys_retained_state_credentials_and_evidence(self):
        prepare(self.configuration, self.bundle)
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            current = store.binding(REPOSITORIES[1])
            binding = Binding.parse({name: current[name] for name in Binding.__dataclass_fields__})
            store.reserve(binding, current["revision"])
            store.change(REPOSITORIES[0], "target", 3)
            store.change(REPOSITORIES[0], "remove")
            store.change(REPOSITORIES[1], "target", 2)
            store.change(REPOSITORIES[1], "pause")
        evidence = self.bundle / "state/openjules/retained-artifact"
        evidence.write_bytes(b"existing capture bytes")
        before, sealed = self.retained_rows(), self.sealed()
        reordered = deepcopy(self.configuration)
        reordered["repositories"].reverse()
        with patch("zpm.deployment.Store", side_effect=AssertionError("reuse initialized state")), \
                patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("reuse rotated credentials")):
            report = prepare(reordered, self.bundle)
            self.assertTrue(verify(self.bundle)["valid"])
        self.assertTrue(report["reused"])
        self.assertFalse(report["services_started"])
        self.assertEqual(self.retained_rows(), before)
        self.assertEqual(self.sealed(), sealed)
        self.assertEqual(evidence.read_bytes(), b"existing capture bytes")

    def test_missing_second_retained_enrollment_is_never_recreated(self):
        prepare(self.configuration, self.bundle)
        with sqlite3.connect(self.bundle / "state/zpm.sqlite3") as database:
            database.execute("DELETE FROM bindings WHERE repository=?", (REPOSITORIES[1],))
        before, sealed = self.retained_rows(), self.sealed()
        for operation in (lambda: verify(self.bundle), lambda: prepare(self.configuration, self.bundle)):
            with self.assertRaises(ConfigurationError):
                operation()
            self.assertEqual(self.retained_rows(), before)
            self.assertEqual(self.sealed(), sealed)

    def test_live_addition_and_schema1_migration_do_not_replace_existing_installations(self):
        prepare(self.configuration, self.bundle)
        sealed, rows = self.sealed(), self.retained_rows()
        expanded = deepcopy(self.configuration)
        third = deepcopy(self.entries[0])
        third["binding"]["repository"] = "owner/third"
        expanded["repositories"].append(third)
        with self.assertRaises(ConfigurationError):
            prepare(expanded, self.bundle)
        self.assertEqual(self.sealed(), sealed)
        self.assertEqual(self.retained_rows(), rows)
        legacy = {
            "schema_version": 1, "deployment_id": self.configuration["deployment_id"],
            "max_workers": 8, "sources": self.sources, **deepcopy(self.entries[0]),
        }
        legacy_bundle = self.root / "legacy-installation"
        prepare(legacy, legacy_bundle)
        old_files, old_rows = self.sealed(legacy_bundle), self.retained_rows(legacy_bundle)
        with self.assertRaises(ConfigurationError):
            prepare(self.configuration, legacy_bundle)
        self.assertEqual(self.sealed(legacy_bundle), old_files)
        self.assertEqual(self.retained_rows(legacy_bundle), old_rows)

    def test_data_paths_stay_outside_every_checkout_without_banning_source_reuse(self):
        shared = deepcopy(self.configuration)
        shared["repositories"][1]["source"] = shared["sources"]["zpm"]
        prepare(shared, self.bundle)
        self.assertTrue(verify(self.bundle)["valid"])
        for source in (*self.sources.values(), *(entry["source"] for entry in self.entries)):
            checkout = Path(source["checkout"])
            for destination in (checkout, checkout / "deployment"):
                with self.subTest(destination=str(destination)), self.assertRaises(ConfigurationError):
                    prepare(self.configuration, destination)
                self.assertFalse((checkout / "deployment").exists())
        with self.assertRaises(ConfigurationError):
            prepare(self.configuration, self.root)
        self.assertTrue(verify(self.bundle)["valid"])

    def test_guest_environment_aliases_are_scoped_by_repository_and_target(self):
        configuration = deepcopy(self.configuration)
        values = {}
        expected = {}
        for index, entry in enumerate(configuration["repositories"]):
            entry["environment_refs"] = {}
            for target in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
                source = "INPUT_" + str(index) + "_" + target
                value = "synthetic-guest-" + str(index) + "-" + target
                entry["environment_refs"][target] = "env:" + source
                values[source] = value
                expected[(REPOSITORIES[index], target)] = value
        with patch.dict(os.environ, values | {"PYTHONPATH": "/untrusted", "UNRELATED_SECRET": "do-not-forward"}):
            prepare(configuration, self.bundle)
            normalized = self.read("manifest.json")["configuration"]
            environments = {name: _environment(self.bundle, name, normalized) for name in (*SOURCE_FILES, "bootstrap")}
        profiles = self.read("config/openjules.json")["profiles"]
        aliases = set()
        for profile in profiles:
            suffix = hashlib.sha256(profile["repository"].encode()).hexdigest().upper()
            self.assertEqual(set(profile["environment_refs"]), {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
            for target, reference in profile["environment_refs"].items():
                alias = "ZPM_REPOSITORY_" + suffix + "_GUEST_" + target
                self.assertEqual(reference, "env:" + alias)
                self.assertNotIn(alias, aliases)
                aliases.add(alias)
                self.assertEqual(environments["openjules"][alias], expected[(profile["repository"], target)])
        self.assertEqual(len(aliases), 4)
        controls = self.read("secrets/environment.json")
        owners = {"OPENJULES_WORKER_TOKEN": "zpm", "POLYPHONY_RUNTIME_TOKEN": "openjules"}
        for variable, owner in owners.items():
            for name, environment in environments.items():
                if name == owner:
                    self.assertEqual(environment[variable], controls[variable])
                else:
                    self.assertNotIn(variable, environment)
                    self.assertNotIn(controls[variable], environment.values())
        for name, environment in environments.items():
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("UNRELATED_SECRET", environment)
            self.assertTrue(set(values).isdisjoint(environment))
            if name != "openjules":
                self.assertTrue(aliases.isdisjoint(environment))
                self.assertTrue(set(values.values()).isdisjoint(environment.values()))
        self.assert_no_reference_values_written(values.values())
        for missing in (True, False):
            inputs = values | {"ZPM_MODEL_OPENAI_API_KEY": "old-singleton-guest-value"}
            if missing:
                inputs.pop("INPUT_0_OPENAI_API_KEY")
            else:
                inputs["INPUT_0_OPENAI_API_KEY"] = ""
            with self.subTest(missing_guest=missing), patch.dict(os.environ, inputs, clear=True):
                with self.assertRaises(ConfigurationError):
                    _environment(self.bundle, "openjules", normalized)

    def test_provider_and_child_profiles_keep_repository_binding_and_distinct_provider_aliases(self):
        configuration = deepcopy(self.configuration)
        for name, entry in zip(("alpha", "beta"), configuration["repositories"]):
            entry["model_policy"] = self.model_policy(name)
        configuration["repositories"][0]["child_policy"] = {
            "max_children": 2, "max_child_seconds": 20, "max_child_turns": 4,
            "capabilities": ["python"],
        }
        values = {"PROVIDER_ALPHA": "synthetic-provider-alpha", "PROVIDER_BETA": "synthetic-provider-beta"}
        with patch.dict(os.environ, values):
            prepare(configuration, self.bundle)
            normalized = self.read("manifest.json")["configuration"]
            environments = {name: _environment(self.bundle, name, normalized) for name in (*SOURCE_FILES, "bootstrap")}
        runtime = self.read("config/openjules.json")
        self.assertEqual(len(runtime["models"]), 2)
        aliases = set()
        for model in runtime["models"]:
            repository = model["repository"]
            alias = "ZPM_REPOSITORY_" + hashlib.sha256(repository.encode()).hexdigest().upper() + "_PROVIDER_TOKEN"
            self.assertEqual(model["deployment_id"], "multiple-installation")
            self.assertEqual(model["profile"], "approved")
            self.assertEqual(model["policy"]["token_ref"], "env:" + alias)
            self.assertNotIn(alias, aliases)
            aliases.add(alias)
            self.assertEqual(environments["openjules"][alias], values["PROVIDER_" + repository.rsplit("/", 1)[1].upper()])
        self.assertEqual(len(runtime["children"]), 1)
        child = runtime["children"][0]
        self.assertEqual(child["repository"], REPOSITORIES[0])
        self.assertEqual(child["profile"], "approved")
        policy = self.read("config/polyphony.json")["repositories"]
        self.assertEqual(policy[REPOSITORIES[0]]["profiles"]["approved"]["max_children"], 2)
        self.assertNotIn("max_children", policy[REPOSITORIES[1]]["profiles"]["approved"])
        for name in ("zpm", "polyphony", "bootstrap"):
            self.assertTrue(aliases.isdisjoint(environments[name]))
            self.assertTrue(set(values.values()).isdisjoint(environments[name].values()))
        self.assert_no_reference_values_written(values.values())
        for invalid in (None, "", "invalid token with spaces"):
            inputs = values | {"ZPM_MODEL_PROVIDER_TOKEN": "old-singleton-provider-value"}
            if invalid is None:
                inputs.pop("PROVIDER_ALPHA")
            else:
                inputs["PROVIDER_ALPHA"] = invalid
            with self.subTest(invalid_provider=invalid), patch.dict(os.environ, inputs, clear=True):
                with self.assertRaises(ConfigurationError):
                    _environment(self.bundle, "openjules", normalized)

    def test_actual_serialized_openjules_file_accepts_one_million_bytes_and_rejects_one_more(self):
        configuration = deepcopy(self.configuration)
        for entry in configuration["repositories"]:
            entry["command"] = ["python3", "-c", "pass", "å"] + ["x"] * 124
        # Equal-length destination paths have equal encoded length here. Measure
        # the real public output, not a private formatter or character estimate.
        probe = self.root / ("p" * len(self.bundle.name))
        prepare(configuration, probe)
        measured = (probe / "config/openjules.json").stat().st_size
        padding = 1_000_000 - measured
        self.assertGreater(padding, 0)
        exact = deepcopy(configuration)
        for entry in exact["repositories"]:
            for index in range(3, len(entry["command"])):
                value = entry["command"][index]
                amount = min(4096 - len(value), padding)
                entry["command"][index] += "x" * amount
                padding -= amount
        self.assertEqual(padding, 0, "valid bounded argv cannot reach the declared boundary")
        prepare(exact, self.bundle)
        self.assertEqual((self.bundle / "config/openjules.json").stat().st_size, 1_000_000)
        self.assertTrue(verify(self.bundle)["valid"])
        retained = self.sealed()
        over = deepcopy(exact)
        changed = False
        for entry in over["repositories"]:
            for index in range(3, len(entry["command"])):
                value = entry["command"][index]
                if not changed and len(value) < 4096:
                    entry["command"][index] += "x"
                    changed = True
        self.assertTrue(changed)
        destination = self.root / ("q" * len(self.bundle.name))
        with self.assertRaises(ConfigurationError):
            prepare(over, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(self.sealed(), retained)


if __name__ == "__main__":
    unittest.main()
