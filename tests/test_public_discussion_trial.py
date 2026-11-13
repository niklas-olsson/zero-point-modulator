"""Real dedicated-process containment and explicit installed-scene oracles.

These controls do not qualify a provider, native transport or installed stack.
Subreaping is confined to the helper process, never this shared unittest host.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest

from tests.contracts import check_public_discussion_stack as trial


class InstalledProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.revisions = {name: character * 40 for name, character in
                          (("zpm", "1"), ("openjules", "2"), ("polyphony", "3"))}
        self.report = {"passed": True, "final_reserved": 0,
                       "installation_bootstrap_and_doctor": "passed",
                       "component_revisions": dict(self.revisions)}
        self.configuration = {"sources": {name: {"revision": value} for name, value in self.revisions.items()}}
        self.markers = {name: {"revision": value, "python": "explicit fixture"} for name, value in self.revisions.items()}

    def check(self):
        trial.installation_agreement(self.report, self.configuration, self.markers, self.revisions)

    def test_successful_same_revision_set(self):
        self.check()

    def test_stale_successful_report_is_not_current_installation_evidence(self):
        self.report["component_revisions"]["polyphony"] = "4" * 40
        with self.assertRaisesRegex(AssertionError, "another revision set"):
            self.check()

    def test_old_environment_or_rewritten_manifest_is_refused(self):
        for item in (self.markers["openjules"], self.configuration["sources"]["openjules"]):
            with self.subTest(item=item):
                item["revision"] = "5" * 40
                with self.assertRaisesRegex(AssertionError, "source or bootstrap marker"):
                    self.check()
                item["revision"] = self.revisions["openjules"]

    def test_unfinished_managed_trial_cannot_be_relabelled_as_installed(self):
        for field, value in (("passed", False), ("final_reserved", 1),
                             ("installation_bootstrap_and_doctor", "pending")):
            with self.subTest(field=field):
                old = self.report[field]
                self.report[field] = value
                with self.assertRaisesRegex(AssertionError, "successful stopped"):
                    self.check()
                self.report[field] = old

    def test_remote_record_and_gap_oracles_preserve_original_evidence(self):
        status = {"stream_id": "fixture-stream"}
        original = {"body": {"content": "retained dissent", "reply_to": None}, "signature": "original"}
        value = {"status": status, "history": {"gap": None, "peer_gaps": {"count": 0, "items": []},
                 "has_more": False, "next_cursor": 2, "messages": [{"record": original, "reply_state": "none"}]}}
        self.assertEqual(trial.check_page(value, status, 1, [original]), 2)
        changed = deepcopy(value)
        changed["history"]["messages"][0]["record"]["signature"] = "rewritten"
        with self.assertRaisesRegex(AssertionError, "exact original"):
            trial.check_page(changed, status, 1, [original])
        changed = deepcopy(value)
        changed["history"]["gap"] = {"reason": "stream_changed"}
        with self.assertRaisesRegex(AssertionError, "gap"):
            trial.check_page(changed, status, 1, [original])
        changed = deepcopy(value)
        changed["history"]["messages"][0]["reply_state"] = "unresolved"
        with self.assertRaisesRegex(AssertionError, "reply relationships"):
            trial.check_page(changed, status, 1, [original])


class ExactProviderRequestTests(unittest.TestCase):
    def setUp(self):
        # Exercise the actual oracle without constructing a listener or model.
        self.fixture = object.__new__(trial.ProviderFixture)
        self.fixture.tokens = {"alpha": "explicit-test-token"}
        self.fixture.calls, self.fixture.error, self.fixture.expected = [], None, None
        self.fixture.lock = threading.Lock()
        self.turn = {"request_key": "proposal", "instruction": "explicit fixture instruction"}
        self.context = {"status": {"author": "fixture-author"}, "messages": [],
                        "peer_gaps": [{"count": 0, "items": []}], "context_after": 0, "next_cursor": 0}
        self.output = {"kind": "decline", "content": "retained dissent"}
        self.fixture.expect("alpha", self.turn, self.context, self.output)
        self.request = {"model": "deterministic-public-discussion-fixture", "stream": False, "max_tokens": 128,
                        "messages": [{"role": "system", "content": trial.SYSTEM_MESSAGE + "Fixture persona alpha"},
                                     {"role": "user", "content": trial.canonical({
                                         "instruction": self.turn["instruction"], "public_context": self.context}).decode()}]}

    def test_complete_approved_request_is_counted_once(self):
        self.assertEqual(self.fixture.respond("Bearer explicit-test-token", self.request), self.output)
        self.fixture.check(1)
        with self.assertRaisesRegex(AssertionError, "unsolicited"):
            self.fixture.respond("Bearer explicit-test-token", self.request)

    def test_extra_context_message_fields_or_system_content_are_refused(self):
        variants = []
        value = deepcopy(self.request)
        prompt = json.loads(value["messages"][1]["content"])
        prompt["public_context"]["private_canary"] = "must not be approved"
        value["messages"][1]["content"] = trial.canonical(prompt).decode()
        variants.append(value)
        value = deepcopy(self.request)
        value["messages"][0]["content"] = "unapproved private prefix" + value["messages"][0]["content"]
        variants.append(value)
        value = deepcopy(self.request)
        value["messages"][1]["private_canary"] = "must not be approved"
        variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaisesRegex(AssertionError, "exact approved public request"):
                    self.fixture.respond("Bearer explicit-test-token", value)
                self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.fixture.respond("Bearer explicit-test-token", self.request), self.output)
        self.fixture.check(1)


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux containment qualification")
class DedicatedContainmentTests(unittest.TestCase):
    def probe(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                         start_new_session=True, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            script = textwrap.dedent(r'''
                import importlib.util,json,os,signal,sys,time
                from pathlib import Path
                spec=importlib.util.spec_from_file_location("trial",sys.argv[1])
                trial=importlib.util.module_from_spec(spec); spec.loader.exec_module(trial)
                mode=sys.argv[2]; directory=Path(sys.argv[3])
                owner=trial.OwnedTrial(dedicated_process=True)
                failed=False; reason=None; entry=None; metadata_original=None
                cleanup_rejected=False; cleanup_reason=None
                try:
                    if mode in {"normal", "persistent-stat-loss"}:
                        source="print('normal',flush=True)" if mode == "normal" else "import time; time.sleep(30)"
                    else:
                        leaf="import os,time,json,signal; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path("+repr(str(directory/"leaf.json"))+").write_text(json.dumps({'pid':os.getpid(),'starttime':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]})); time.sleep(30)"
                        source="import os,sys,time,subprocess; from pathlib import Path; subprocess.Popen([sys.executable,'-c',"+repr(leaf)+"],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                        source+="\nwhile not Path("+repr(str(directory/"leaf.json"))+").exists(): time.sleep(.01)\n"
                        source+=("os._exit(7)" if mode == "orphan" else "time.sleep(30)")
                    if mode == "launch-observation":
                        original=trial.process_info
                        first=[True]
                        def missing_once(pid):
                            if first[0]: first[0]=False; return None
                            return original(pid)
                        trial.process_info=missing_once
                    entry=owner.launch("controlled",[sys.executable,"-c",source],directory,trial.PROFILE_ENVIRONMENT)
                    if mode == "persistent-stat-loss":
                        metadata_original=trial.process_info
                        trial.process_info=lambda pid: None
                        owner.deadline=time.monotonic()-1
                    if mode == "expired":
                        cutoff=time.monotonic()+3
                        while not (directory/"leaf.json").exists():
                            assert time.monotonic()<cutoff
                            time.sleep(.01)
                        owner.deadline=time.monotonic()-1
                    result=owner.wait([entry],3)[0]
                    assert result['returncode']==0
                except AssertionError as error:
                    failed=True; reason=str(error)
                finally:
                    started=time.monotonic()
                    try:
                        owner.cleanup(started+trial.FINAL_CLEANUP_SECONDS)
                    except AssertionError as error:
                        if mode != "persistent-stat-loss": raise
                        cleanup_rejected=True; cleanup_reason=str(error)
                    finally:
                        if metadata_original is not None: trial.process_info=metadata_original
                if (directory/"leaf.json").exists():
                    leaf=json.loads((directory/"leaf.json").read_text())
                    observed=trial.process_info(leaf['pid'])
                    assert observed is None or observed['starttime']!=leaf['starttime']
                assert trial.children_of(os.getpid())==[]
                assert trial.kernel_has_no_children()
                print(json.dumps({'failed':failed,'reason':reason,'children':[],
                    'cleanup_rejected':cleanup_rejected,'cleanup_reason':cleanup_reason,
                    'cleanup_seconds':time.monotonic()-started,
                    'action_admission_closed':owner.closing,
                    'returncode':entry['process'].returncode if entry else None}))
            ''')
            try:
                result = subprocess.run([sys.executable, "-c", script, trial.__file__, mode, str(root)],
                                        cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                        text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(unrelated.poll(), "containment affected unrelated process work")
                value = json.loads(result.stdout)
                self.assertEqual(value["children"], [])
                self.assertTrue(value["action_admission_closed"])
                self.assertLess(value["cleanup_seconds"], trial.FINAL_CLEANUP_SECONDS)
                return value
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=5)
                # Only this test's exact recorded leaf may need cleanup if the
                # implementation under test regresses. Never scan global PIDs.
                record = root / "leaf.json"
                if record.exists():
                    leaf = json.loads(record.read_text())
                    current = trial.process_info(leaf["pid"])
                    if current is not None and current["starttime"] == leaf["starttime"]:
                        os.kill(leaf["pid"], signal.SIGKILL)

    def test_normal_command_reaps_without_touching_an_unrelated_process(self):
        value = self.probe("normal")
        self.assertFalse(value["failed"])
        self.assertEqual(value["returncode"], 0)

    def test_early_exited_parent_cannot_hide_a_separate_session_orphan(self):
        value = self.probe("orphan")
        self.assertTrue(value["failed"])
        self.assertIn("adopted descendant", value["reason"])

    def test_expired_action_budget_still_contains_separate_session_child(self):
        value = self.probe("expired")
        self.assertTrue(value["failed"])
        self.assertIn("action budget expired", value["reason"])

    def test_failed_post_spawn_observation_still_cleans_held_child(self):
        value = self.probe("launch-observation")
        self.assertTrue(value["failed"])
        self.assertIn("leader ownership", value["reason"])

    def test_missing_ownership_metadata_never_becomes_successful_empty_cleanup(self):
        value = self.probe("persistent-stat-loss")
        self.assertTrue(value["cleanup_rejected"])
        self.assertIn("metadata was unavailable", value["cleanup_reason"])


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux containment qualification")
class DestinationReadinessTests(unittest.TestCase):
    """Named process fixtures exercise ownership, not MCP or native transport."""

    def probe(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            script = textwrap.dedent(r'''
                import importlib.util,json,os,signal,subprocess,sys,time
                from pathlib import Path
                spec=importlib.util.spec_from_file_location("trial",sys.argv[1])
                trial=importlib.util.module_from_spec(spec); spec.loader.exec_module(trial)
                mode=sys.argv[2]; directory=Path(sys.argv[3])
                package=directory/"src"/"liminal_bridge"; package.mkdir(parents=True)
                (package.parent/"__init__.py").write_text("")
                (package/"__init__.py").write_text("")
                record="json.dumps({'pid':os.getpid(),'starttime':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]})"
                bridge=directory/"native_fixture.py"
                bridge.write_text("\n".join([
                    "import time",
                    "from pathlib import Path",
                    "directory=Path(__file__).parent",
                    "while not (directory/'release').exists(): time.sleep(.01)",
                ])+"\n")
                (package/"conversation_server.py").write_text("\n".join([
                    "import json,os,subprocess,sys,time",
                    "from pathlib import Path",
                    "config=json.loads(Path(sys.argv[-1]).read_text())",
                    "directory=Path(config['directory'])",
                    "(directory/'profile.json').write_text("+record+")",
                    "if config['mode']=='missing': time.sleep(30)",
                    "else:",
                    "    child=subprocess.Popen([sys.executable,'-c',config['node_bootstrap']],env=config['node_environment'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
                    "    raise SystemExit(child.wait())",
                ])+"\n")
                configuration=directory/"profile.json.config"
                expected={"python":sys.executable,"config":str(configuration),
                          "node":sys.executable,"bridge":str(bridge),
                          "scope_hash":"a"*64,"port":31337}
                # The identity file is closed before exec exposes the exact
                # bridge argv. The bridge never rewrites it; PID/starttime persist.
                node_bootstrap="\n".join([
                    "import json,os,sys",
                    "from pathlib import Path",
                    "directory=Path("+repr(str(directory))+")",
                    "(directory/'node.json').write_text("+record+")",
                    "os.execve(sys.executable,"+repr([sys.executable,str(bridge),
                        expected["scope_hash"],"--conversation-bootstrap-port",str(expected["port"])])
                        +","+repr(trial.NODE_ENVIRONMENT)+")",
                ])
                configuration.write_text(json.dumps(expected | {
                    "mode":mode,"directory":str(directory),
                    "node_environment":trial.NODE_ENVIRONMENT,"node_bootstrap":node_bootstrap,
                }))
                # The profile and native-shaped child use the exact argv,
                # environment and session ancestry checked by the real observer.
                command="\n".join([
                    "import json,os,subprocess,sys",
                    "from pathlib import Path",
                    "directory=Path("+repr(str(directory))+")",
                    "(directory/'leader.json').write_text("+record+")",
                    "if "+repr(mode)+"=='exited': raise SystemExit(0)",
                    "profile=subprocess.Popen([sys.executable,'-m','src.liminal_bridge.conversation_server','--config',"+repr(str(configuration))+"],env="+repr(trial.PROFILE_ENVIRONMENT)+",start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
                    "raise SystemExit(profile.wait())",
                ])
                owner=trial.OwnedTrial(dedicated_process=True)
                entry=None; ready=None; source=None; reason=None
                try:
                    entry=owner.launch("destination",[sys.executable,"-c",command],
                                       directory,trial.PROFILE_ENVIRONMENT,expected_profile=expected)
                    original_start=entry["start"]
                    ready=owner.profile_ready(entry, maximum=.5 if mode=="missing" else 3)
                    assert not (directory/"release").exists()
                    for kind in ("profile","node"):
                        recorded=json.loads((directory/(kind+".json")).read_text())
                        assert ready[kind]["pid"]==recorded["pid"]
                        assert ready[kind]["starttime"]==recorded["starttime"]
                        assert trial.process_info(recorded["pid"])["state"] not in {"Z","X"}
                    assert ready["profile"]["parent"]==entry["process"].pid
                    assert ready["node"]["parent"]==ready["profile"]["pid"]
                    assert entry["start"]==original_start
                    if mode=="deadline":
                        # This absolute command deadline elapsed during actual
                        # readiness. A fresh default wait allowance must not win.
                        owner.wait([entry], cutoff=original_start)
                        raise AssertionError("expired original deadline was renewed")
                    source=owner.launch("sole-source",[sys.executable,"-c",
                        "from pathlib import Path; Path("+repr(str(directory/"release"))+").write_text('released')"],
                        directory,trial.PROFILE_ENVIRONMENT)
                    assert source["start"]>=ready["observed_monotonic"]
                    results=owner.wait([source,entry],cutoff=original_start+trial.COMMAND_SECONDS)
                    assert all(result["returncode"]==0 for result in results)
                except AssertionError as error:
                    reason=str(error)
                finally:
                    cleanup_started=time.monotonic()
                    owner.cleanup(cleanup_started+trial.FINAL_CLEANUP_SECONDS)
                for name in ("leader","profile","node"):
                    path=directory/(name+".json")
                    if path.exists():
                        recorded=json.loads(path.read_text())
                        current=trial.process_info(recorded["pid"])
                        assert current is None or current["starttime"]!=recorded["starttime"]
                assert trial.children_of(os.getpid())==[]
                assert trial.kernel_has_no_children()
                assert all(item["process"].returncode is not None for item in owner.commands)
                print(json.dumps({
                    "reason":reason,"ready":ready is not None,
                    "source_started":source is not None,
                    "source_after_readiness":source is not None and ready is not None
                        and source["start"]>=ready["observed_monotonic"],
                    "release_written":(directory/"release").exists(),
                    "children":[],"all_reaped":True,"admission_closed":owner.closing,
                    "cleanup_seconds":time.monotonic()-cleanup_started,
                }))
            ''')
            try:
                result = subprocess.run(
                    [sys.executable, "-c", script, trial.__file__, mode, str(root)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                    text=True, timeout=20, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(unrelated.poll(), "readiness containment affected unrelated work")
                value = json.loads(result.stdout)
                self.assertEqual(value["children"], [])
                self.assertTrue(value["all_reaped"])
                self.assertTrue(value["admission_closed"])
                self.assertLess(value["cleanup_seconds"], trial.FINAL_CLEANUP_SECONDS)
                return value
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=5)
                # Only exact private fixture identities, never global PID scans,
                # may be contained if the implementation under test regresses.
                for name in ("leader", "profile", "node"):
                    path = root / (name + ".json")
                    if not path.exists():
                        continue
                    recorded = json.loads(path.read_text())
                    current = trial.process_info(recorded["pid"])
                    if current is None or current["starttime"] != recorded["starttime"]:
                        continue
                    try:
                        if current["group"] == current["session"] == current["pid"]:
                            os.killpg(current["pid"], signal.SIGKILL)
                        else:
                            os.kill(current["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_receiver_is_observed_before_the_sole_source_starts(self):
        value = self.probe("ready")
        self.assertIsNone(value["reason"])
        self.assertTrue(value["ready"])
        self.assertTrue(value["source_after_readiness"])
        self.assertTrue(value["release_written"])

    def test_missing_or_exited_receiver_refuses_before_source_and_cleans(self):
        for mode, reasons in [
            ("missing", {"passive destination readiness deadline expired",
                         "kernel process inventory exceeded its deadline"}),
            ("exited", {"passive destination exited before readiness"}),
        ]:
            with self.subTest(mode=mode):
                value = self.probe(mode)
                # The same readiness cutoff also bounds the real /proc scan.
                self.assertIn(value["reason"], reasons)
                self.assertFalse(value["ready"])
                self.assertFalse(value["source_started"])
                self.assertFalse(value["release_written"])

    def test_readiness_does_not_renew_original_command_deadline(self):
        value = self.probe("deadline")
        self.assertEqual(value["reason"], "owned command exceeded its deadline")
        self.assertTrue(value["ready"])
        self.assertFalse(value["source_started"])
        self.assertFalse(value["release_written"])


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "WNOWAIT"), "Linux proc lifetime qualification")
class ExitingObservationTests(unittest.TestCase):
    """Real kernel denial under an unprivileged reader, not a native A/B scene."""

    def probe(self, mode):
        # Privileged readers may read a root-owned zombie environment. Decide
        # this prerequisite before creating any fixture or observing a denial.
        if os.geteuid() == 0:
            self.skipTest("real proc denial requires a non-root reader")
        with Path("/proc/self/status").open("rb") as stream:
            status = stream.read(8193)
        self.assertLessEqual(len(status), 8192)
        capabilities = [line.split()[1] for line in status.splitlines()
                        if line.startswith(b"CapEff:")]
        self.assertEqual(len(capabilities), 1)
        if int(capabilities[0], 16) & ((1 << 1) | (1 << 2) | (1 << 19)):
            self.skipTest("real proc denial requires no DAC or ptrace override")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = textwrap.dedent(r'''
                import importlib.util,json,os,sys,time
                from copy import deepcopy
                from pathlib import Path
                spec=importlib.util.spec_from_file_location("trial",sys.argv[1])
                trial=importlib.util.module_from_spec(spec); spec.loader.exec_module(trial)
                mode=sys.argv[2]; directory=Path(sys.argv[3])
                assert os.geteuid()!=0
                package=directory/"src"/"liminal_bridge"; package.mkdir(parents=True)
                (package.parent/"__init__.py").write_text("")
                (package/"__init__.py").write_text("")
                configuration=directory/"profile.config"
                configuration.write_text(json.dumps({"directory":str(directory),"mode":mode}))
                bridge=directory/"node_fixture.py"
                bridge.write_text("\n".join([
                    "import ctypes,json,os,time",
                    "from pathlib import Path",
                    "configuration=json.loads(Path(__file__).with_name('profile.config').read_text())",
                    "directory=Path(configuration['directory'])",
                    "deadline=time.monotonic()+12",
                    "(directory/'node_ready').touch()",
                    "while not (directory/'trigger').exists():",
                    "    assert time.monotonic()<deadline",
                    "    time.sleep(.005)",
                    "if configuration['mode']=='live':",
                    "    libc=ctypes.CDLL(None,use_errno=True)",
                    "    libc.prctl.restype=ctypes.c_int",
                    "    zero=ctypes.c_ulong(0)",
                    "    assert libc.prctl(ctypes.c_int(4),zero,zero,zero,zero)==0",
                    "    (directory/'live_hidden').touch()",
                    "    while not (directory/'release').exists():",
                    "        assert time.monotonic()<deadline",
                    "        time.sleep(.005)",
                    "os._exit(0)",
                ])+"\n")
                node_argv=[sys.executable,str(bridge),"a"*64,"--conversation-bootstrap-port","31337"]
                (package/"conversation_server.py").write_text("\n".join([
                    "import json,os,subprocess,sys,time",
                    "from pathlib import Path",
                    "configuration=json.loads(Path(sys.argv[-1]).read_text())",
                    "directory=Path(configuration['directory'])",
                    "deadline=time.monotonic()+12",
                    "node=subprocess.Popen("+repr(node_argv)+",env="+repr(trial.NODE_ENVIRONMENT)+",stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
                    "while os.waitid(os.P_PID,node.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:",
                    "    assert time.monotonic()<deadline",
                    "    time.sleep(.005)",
                    "(directory/'zombie').touch()",
                    "while not (directory/'reap').exists() and not (directory/'release').exists():",
                    "    assert time.monotonic()<deadline",
                    "    time.sleep(.005)",
                    "assert node.wait()==0",
                    "(directory/'reaped').touch()",
                    "while not (directory/'release').exists():",
                    "    assert time.monotonic()<deadline",
                    "    time.sleep(.005)",
                ])+"\n")
                command="\n".join([
                    "import subprocess,sys",
                    "profile=subprocess.Popen([sys.executable,'-m','src.liminal_bridge.conversation_server','--config',"+repr(str(configuration))+"],env="+repr(trial.PROFILE_ENVIRONMENT)+",start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
                    "raise SystemExit(profile.wait())",
                ])
                expected={"python":sys.executable,"config":str(configuration),
                          "node":sys.executable,"bridge":str(bridge),
                          "scope_hash":"a"*64,"port":31337}
                owner=trial.OwnedTrial(dedicated_process=True)
                original_file=trial.bounded_file
                original_info=trial.process_info
                cutoff=time.monotonic()+8
                def until(predicate):
                    while not predicate():
                        assert time.monotonic()<cutoff, "fixture barrier deadline"
                        time.sleep(.005)
                entry=None; result=None; facts={}
                try:
                    entry=owner.launch("lifetime-fixture",[sys.executable,"-c",command],
                                       directory,trial.PROFILE_ENVIRONMENT,expected_profile=expected)
                    original_start=entry["start"]; original_deadline=owner.deadline
                    until(lambda:(directory/"node_ready").exists())
                    inventory=trial.process_inventory(cutoff)
                    owner._observe(entry,inventory)
                    assert len(entry["observed"])==2
                    profile=next(value for value in entry["observed"].values() if value["kind"]=="profile")
                    original=deepcopy(next(value for value in entry["observed"].values() if value["kind"]=="node"))
                    assert profile["parent"]==entry["process"].pid
                    target=original["pid"]; key=(target,original["starttime"])
                    assert original["parent"]==profile["pid"]
                    assert original["group"]==original["session"]==profile["pid"]
                    assert original["state"] not in {"Z","X"}
                    if mode=="unbound":
                        del entry["observed"][key]
                    elif mode=="changed":
                        # A deliberately contradictory retained binding is not
                        # claimed as actual PID reuse. Fresh kernel identity wins.
                        entry["observed"][key]["starttime"]=str(int(original["starttime"])+1)
                    before=deepcopy(entry["observed"])
                    actual_errors=[]; armed=[True]
                    def gated_file(path,maximum):
                        path=str(path)
                        if path==f"/proc/{target}/cmdline" and armed[0]:
                            raw=original_file(path,maximum)
                            assert raw.split(b"\0")[:-1]==[os.fsencode(value) for value in node_argv]
                            armed[0]=False
                            (directory/"trigger").touch()
                            until(lambda:(directory/("live_hidden" if mode=="live" else "zombie")).exists())
                            current=trial.process_info(target)
                            assert all(current[name]==original[name]
                                       for name in ("pid","parent","group","session","starttime"))
                            stat=original_file(f"/proc/{target}/stat",4096).rsplit(b")",1)[1].split()
                            flags=int(stat[6])
                            facts.update({"state":current["state"],"flags":flags})
                            if mode=="live":
                                assert current["state"] not in {"Z","X"} and not flags & 4
                            else:
                                assert current["state"]=="Z" and flags & 4
                            return raw
                        if path==f"/proc/{target}/environ" and not armed[0]:
                            try:
                                return original_file(path,maximum)
                            except PermissionError as error:
                                assert error.errno==13
                                actual_errors.append(error)
                                if mode=="missing":
                                    (directory/"reap").touch()
                                    until(lambda:(directory/"reaped").exists())
                                    assert trial.process_info(target) is None
                                raise
                        return original_file(path,maximum)
                    trial.bounded_file=gated_file
                    caught=None
                    try:
                        owner._observe(entry,inventory)
                    except PermissionError as error:
                        caught=error
                    assert len(actual_errors)==1, "kernel did not produce the required real denial"
                    assert entry["observed"]==before
                    facts["observation_unchanged"]=True
                    assert entry["start"]==original_start and owner.deadline==original_deadline
                    assert owner.finish(entry,cutoff=cutoff) is None
                    assert entry["result"] is None and not owner.closing
                    if mode=="known":
                        assert caught is None
                        assert not hasattr(owner,"observation_failure")
                        assert trial.process_info(target)["state"]=="Z"
                        assert entry["observed"][key]==original
                        assert entry["exiting_observations"]=={key}
                    else:
                        assert not entry.get("exiting_observations")
                        assert caught is actual_errors[0]
                        assert owner.observation_failure=={"operation":"node_environment","errno":13}
                        encoded=json.dumps(owner.observation_failure,sort_keys=True)
                        assert len(encoded.encode())<128 and str(directory) not in encoded
                    trial.bounded_file=original_file
                    if mode=="known":
                        # This controlled state facade models only the pre-Z
                        # readiness join. The actual kernel task remains a
                        # measured zombie; no pre-Z timing is claimed.
                        def pre_z_state_projection(pid):
                            value=original_info(pid)
                            if pid==target and value is not None:
                                assert value["state"]=="Z" and value["starttime"]==original["starttime"]
                                return value | {"state":"S"}
                            return value
                        trial.process_info=pre_z_state_projection
                        retained_exit=entry.pop("exiting_observations")
                        try:
                            unmarked=owner.profile_ready(entry,maximum=.5)
                            assert unmarked["profile"]["pid"]==profile["pid"]
                            assert unmarked["node"]["pid"]==target and unmarked["node"]["state"]=="S"
                        finally:
                            entry["exiting_observations"]=retained_exit
                        minted=False; refusal=None
                        try:
                            owner.profile_ready(entry,maximum=.5)
                            minted=True
                        except AssertionError as error:
                            refusal=str(error)
                        assert not minted and refusal in {
                            "passive destination readiness deadline expired",
                            "kernel process inventory exceeded its deadline"}
                        assert entry["exiting_observations"]=={key}
                        assert entry["observed"][key]==original
                        assert entry["start"]==original_start and owner.deadline==original_deadline
                        trial.process_info=original_info
                        facts.update({"unmarked_pre_z_projection_ready":True,
                                      "known_exit_projection_ready":False})
                        (directory/"release").touch()
                        result=owner.wait([entry],maximum=3,cutoff=cutoff)[0]
                        assert result["returncode"]==0
                    facts.update({"original_error_preserved":caught is actual_errors[0],
                                  "denials":len(actual_errors),
                                  "normal_completion":result is not None,
                                  "diagnostic":getattr(owner,"observation_failure",None)})
                finally:
                    trial.bounded_file=original_file
                    trial.process_info=original_info
                    owner.cleanup(time.monotonic()+trial.FINAL_CLEANUP_SECONDS)
                assert all(item["process"].returncode is not None for item in owner.commands)
                assert trial.children_of(os.getpid())==[] and trial.kernel_has_no_children()
                facts.update({"all_reaped":True,"children":[]})
                print(json.dumps(facts))
            ''')
            result = subprocess.run(
                [sys.executable, "-c", script, str(Path(trial.__file__).resolve()), mode, str(directory)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["denials"], 1)
            self.assertTrue(value["observation_unchanged"])
            self.assertTrue(value["all_reaped"])
            self.assertEqual(value["children"], [])
            return value

    def test_previously_verified_exiting_task_preserves_only_its_old_observation(self):
        value = self.probe("known")
        self.assertEqual(value["state"], "Z")
        self.assertTrue(value["flags"] & 4)
        self.assertFalse(value["original_error_preserved"])
        self.assertTrue(value["normal_completion"])
        self.assertIsNone(value["diagnostic"])
        self.assertTrue(value["unmarked_pre_z_projection_ready"])
        self.assertFalse(value["known_exit_projection_ready"])

    def test_live_nondumpable_task_keeps_the_actual_permission_denial(self):
        value = self.probe("live")
        self.assertNotIn(value["state"], {"Z", "X"})
        self.assertFalse(value["flags"] & 4)
        self.assertTrue(value["original_error_preserved"])
        self.assertFalse(value["normal_completion"])
        self.assertEqual(value["diagnostic"], {"operation": "node_environment", "errno": 13})

    def test_unbound_changed_or_missing_identity_never_suppresses_actual_denial(self):
        for mode in ("unbound", "changed", "missing"):
            with self.subTest(mode=mode):
                value = self.probe(mode)
                self.assertTrue(value["original_error_preserved"])
                self.assertFalse(value["normal_completion"])
                self.assertEqual(value["diagnostic"], {"operation": "node_environment", "errno": 13})


@unittest.skipUnless(sys.platform == "linux", "Linux proc lifetime qualification")
class DisappearingObservationTests(unittest.TestCase):
    """Real reap between metadata reads; the parent profile is a named fixture."""

    def probe(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            script = textwrap.dedent(r'''
                import importlib.util,json,os,subprocess,sys,time
                from copy import deepcopy
                from pathlib import Path
                from types import SimpleNamespace
                spec=importlib.util.spec_from_file_location("trial",sys.argv[1])
                trial=importlib.util.module_from_spec(spec); spec.loader.exec_module(trial)
                mode=sys.argv[2]; directory=Path(sys.argv[3])
                bridge=directory/"node_fixture.py"
                bridge.write_text("import sys\nprint('ready',flush=True)\nsys.stdin.readline()\n")
                argv=[sys.executable,str(bridge),"a"*64,"--conversation-bootstrap-port","31337"]
                node=subprocess.Popen(argv,env=trial.NODE_ENVIRONMENT,stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
                original_file=trial.bounded_file; original_info=trial.process_info
                try:
                    assert node.stdout.readline()=="ready\n"
                    parent=trial.process_info(os.getpid())
                    assert parent["pid"]==parent["group"]==parent["session"]
                    expected={"python":sys.executable,"config":str(directory/"named-profile-fixture"),
                              "node":sys.executable,"bridge":str(bridge),"scope_hash":"a"*64,"port":31337}
                    entry={"process":SimpleNamespace(pid=os.getpid()),"profile":expected,
                           "start":time.monotonic(),
                           "observed":{(parent["pid"],parent["starttime"]):parent|{"kind":"profile"}}}
                    owner=object.__new__(trial.OwnedTrial)
                    owner.deadline=time.monotonic()+8
                    inventory=trial.process_inventory(owner.deadline)
                    owner._observe(entry,inventory)
                    info=inventory[node.pid]; key=(node.pid,info["starttime"])
                    assert entry["observed"][key]==info|{"kind":"node"}
                    if mode.startswith("unbound"):
                        del entry["observed"][key]
                    before=deepcopy(entry["observed"])
                    original_start=entry["start"]; original_deadline=owner.deadline
                    if mode in {"known","unbound","known-exe","unbound-exe"}:
                        actual_errors=[]; armed=[True]
                        operation="node_executable" if mode.endswith("-exe") else "node_environment"
                        original_metadata=owner._observe_metadata
                        def tracked_metadata(entry,info,name,read):
                            def tracked_read():
                                try:
                                    return read()
                                except FileNotFoundError as error:
                                    assert name==operation and error.errno==2
                                    actual_errors.append(error)
                                    raise
                            return original_metadata(entry,info,name,tracked_read)
                        owner._observe_metadata=tracked_metadata
                        def gated_file(path,maximum):
                            path=str(path)
                            preceding="environ" if mode.endswith("-exe") else "cmdline"
                            if path==f"/proc/{node.pid}/{preceding}" and armed[0]:
                                raw=original_file(path,maximum)
                                if preceding=="cmdline":
                                    assert raw.split(b"\0")[:-1]==[os.fsencode(value) for value in argv]
                                armed[0]=False
                                node.stdin.write("stop\n"); node.stdin.flush()
                                assert node.wait(timeout=3)==0
                                assert trial.process_info(node.pid) is None
                                return raw
                            return original_file(path,maximum)
                        trial.bounded_file=gated_file
                        owner._observe(entry,inventory)
                        assert len(actual_errors)==1
                        assert not hasattr(owner,"observation_failure")
                        assert trial.process_info(node.pid) is None
                    else:
                        # A deliberately injected missing-file error while the
                        # actual process is alive cannot establish disappearance.
                        original_error=FileNotFoundError(2,"private fixture path")
                        def missing():
                            raise original_error
                        if mode=="unreadable":
                            def unreadable(_pid):
                                raise PermissionError(13,"private fixture path")
                            trial.process_info=unreadable
                        caught=None
                        try:
                            owner._observe_metadata(entry,info,"node_environment",missing)
                        except FileNotFoundError as error:
                            caught=error
                        assert caught is original_error
                        assert owner.observation_failure=={"operation":"node_environment","errno":2}
                        assert "private" not in json.dumps(owner.observation_failure)
                        trial.process_info=original_info
                        assert trial.process_info(node.pid)["starttime"]==info["starttime"]
                        node.stdin.write("stop\n"); node.stdin.flush()
                        assert node.wait(timeout=3)==0
                    assert entry["observed"]==before and not entry.get("exiting_observations")
                    assert entry["start"]==original_start and owner.deadline==original_deadline
                    print(json.dumps({"mode":mode,"observations_unchanged":True,
                                      "deadlines_unchanged":True,"reaped":node.returncode==0}))
                finally:
                    trial.bounded_file=original_file; trial.process_info=original_info
                    if node.poll() is None:
                        node.kill(); node.wait(timeout=3)
                    node.stdin.close(); node.stdout.close()
            ''')
            result = subprocess.run(
                [sys.executable, "-c", script, str(Path(trial.__file__).resolve()), mode, temporary],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                start_new_session=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = json.loads(result.stdout)
            self.assertTrue(value["observations_unchanged"])
            self.assertTrue(value["deadlines_unchanged"])
            self.assertTrue(value["reaped"])

    def test_reaped_descendant_adds_no_observation_or_readiness(self):
        for mode in ("known", "unbound", "known-exe", "unbound-exe"):
            with self.subTest(mode=mode):
                self.probe(mode)

    def test_live_or_unreadable_process_preserves_original_missing_file_error(self):
        for mode in ("live", "unreadable"):
            with self.subTest(mode=mode):
                self.probe(mode)


if __name__ == "__main__":
    unittest.main()
