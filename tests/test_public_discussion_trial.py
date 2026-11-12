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


if __name__ == "__main__":
    unittest.main()
