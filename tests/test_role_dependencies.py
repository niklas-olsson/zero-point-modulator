"""Versioned sealed inputs retain the exact declared installation role."""

from copy import deepcopy
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from tests import test_dependencies
from zpm import dependencies, deployment
from zpm.models import ConfigurationError


class RoleDependencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_dependencies.DependencyTests()
        self.fixture.setUp()
        self.tokens = {"ZPM_INSTALL_RUNTIME_A": "a" * 48, "ZPM_INSTALL_LIFECYCLE_A": "b" * 48}

    def tearDown(self):
        self.fixture.tearDown()

    def configuration(self, role):
        original = self.fixture.config
        selected = ("zpm", "polyphony") if role == "controller-authority" else ("zpm", "openjules")
        value = {"schema_version": 3, "role": role, "deployment_id": "sealed-role-test",
                 "sources": {name: self.fixture.sources[name] for name in selected},
                 "binding": original["binding"] | {"repository": "github:github.com/owner/project"}}
        if role == "controller-authority":
            value.update(base_commit=self.fixture.sources["source"]["revision"], policy=original["policy"], max_workers=2,
                runtime_principals=[{"identity": "runtime-a", "token_ref": "env:ZPM_INSTALL_RUNTIME_A"}],
                destinations=[{"destination_id": "host-a", "token_ref": "env:ZPM_INSTALL_LIFECYCLE_A"}])
        else:
            value.update(source=self.fixture.sources["source"], image=original["image"], command=original["command"], runtime={},
                destination_id="host-a", worker_ceiling=1, authority_endpoint="http://127.0.0.1:8766", runtime_principal="runtime-a",
                runtime_token_ref="env:ZPM_INSTALL_RUNTIME_A", lifecycle_token_ref="env:ZPM_INSTALL_LIFECYCLE_A")
        plan = deepcopy(self.fixture.plan)
        plan["schema_version"] = 2
        plan["environments"] = {name: plan["environments"][name] for name in selected}
        self.fixture.save_plan(plan)
        value["dependency_plan"] = deepcopy(self.fixture.config["dependency_plan"])
        return value, plan

    def test_both_role_plan_versions_seal_only_their_selected_sources_and_roots(self):
        for role in ("controller-authority", "execution"):
            with self.subTest(role=role):
                value, _ = self.configuration(role)
                directory = self.fixture.root / role
                with patch.dict(os.environ, self.tokens), patch.object(dependencies, "_run", side_effect=AssertionError("package execution during prepare")):
                    report = deployment.prepare(value, directory)
                    self.assertTrue(deployment.verify(directory)["valid"])
                self.assertEqual(report["dependency_mode"], "sealed-local-wheels-v2")
                plan = json.loads((directory / "dependencies/plan.json").read_text())
                self.assertEqual(set(plan["environments"]), set(value["sources"]))
                roots = {path.name.removesuffix("-roots.json") for path in (directory / "dependencies").glob("*-roots.json")}
                self.assertEqual(roots, set(value["sources"]))
                self.assertFalse((directory / "environments/sealed-intent.json").exists())

    def test_role_plan_rejects_legacy_version_foreign_sources_environments_and_unused_artifacts(self):
        value, original = self.configuration("execution")
        for change in ("version", "environment", "missing", "unused"):
            plan = deepcopy(original)
            if change == "version":
                plan["schema_version"] = 1
            elif change == "environment":
                plan["environments"]["polyphony"] = self.fixture.plan["environments"]["polyphony"]
            elif change == "missing":
                del plan["environments"]["openjules"]
            else:
                for row in plan["environments"].values():
                    row["wheels"].remove("fixture_package-1.0-py3-none-any.whl")
            self.fixture.save_plan(plan)
            value["dependency_plan"] = self.fixture.config["dependency_plan"]
            with self.subTest(change=change), self.assertRaises(ConfigurationError):
                deployment.parse_configuration(value)
        self.fixture.save_plan(original)
        with self.assertRaises(ConfigurationError):
            dependencies.normalize_reference(self.fixture.config["dependency_plan"], self.fixture.config["sources"], role="execution")

    def test_legacy_plan_still_requires_all_three_sources_and_version_one(self):
        self.fixture.save_plan(self.fixture.plan)
        reference = self.fixture.config["dependency_plan"]
        self.assertEqual(dependencies.normalize_reference(reference, self.fixture.config["sources"]), reference)
        with self.assertRaises(ConfigurationError):
            dependencies.normalize_reference(reference, {key: value for key, value in self.fixture.config["sources"].items() if key != "polyphony"})
        version_two = deepcopy(self.fixture.plan) | {"schema_version": 2}
        self.fixture.save_plan(version_two)
        with self.assertRaises(ConfigurationError):
            dependencies.normalize_reference(self.fixture.config["dependency_plan"], self.fixture.config["sources"])


if __name__ == "__main__":
    unittest.main()
