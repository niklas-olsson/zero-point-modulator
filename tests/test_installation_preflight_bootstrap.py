"""Opted-in bootstrap import controls, including Python's isolated mode."""

from copy import deepcopy
import builtins
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_dependencies
from zpm import dependencies, deployment, installation_context, installation_preflight


class StopBeforeImport(Exception):
    pass


class PreflightBootstrapTests(unittest.TestCase):
    setUp = test_dependencies.DependencyTests.setUp
    tearDown = test_dependencies.DependencyTests.tearDown
    save_plan = test_dependencies.DependencyTests.save_plan

    def prepare_case(self, selected):
        value = deepcopy(self.config)
        if selected:
            value["installation_admission"] = installation_preflight.CONTRACT
        bundle = self.root / ("selected" if selected else "legacy")
        deployment.prepare(value, bundle)
        return bundle, deployment.parse_configuration(value)

    def test_first_sealed_import_gets_explicit_cache_flags_only_when_selected(self):
        for selected in (False, True):
            bundle, data = self.prepare_case(selected)
            events, imported = [], []
            def run(command, cwd, environment, *args, **kwargs):
                if any("importlib.import_module" in part for part in command):
                    events.append("import")
                    imported.append((command, cwd, environment))
                    raise StopBeforeImport
                return ""
            with self.subTest(selected=selected), \
                    patch.object(installation_preflight, "guard", side_effect=lambda *_: events.append("guard")), \
                    patch.object(dependencies, "_completion", side_effect=lambda root, data, service, *args, **kwargs: service == "zpm"), \
                    patch.object(dependencies, "_installed", return_value={}), \
                    patch.object(dependencies, "_run", side_effect=run), \
                    self.assertRaises(StopBeforeImport):
                dependencies.bootstrap(bundle, data, {"PATH": os.defpath, "PYTHONPATH": "/private/import",
                                                     "PROVIDER_TOKEN": "private-canary"})
            command, cwd, environment = imported[0]
            self.assertIn("-I", command)
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("PROVIDER_TOKEN", environment)
            if selected:
                self.assertEqual(events[-2:], ["guard", "import"])
                self.assertEqual(command[1:4], ["-I", "-B", "-X"])
                self.assertEqual(command[4], "pycache_prefix=" + str(cwd / "unused-bytecode"))
                self.assertIn("sys.path.append", command[6])
                self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
                self.assertEqual(environment["PYTHONPYCACHEPREFIX"], str(cwd / "unused-bytecode"))
            else:
                self.assertEqual(events, ["import"])
                self.assertNotIn("-B", command)
                self.assertNotIn("-X", command)
                self.assertIn("sys.path.insert(0", command[3])

    def test_completed_sealed_bootstrap_context_probe_keeps_fresh_cache_controls(self):
        for selected in (False, True):
            bundle, data = self.prepare_case(selected)
            probes = []
            def context(root, checkout, environment, **kwargs):
                probes.append(dict(environment))
            with self.subTest(selected=selected), \
                    patch.object(installation_preflight, "guard") as guard, \
                    patch.object(installation_context, "completed", return_value=True), \
                    patch.object(installation_context, "probe", side_effect=context), \
                    patch.object(dependencies, "_completion", return_value=True), \
                    patch.object(dependencies, "_run", side_effect=AssertionError("completed bootstrap executed package command")):
                result = dependencies.bootstrap(bundle, data, {"PATH": os.defpath, "PYTHONPATH": "/private/import"})
            self.assertTrue(result["installed"])
            self.assertEqual(len(probes), 1)
            if selected:
                guard.assert_called_once_with(bundle, data)
                self.assertEqual(probes[0]["PYTHONDONTWRITEBYTECODE"], "1")
                prefix = Path(probes[0]["PYTHONPYCACHEPREFIX"])
                self.assertEqual(prefix.parent, bundle / "environments")
                self.assertTrue(prefix.name.startswith(".installation-bytecode-"))
                self.assertNotIn("PYTHONPATH", probes[0])
            else:
                guard.assert_not_called()
                self.assertEqual(probes[0]["PYTHONPATH"], "/private/import")
                self.assertNotIn("PYTHONPYCACHEPREFIX", probes[0])

    def test_absent_option_bootstrap_does_not_import_new_preflight_module(self):
        bundle, data = self.prepare_case(False)
        original_import = builtins.__import__
        def imports(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith("installation_preflight") or "installation_preflight" in (fromlist or ()):
                raise AssertionError("omitted-option dependency bootstrap imported preflight")
            return original_import(name, globals, locals, fromlist, level)
        with patch.object(builtins, "__import__", side_effect=imports), \
                patch.object(installation_context, "completed", return_value=True), \
                patch.object(installation_context, "probe"), \
                patch.object(dependencies, "_completion", return_value=True), \
                patch.object(dependencies, "_run", side_effect=AssertionError("completed bootstrap executed package command")):
            self.assertTrue(dependencies.bootstrap(bundle, data, {"PATH": os.defpath})["installed"])

    def test_raw_context_probe_environment_is_conditional_and_never_reuses_prefix(self):
        for selected in (False, True):
            bundle, data = self.prepare_case(selected)
            first = deployment._environment(bundle, "bootstrap", data)
            second = deployment._environment(bundle, "bootstrap", data)
            with self.subTest(selected=selected):
                if selected:
                    self.assertEqual(first["PYTHONDONTWRITEBYTECODE"], "1")
                    self.assertNotEqual(first["PYTHONPYCACHEPREFIX"], second["PYTHONPYCACHEPREFIX"])
                    self.assertEqual(Path(first["PYTHONPYCACHEPREFIX"]).parent, bundle / "environments")
                    self.assertFalse(Path(first["PYTHONPYCACHEPREFIX"]).exists())
                else:
                    self.assertNotIn("PYTHONPYCACHEPREFIX", first)

    def test_ranged_install_disables_pip_compilation_only_for_selected_recipe(self):
        for selected in (False, True):
            value = deepcopy(self.config)
            value.pop("dependency_plan")
            if selected:
                value["installation_admission"] = installation_preflight.CONTRACT
            bundle = self.root / ("ranged-selected" if selected else "ranged-legacy")
            deployment.prepare(value, bundle)
            data = deployment.parse_configuration(value)
            installs = []
            def run(command, **kwargs):
                if command[1:3] == ["-m", "venv"]:
                    Path(command[-1]).mkdir(mode=0o700)
                elif command[1:4] == ["-m", "pip", "install"]:
                    installs.append((command, kwargs["env"]))
                return SimpleNamespace(stdout="fixture==1\n")
            with self.subTest(selected=selected), \
                    patch.object(installation_context, "completed", return_value=False), \
                    patch.object(deployment, "_installation_context"), \
                    patch.object(deployment.subprocess, "run", side_effect=run):
                result = deployment._bootstrap_locked(bundle, data)
            self.assertTrue(result["installed"])
            self.assertEqual(len(installs), 3)
            for command, environment in installs:
                self.assertEqual("--no-compile" in command, selected)
                if selected:
                    self.assertEqual(Path(environment["PYTHONPYCACHEPREFIX"]).parent, bundle / "environments")


if __name__ == "__main__":
    unittest.main()
