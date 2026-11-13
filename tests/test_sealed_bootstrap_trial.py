"""The installed fixture must prove network denial without privileged code."""

from __future__ import annotations

import errno
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile


def load(name):
    path = Path(__file__).parent / "contracts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stack = load("check_stack")
denied = load("denied_bootstrap")
acquisition = load("prepare_dependencies")


class SealedBootstrapTrialTests(unittest.TestCase):
    def test_explicit_online_acquisition_keeps_proxy_routing_without_ambient_pip_policy(self):
        supplied = {"HTTPS_PROXY": "http://approved-proxy.invalid:8080", "no_proxy": "127.0.0.1",
                    "ALL_PROXY": "http://approved-fallback.invalid:8080", "PATH": "/usr/bin",
                    "PIP_CONFIG_FILE": "/unapproved/pip.conf", "PIP_INDEX_URL": "https://unapproved.invalid",
                    "PYTHONPATH": "/unapproved/modules"}
        with patch.dict(acquisition.os.environ, supplied, clear=True):
            result = acquisition.acquisition_environment(Path("/private/home"), Path("/private/tmp"))
        for key in ("HTTPS_PROXY", "ALL_PROXY", "no_proxy", "PATH"):
            self.assertEqual(result[key], supplied[key])
        self.assertEqual(result["PIP_CONFIG_FILE"], acquisition.os.devnull)
        self.assertNotIn("PIP_INDEX_URL", result)
        self.assertNotIn("PYTHONPATH", result)
        self.assertEqual(result["HOME"], "/private/home")

    def test_default_bootstrap_still_uses_existing_manager(self):
        self.assertEqual(stack.bootstrap_command(Path("/bundle"), Path("/trial"), "bootstrap", deny_network=False),
                         [sys.executable, "/bundle/manage.py", "bootstrap"])

    def test_privileges_drop_before_any_candidate_python_executes(self):
        with patch.object(stack.os, "getuid", return_value=1001), \
                patch.object(stack.os, "getgid", return_value=1002), \
                patch.object(stack.os, "readlink", side_effect=["net:[123]", "pid:[234]"]), \
                patch.object(Path, "is_file", return_value=True):
            command = stack.bootstrap_command(Path("/bundle"), Path("/trial"), "bootstrap", deny_network=True)
        python_position = command.index(sys.executable)
        self.assertEqual(command[:11], ["/usr/bin/sudo", "-n", "--", "/usr/bin/unshare", "--net", "--pid",
                                       "--fork", "--kill-child=SIGKILL", "--mount-proc=/proc", "--propagation=private", "--"])
        for argument in ("/usr/bin/setpriv", "--reuid=1001", "--regid=1002", "--clear-groups",
                         "--no-new-privs", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all"):
            self.assertLess(command.index(argument), python_position)
        self.assertEqual(command[python_position + 1], "-I")
        self.assertIn("/trial/bootstrap-network.json", command)
        self.assertIn("pid:[234]", command)

    def test_network_trial_cannot_run_as_root(self):
        with patch.object(stack.os, "getuid", return_value=0):
            with self.assertRaisesRegex(AssertionError, "ordinary runner"):
                stack.bootstrap_command(Path("/bundle"), Path("/trial"), "bootstrap", deny_network=True)

    def _network_patches(self, *, namespace="net:[456]", interfaces=None, result=errno.ENETUNREACH, capabilities="0"):
        status = "NoNewPrivs:\t1\n" + "".join(name + ":\t" + capabilities + "\n" for name in
                                                ("CapEff", "CapPrm", "CapAmb", "CapBnd"))
        client = MagicMock()
        client.__enter__.return_value.connect_ex.return_value = result
        return [patch.object(denied.os, "getuid", return_value=1001),
                patch.object(denied.os, "geteuid", return_value=1001),
                patch.object(denied.os, "getgid", return_value=1002),
                patch.object(denied.os, "getegid", return_value=1002),
                patch.object(denied.os, "getgroups", return_value=[]),
                patch.object(denied.os, "readlink", return_value=namespace),
                patch.object(denied.Path, "read_text", side_effect=[status, "Iface Destination Gateway\n"]),
                patch.object(denied.socket, "if_nameindex", return_value=interfaces or [(1, "lo")]),
                patch.object(denied.socket, "socket", return_value=client)]

    def test_namespace_evidence_refuses_routes_privileges_and_successful_connections(self):
        from contextlib import ExitStack
        for options in ({"namespace": "net:[123]"}, {"interfaces": [(1, "lo"), (2, "eth0")]},
                        {"result": 0}, {"result": errno.ECONNREFUSED}, {"capabilities": "1"}):
            with self.subTest(options=options), ExitStack() as context:
                for item in self._network_patches(**options):
                    context.enter_context(item)
                with self.assertRaises(ValueError):
                    denied.network_evidence("net:[123]", 1001, 1002)

    def test_network_evidence_requires_actual_unreachable_results(self):
        from contextlib import ExitStack
        with ExitStack() as context:
            for item in self._network_patches():
                context.enter_context(item)
            result = denied.network_evidence("net:[123]", 1001, 1002)
        self.assertTrue(result["network_namespace_distinct"])
        self.assertEqual(len(result["ip_probes"]), 3)
        self.assertEqual({item["errno"] for item in result["ip_probes"]}, {errno.ENETUNREACH})

    def test_acquisition_binds_wheel_metadata_and_bytes_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "demo_pkg-1.2-py3-none-any.whl"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("demo_pkg-1.2.dist-info/METADATA", "Name: demo_pkg\nVersion: 1.2\n")
                archive.writestr("demo_pkg/_vendor/helper-2.dist-info/METADATA", "Name: helper\nVersion: 2\n")
                archive.writestr("demo_pkg.py", "raise RuntimeError('must never execute during acquisition')\n")
            result = acquisition.wheel_record(path)
            self.assertEqual(result["name"], "demo-pkg")
            self.assertEqual(result["version"], "1.2")
            self.assertEqual(result["size"], path.stat().st_size)
            original = result["sha256"]
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("extra.txt", "changed")
            self.assertNotEqual(acquisition.wheel_record(path)["sha256"], original)
            linked = Path(temporary) / "linked.whl"
            linked.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "linked"):
                acquisition.wheel_record(linked)

    def test_public_receipt_omits_installed_paths_and_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for service in ("zpm", "openjules", "polyphony"):
                directory = root / "environments" / service
                directory.mkdir(parents=True)
                receipt = {"plan_sha256": "a" * 64, "source_revision": "b" * 40,
                           "interpreter": {"implementation": "cpython"},
                           "installed": {"example": {"version": "1.0", "files_sha256": "c" * 64}}}
                (directory / "sealed-complete.json").write_text(json.dumps(receipt))
            result = stack.sealed_report(root, {"sha256": "a" * 64}, root, deny_network=False)
            self.assertEqual(result["environments"]["zpm"]["distribution_count"], 1)
            self.assertNotIn("example", json.dumps(result))
            self.assertNotIn(str(root), json.dumps(result))
            self.assertFalse(result["bootstrap_ip_network_denied"])

    def test_timeout_keeps_proven_isolation_and_failed_outcome(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            manager = bundle / "manage.py"
            manager.write_text("raise AssertionError('fixture must not execute')")
            evidence = root / "bootstrap-network.json"
            with patch.object(denied, "network_evidence", return_value={"network_namespace_distinct": True}), \
                    patch.object(denied.subprocess, "run", side_effect=subprocess.TimeoutExpired(["bootstrap"], 870)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    denied.run(manager, bundle, evidence, "net:[123]", 1001, 1002, "pid:[234]")
            report = json.loads(evidence.read_text())
            self.assertTrue(report["network_namespace_distinct"])
            self.assertEqual(report["bootstrap_outcome"], "timed_out")
            self.assertIsNone(report["bootstrap_returncode"])
            self.assertGreater(report["elapsed_ns"], 0)
            self.assertIn("peak_waited_child_rss_bytes", report)

    def test_disk_inventory_never_follows_external_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.write_bytes(b"private external bytes")
            installation = root / "installation"
            installation.mkdir()
            (installation / "actual").write_bytes(b"hello")
            (installation / "linked").symlink_to(outside)
            report = denied.storage_bytes(installation)
            self.assertEqual(report["logical_regular_file_bytes"], 5)
            self.assertEqual(report["symlinks_not_followed"], 1)

    def test_online_acquisition_stops_and_reaps_excessive_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(acquisition, "MAX_COMMAND_OUTPUT", 1024):
                with self.assertRaisesRegex(ValueError, "output bound"):
                    acquisition.bounded_acquisition(
                        [sys.executable, "-c", "import sys;sys.stdout.write('x'*65536);sys.stdout.flush()"],
                        {}, root / "log", root)
            self.assertLessEqual((root / "log").stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
