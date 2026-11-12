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

    def child_configuration(self):
        configuration = deepcopy(self.configuration)
        entry = configuration["repositories"][0]
        entry["model_policy"] = self.model_policy("alpha")
        entry["child_policy"] = {
            "max_children": 2, "max_child_seconds": 20, "max_child_turns": 4,
            "capabilities": ["python"],
        }
        return configuration

    def public_configuration(self):
        configuration = deepcopy(self.configuration)
        self.public_scopes = {}
        for index, entry in enumerate(configuration["repositories"]):
            name = ("alpha", "beta")[index]
            path = self.root / ("scope-" + name + ".key")
            raw = ("private-multiple-scope-" + name + "\n").encode()
            path.write_bytes(raw)
            path.chmod(0o600)
            self.public_scopes[name] = (path, raw)
            entry["model_policy"] = self.model_policy(name)
            entry["command"] = [
                "python3", "-I", "-S", "/opt/openjules/managed_root_runner.py",
                "--socket", "/run/openjules-model/model.sock",
                "--control-socket", "/run/openjules-model/control.sock",
                "--public-socket", "/run/openjules-model/public.sock",
            ]
            entry["child_policy"] = {
                "max_children": 2, "max_child_seconds": 20, "max_child_turns": 4,
                "capabilities": ["python"], "outcome_protocol": "initial-v1",
                "public_discussion": {
                    "protocol": "managed-public-pull-v1", "public_approved": True,
                    "topic": "approved-topic-" + name,
                    "server": {"scope_key_file": str(path)},
                    "personas": {"reviewer": "Independent approved persona for " + name},
                    "instructions": {"question": "Approved public question for " + name},
                    "invitations": {},
                },
            }
        return configuration

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

    def test_public_profiles_derive_distinct_private_bindings_without_new_canonical_fields(self):
        configuration = self.public_configuration()
        original = deepcopy(configuration)
        prepare(configuration, self.bundle)
        self.assertTrue(verify(self.bundle)["valid"])
        self.assertEqual(configuration, original)
        normalized = self.read("manifest.json")["configuration"]
        runtime = self.read("config/openjules.json")
        canonical = self.read("config/polyphony.json")["repositories"]
        paths = set()
        for index, child in enumerate(runtime["children"]):
            name = ("alpha", "beta")[index]
            repository = REPOSITORIES[index]
            self.assertEqual(child["repository"], repository)
            self.assertEqual(child["profile"], "approved")
            self.assertEqual(child["deployment_id"], configuration["deployment_id"])
            public = child["policy"]["public_discussion"]
            source_path, raw = self.public_scopes[name]
            normalized_public = normalized["repositories"][index]["child_policy"]["public_discussion"]
            self.assertEqual(normalized_public["server"], {
                "scope_key_file": str(source_path), "scope_key_sha256": hashlib.sha256(raw).hexdigest(),
            })
            self.assertEqual(public | {"server": normalized_public["server"]}, normalized_public)
            server = public["server"]
            self.assertEqual(server["python"], str(self.bundle / "environments/polyphony/bin/python"))
            self.assertEqual(server["cwd"], configuration["sources"]["polyphony"]["checkout"])
            self.assertNotIn("bootstrap", server)
            copy = Path(server["scope_key_file"])
            self.assertEqual(copy.parent, self.bundle / "secrets")
            self.assertEqual(copy.read_bytes(), raw)
            self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
            self.assertEqual(copy.stat().st_nlink, 1)
            self.assertNotIn(copy, paths)
            paths.add(copy)
            entry = configuration["repositories"][index]
            self.assertEqual(canonical[repository]["profiles"]["approved"], {
                "base": entry["source"]["revision"], "attempt_seconds": entry["policy"]["attempt_seconds"],
                "capabilities": ["python"], "max_children": 2, "max_child_seconds": 20,
                "max_child_turns": 4, "child_capabilities": ["python"],
            })
            self.assertEqual(runtime["profiles"][index]["command"], entry["command"])
        self.assertEqual(len(paths), 2)
        self.assertEqual(self.read("config/provider.json")["max_workers"], 8)
        for filename, data in self.sealed().items():
            if self.bundle / filename not in paths:
                self.assertNotIn(b"private-multiple-scope-", data, filename)

    def test_public_profiles_validate_both_entries_before_any_publication(self):
        configuration = self.public_configuration()
        with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("partly prepared invalid profiles")), \
                patch("zpm.deployment.Store", side_effect=AssertionError("partly enrolled invalid profiles")):
            for index in (0, 1):
                for failure in ("null", "approval", "hash", "missing-file", "cold", "root"):
                    bad = deepcopy(configuration)
                    entry = bad["repositories"][index]
                    public = entry["child_policy"]["public_discussion"]
                    if failure == "null":
                        entry["child_policy"]["public_discussion"] = None
                    elif failure == "approval":
                        public["public_approved"] = 1
                    elif failure == "hash":
                        public["server"]["scope_key_sha256"] = "0" * 64
                    elif failure == "missing-file":
                        public["server"]["scope_key_file"] = str(self.root / "missing-scope")
                    elif failure == "cold":
                        entry["child_policy"]["session_protocol"] = "cold-clarification-v1"
                    else:
                        entry["command"] = ["python3", "unapproved-root.py"]
                    with self.subTest(repository=index, failure=failure), self.assertRaises(ConfigurationError):
                        prepare(bad, self.bundle)
                    self.assertFalse(self.bundle.exists())

    def test_public_and_cold_or_omitted_profiles_remain_separate_in_one_service(self):
        configuration = self.public_configuration()
        for mode in ("omitted", "cold"):
            with self.subTest(mode=mode):
                bundle = self.root / ("mixed-" + mode)
                selected = deepcopy(configuration)
                beta = selected["repositories"][1]
                beta["command"] = self.entries[1]["command"]
                if mode == "omitted":
                    beta.pop("child_policy")
                else:
                    beta["child_policy"].pop("public_discussion")
                    beta["child_policy"]["session_protocol"] = "cold-clarification-v1"
                normalized = parse_configuration(selected)
                prepare(selected, bundle)
                self.assertTrue(verify(bundle)["valid"])
                runtime = self.read("config/openjules.json", bundle)
                children = {item["repository"]: item["policy"] for item in runtime["children"]}
                self.assertIn("public_discussion", children[REPOSITORIES[0]])
                self.assertNotIn("session_protocol", children[REPOSITORIES[0]])
                if mode == "omitted":
                    self.assertNotIn(REPOSITORIES[1], children)
                    self.assertNotIn("child_policy", normalized["repositories"][1])
                else:
                    self.assertEqual(children[REPOSITORIES[1]], normalized["repositories"][1]["child_policy"])
                    self.assertEqual(children[REPOSITORIES[1]]["session_protocol"], "cold-clarification-v1")
                    self.assertNotIn("public_discussion", children[REPOSITORIES[1]])
                self.assertEqual(runtime["profiles"][1]["command"], self.entries[1]["command"])
                self.assertEqual(len(list((bundle / "secrets").glob("public-scope-*.key"))), 1)
                beta_canonical = self.read("config/polyphony.json", bundle)["repositories"][REPOSITORIES[1]]["profiles"]["approved"]
                self.assertNotIn("public_discussion", beta_canonical)
                self.assertNotIn("session_protocol", beta_canonical)
                self.assertEqual(self.read("config/provider.json", bundle)["max_workers"], 8)

    def test_public_reordering_reuses_exact_seals_and_cannot_reset_removed_state(self):
        configuration = self.public_configuration()
        prepare(configuration, self.bundle)
        with Store(self.bundle / "state/zpm.sqlite3") as store:
            store.change(REPOSITORIES[0], "target", 2)
            store.change(REPOSITORIES[0], "remove")
            store.change(REPOSITORIES[1], "pause")
        retained = self.retained_rows()
        sealed = self.sealed()
        capture = self.bundle / "state/openjules/retained-public-capture"
        capture.write_bytes(b"retained exact capture")
        reordered = deepcopy(configuration)
        reordered["repositories"].reverse()
        # Supplying the already normalized hash does not rotate the original binding.
        for entry in reordered["repositories"]:
            path = entry["child_policy"]["public_discussion"]["server"]["scope_key_file"]
            entry["child_policy"]["public_discussion"]["server"]["scope_key_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        with patch("zpm.deployment.Store", side_effect=AssertionError("rewrote retained journal")), \
                patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("rotated retained credentials")):
            self.assertTrue(prepare(reordered, self.bundle)["reused"])
            for index in (0, 1):
                changed = deepcopy(configuration)
                changed["repositories"][index]["child_policy"]["public_discussion"]["topic"] += "-changed"
                with self.subTest(repository=index), self.assertRaisesRegex(ConfigurationError, "different configuration"):
                    prepare(changed, self.bundle)
            beta_path, beta_raw = self.public_scopes["beta"]
            beta_path.unlink()
            try:
                with self.assertRaises(ConfigurationError):
                    prepare(reordered, self.bundle)
                with self.assertRaises(ConfigurationError):
                    verify(self.bundle)
            finally:
                beta_path.write_bytes(beta_raw)
                beta_path.chmod(0o600)
        self.assertEqual(self.sealed(), sealed)
        self.assertEqual(self.retained_rows(), retained)
        self.assertEqual(capture.read_bytes(), b"retained exact capture")
        self.assertTrue(verify(self.bundle)["valid"])

    def test_cold_child_sessions_remain_scoped_to_the_opted_in_repository(self):
        configuration = self.child_configuration()
        entry = configuration["repositories"][0]
        entry["child_policy"].update({
            "outcome_protocol": "initial-v1", "session_protocol": "cold-clarification-v1",
        })
        prepare(configuration, self.bundle)
        self.assertTrue(verify(self.bundle)["valid"])
        normalized = self.read("manifest.json")["configuration"]["repositories"]
        children = self.read("config/openjules.json")["children"]
        self.assertEqual(children, [{
            "deployment_id": configuration["deployment_id"], "repository": REPOSITORIES[0],
            "profile": "approved", "policy": normalized[0]["child_policy"],
        }])
        self.assertEqual(children[0]["policy"]["session_protocol"], "cold-clarification-v1")
        self.assertEqual(children[0]["policy"]["outcome_protocol"], "initial-v1")
        self.assertNotIn("child_policy", normalized[1])
        canonical = self.read("config/polyphony.json")["repositories"]
        self.assertEqual(canonical[REPOSITORIES[0]]["profiles"]["approved"], {
            "base": entry["source"]["revision"], "attempt_seconds": 60, "capabilities": ["python"],
            "max_children": 2, "max_child_seconds": 20, "max_child_turns": 4, "child_capabilities": ["python"],
        })
        self.assertEqual(set(canonical[REPOSITORIES[1]]["profiles"]["approved"]),
                         {"base", "attempt_seconds", "capabilities"})
        self.assertEqual(self.read("config/provider.json")["max_workers"], configuration["max_workers"])

    def test_cold_child_sessions_validate_each_repository_before_publishing(self):
        configuration = self.child_configuration()
        variants = [
            {"outcome_protocol": "initial-v1", "session_protocol": value}
            for value in (None, False, True, 1, 1.0, "", "future-v2", "cold-clarification-v1 ", [], {})
        ]
        variants.extend((
            {"session_protocol": "cold-clarification-v1"},
            {"session_protocol": "cold-clarification-v1", "outcome_protocol": None},
            {"session_protocol": "cold-clarification-v1", "outcome_protocol": "initial-v1", "max_children": 1},
            {"session_protocol": "cold-clarification-v1", "outcome_protocol": "initial-v1",
             "command": ["python3", "custom.py"]},
        ))
        # Exercise both ordered entries, including rejection after a valid first
        # repository was parsed. No partial installation may be published.
        for index in (0, 1):
            for variant in variants:
                bad = deepcopy(configuration)
                entry = bad["repositories"][index]
                entry["model_policy"] = self.model_policy(("alpha", "beta")[index])
                entry["child_policy"] = configuration["repositories"][0]["child_policy"] | variant
                with self.subTest(repository=index, variant=variant), self.assertRaises(ConfigurationError):
                    prepare(bad, self.bundle)
                self.assertFalse(self.bundle.exists())

    def test_cold_child_session_opt_in_preserves_immutable_multi_repository_bundles(self):
        configuration = self.child_configuration()
        configuration["repositories"][0]["child_policy"]["outcome_protocol"] = "initial-v1"
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                bundle = self.root / ("sessions-" + str(enabled))
                selected = deepcopy(configuration)
                policy = selected["repositories"][0]["child_policy"]
                if enabled:
                    policy["session_protocol"] = "cold-clarification-v1"
                prepare(selected, bundle)
                sealed = self.sealed(bundle)
                if not enabled:
                    normalized = self.read("manifest.json", bundle)["configuration"]["repositories"]
                    self.assertEqual(normalized[0]["child_policy"], {
                        "max_children": 2, "max_child_seconds": 20, "max_child_turns": 4,
                        "capabilities": ["python"], "uid_start": 60000,
                        "command": ["python3", "-I", "-S", "/opt/openjules/managed_runner.py"],
                        "verify_argv": [], "max_result_bytes": 4_000_000, "outcome_protocol": "initial-v1",
                    })
                    self.assertNotIn(b'"session_protocol"', sealed["config/openjules.json"])
                with Store(bundle / "state/zpm.sqlite3") as store:
                    store.change(REPOSITORIES[0], "target", 1)
                    store.change(REPOSITORIES[0], "remove")
                retained_rows = self.retained_rows(bundle)
                artifact = bundle / "state/openjules/retained-session-evidence"
                artifact.write_bytes(b"retained capture")
                changed = deepcopy(selected)
                if enabled:
                    changed["repositories"][0]["child_policy"].pop("session_protocol")
                else:
                    changed["repositories"][0]["child_policy"]["session_protocol"] = "cold-clarification-v1"
                reordered = deepcopy(selected)
                reordered["repositories"].reverse()
                with patch("zpm.deployment.secrets.token_urlsafe", side_effect=AssertionError("credential rotation")), \
                        patch("zpm.deployment.Store", side_effect=AssertionError("state mutation")):
                    self.assertTrue(prepare(reordered, bundle)["reused"])
                    with self.assertRaisesRegex(ConfigurationError, "different configuration"):
                        prepare(changed, bundle)
                self.assertEqual(self.sealed(bundle), sealed)
                self.assertEqual(self.retained_rows(bundle), retained_rows)
                self.assertEqual(artifact.read_bytes(), b"retained capture")

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
