"""Pure helper checks. No role services, Docker operations or network trial."""
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests/contracts'))
try:
    import check_role_stack as subject
finally:
    sys.path.pop(0)


class Clock:
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class HarnessChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.clock = Clock()
        self.time_patch = patch.object(subject.time, 'monotonic', self.clock.monotonic)
        self.sleep_patch = patch.object(subject.time, 'sleep', self.clock.sleep)
        self.time_patch.start()
        self.sleep_patch.start()
        self.trial = subject.Trial(self.root)
    def tearDown(self):
        self.sleep_patch.stop()
        self.time_patch.stop()
        self.directory.cleanup()

    def record(self, manager, service):
        process = Mock(pid=manager, stdout=io.BytesIO())
        process.poll.return_value = None
        reader = Mock()
        reader.is_alive.return_value = False
        return {'process': process, 'identity': {'pid': manager, 'start_ticks': 1},
                'services': [{'pid': service, 'start_ticks': 1}], 'services_observed': True,
                'reader_stop': Mock(), 'reader': reader, 'overflow': False}

    def test_global_and_case_work_deadlines_clip_nested_actions(self):
        self.clock.now = 830
        self.assertEqual(self.trial.limit(45), 10)
        self.trial.case_deadline = 832
        self.assertEqual(self.trial.limit(45), 2)
        self.trial.cleanup_deadline = 835
        self.assertEqual(self.trial.limit(45, cleanup=True), 5)
        self.clock.now = 832
        with self.assertRaises(AssertionError):
            self.trial.limit(1)

    def test_wait_sets_and_restores_nested_case_deadline(self):
        limits = []
        def observed():
            limits.append(self.trial.limit(45))
            return True
        self.trial.wait(observed, 'mock observation', 2)
        self.assertEqual(limits, [2])
        self.assertEqual(self.trial.case_deadline, self.trial.work_deadline)

    def test_late_observation_is_not_success(self):
        def observed():
            self.clock.now = 3
            return True
        with self.assertRaises(AssertionError):
            self.trial.wait(observed, 'late mock observation', 2)
        self.assertEqual(self.trial.case_deadline, self.trial.work_deadline)

    def test_transient_connection_failure_is_only_bounded_readiness(self):
        calls = []
        def observed():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionRefusedError()
            return True
        self.trial.wait(observed, 'mock readiness', 0.2)
        self.assertEqual(len(calls), 2)

    def test_manager_exit_does_not_prove_service_exit(self):
        record = self.record(200, 201)
        record['process'].poll.return_value = 130
        self.trial.history = [record]
        self.trial.cleanup_deadline = 0.3
        with patch.object(subject, 'original_live', return_value=True):
            result = self.trial.cleanup()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertTrue(result['managers_reaped'])
        self.assertFalse(result['recorded_services_exited'])
        self.assertLessEqual(self.clock.now, 0.3)

    def test_cleanup_signals_all_before_shared_wait(self):
        records = [self.record(200, 201), self.record(300, 301)]
        self.trial.history = records
        self.trial.cleanup_deadline = 0.2
        events = []
        def signalled(pid, signum):
            events.append(('signal', pid, self.clock.now))
        with patch.object(subject.os, 'kill', side_effect=signalled), patch.object(subject, 'original_live', return_value=True):
            result = self.trial.cleanup()
        self.assertEqual(events, [('signal', 200, 0), ('signal', 300, 0)])
        self.assertEqual(result['outcome'], 'unknown')
        self.assertLessEqual(self.clock.now, 0.2)

    def test_cleanup_does_not_signal_reused_pid(self):
        record = self.record(200, 201)
        self.trial.history = [record]
        self.trial.cleanup_deadline = 0
        with patch.object(subject, 'original_live', return_value=False), patch.object(subject.os, 'kill') as kill:
            result = self.trial.cleanup()
        kill.assert_not_called()
        self.assertEqual(result['outcome'], 'unknown')

    def test_missing_startup_observation_keeps_cleanup_unknown(self):
        record = self.record(200, 201)
        record.update(services=[], services_observed=False)
        record['process'].poll.return_value = 130
        self.trial.history = [record]
        with patch.object(subject, 'original_live', return_value=False):
            result = self.trial.cleanup()
        self.assertEqual(result['outcome'], 'unknown')

    def test_known_reaped_processes_and_services_can_complete(self):
        record = self.record(200, 201)
        record['process'].poll.return_value = 130
        self.trial.history = [record]
        with patch.object(subject, 'original_live', return_value=False):
            result = self.trial.cleanup()
        self.assertEqual(result['outcome'], 'observed')

    def test_frontend_cleanup_is_not_short_circuited(self):
        first, second = Mock(), Mock()
        first.finish_stop.return_value = False
        second.finish_stop.return_value = True
        self.trial.frontends = [first, second]
        result = self.trial.cleanup()
        first.finish_stop.assert_called_once_with(self.trial.cleanup_deadline)
        second.finish_stop.assert_called_once_with(self.trial.cleanup_deadline)
        self.assertEqual(result['outcome'], 'unknown')

    def test_unknown_command_cleanup_cannot_become_observed(self):
        self.trial.command_unknown = True
        self.assertEqual(self.trial.cleanup()['outcome'], 'unknown')

    def test_role_plan_filters_only_the_used_artifact_union(self):
        original = {'schema_version': 1, 'interpreter': {'version': 'fixture'},
            'seed': {'method': 'sealed-pip-wheel', 'filename': 'pip.whl'},
            'artifacts': [{'filename': name, 'sha256': name} for name in ('pip.whl', 'zpm.whl', 'oj.whl', 'kp.whl')],
            'environments': {'zpm': {'source_revision': 'z', 'wheels': ['pip.whl', 'zpm.whl']},
                             'openjules': {'source_revision': 'o', 'wheels': ['pip.whl', 'oj.whl']},
                             'polyphony': {'source_revision': 'p', 'wheels': ['pip.whl', 'kp.whl']}}}
        path = self.root / 'original.json'
        subject.private_json(path, original)
        reference = {'manifest': str(path), 'sha256': subject.digest(path.read_bytes()), 'artifacts': '/fixture-only-artifacts'}
        result = subject.role_dependency_plan(self.root, reference, ('zpm', 'openjules'))
        plan = json.loads(Path(result['manifest']).read_bytes())
        self.assertEqual(plan['schema_version'], 2)
        self.assertEqual(set(plan['environments']), {'zpm', 'openjules'})
        self.assertEqual({row['filename'] for row in plan['artifacts']}, {'pip.whl', 'zpm.whl', 'oj.whl'})
        self.assertEqual(plan['seed'], original['seed'])
        self.assertEqual(result['artifacts'], reference['artifacts'])
        self.assertEqual(result['sha256'], subject.digest(Path(result['manifest']).read_bytes()))

    def test_role_plan_refuses_changed_original_digest(self):
        path = self.root / 'original.json'
        subject.private_json(path, {'schema_version': 1})
        with self.assertRaises(AssertionError):
            subject.role_dependency_plan(self.root, {'manifest': str(path), 'sha256': '0' * 64, 'artifacts': '/fixture'}, ('zpm', 'openjules'))

    def test_readonly_oracle_never_creates_missing_journal(self):
        path = self.root / 'missing.sqlite3'
        with self.assertRaises(AssertionError):
            subject.rows(path, 'workers')
        self.assertFalse(path.exists())

    def test_readonly_oracle_refuses_extra_history(self):
        path = self.root / 'fixture.sqlite3'
        with sqlite3.connect(path) as database:
            database.execute('CREATE TABLE workers (id INTEGER)')
            database.executemany('INSERT INTO workers VALUES (?)', [(value,) for value in range(9)])
        before = path.read_bytes()
        with self.assertRaises(AssertionError):
            subject.rows(path, 'workers')
        self.assertEqual(path.read_bytes(), before)

    def test_error_message_and_native_stderr_are_not_exported(self):
        failure = subject.RoleTrialFailure('fixed phase', ['fixed check'], ValueError('secret-token-native-stderr'), {'outcome': 'unknown'})
        encoded = json.dumps(failure.role_diagnostic) + str(failure)
        self.assertNotIn('secret-token-native-stderr', encoded)
        self.assertEqual(set(failure.role_diagnostic), {'schema_version', 'passed', 'phase', 'completed_checks', 'error_type', 'cleanup', 'private_recovery_state_retained'})

    def test_completion_after_nested_deadline_is_refused(self):
        output_read, output_write = os.pipe()
        error_read, error_write = os.pipe()
        os.write(output_write, b'{"ok":true}')
        os.close(output_write)
        os.close(error_write)
        process = Mock(pid=200, stdout=os.fdopen(output_read, 'rb'), stderr=os.fdopen(error_read, 'rb'))
        process.poll.return_value = 0
        def completed(timeout):
            self.assertLessEqual(timeout, 2)
            self.clock.now = 3
            return 0
        process.wait.side_effect = completed
        self.trial.case_deadline = 2
        with patch.object(subject.subprocess, 'Popen', return_value=process):
            with self.assertRaises(AssertionError):
                self.trial.command(['unexecuted-fixture'], self.root, {})
        self.assertTrue(self.trial.command_unknown)

    def test_refusal_replay_preserves_the_exact_captured_body_bytes(self):
        raw = b'{ "launch_key" : "original", "admission_revision": 1 }\n'
        connection = Mock()
        response = Mock(status=200)
        response.read1.side_effect = [b'{"disposition":{"kind":"never_admitted"}}', b'']
        connection.getresponse.return_value = response
        with patch.object(subject.http.client, 'HTTPSConnection', return_value=connection), \
                patch.object(subject.ssl, 'create_default_context'):
            result = subject.exchange('https://127.0.0.1:12345', 'POST', '/api/v2/workers/ensure',
                token='fixture-token', budget=self.trial, raw_body=raw, ca='unused-fixture-path')
        self.assertEqual(connection.request.call_args.kwargs['body'], raw)
        self.assertEqual(result['disposition']['kind'], 'never_admitted')
        connection.close.assert_called_once_with()

    def test_manager_is_owned_before_failed_proc_observation(self):
        process = Mock(pid=123, stdout=io.BytesIO())
        process.poll.return_value = None
        def terminate():
            process.poll.return_value = -15
        process.terminate.side_effect = terminate
        with patch.object(subject.subprocess, 'Popen', return_value=process), \
                patch.object(subject, 'proc_identity', side_effect=PermissionError('fixture proc denial')):
            with self.assertRaises(PermissionError):
                self.trial.start('a', self.root, {}, [['unexecuted-service']])
            self.assertIs(self.trial.managers['a']['process'], process)
            self.assertEqual(len(self.trial.history), 1)
            result = self.trial.cleanup()
        process.terminate.assert_called_once_with()
        self.assertTrue(result['managers_reaped'])
        self.assertEqual(result['outcome'], 'unknown')

    def test_tls_listener_is_owned_and_closed_when_thread_start_fails(self):
        class FakeServer:
            def __init__(self, *args):
                self.closed = False
            def server_close(self):
                self.closed = True
        thread = Mock(ident=None)
        thread.start.side_effect = RuntimeError('fixture start refusal')
        thread.is_alive.return_value = False
        with patch.object(subject, 'ThreadingHTTPServer', FakeServer), \
                patch.object(subject.ssl, 'SSLContext'), patch.object(subject.threading, 'Thread', return_value=thread):
            with self.assertRaises(RuntimeError):
                subject.LifecycleTLS(self.trial, 10001, 10002, 'unread-fixture-certificate')
            self.assertEqual(len(self.trial.frontends), 1)
            self.assertTrue(self.trial.frontends[0].server.closed)
            result = self.trial.cleanup()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertFalse(result['frontends_stopped'])
        thread.join.assert_not_called()

    def role_environment_fixture(self, *, sealed):
        from zpm import dependencies
        bundle = self.root / 'bundle'
        bundle.mkdir(mode=0o700)
        (bundle / 'environments').mkdir(mode=0o700)
        for name in ('zpm', 'polyphony'):
            (bundle / 'environments' / name).mkdir(mode=0o700)
        data = {'schema_version': 3, 'role': 'controller-authority',
                'sources': {'zpm': {'revision': 'a' * 40}, 'polyphony': {'revision': 'b' * 40}}}
        if sealed:
            (bundle / 'dependencies').mkdir(mode=0o700)
            plan = {'schema_version': 2, 'interpreter': {'version': 'fixture'},
                    'environments': {'zpm': {}, 'polyphony': {}}}
            raw = dependencies._encoded(plan)
            dependencies._write(bundle / 'dependencies/plan.json', raw, readonly=True)
            data['dependency_plan'] = {'sha256': subject.digest(raw)}
            identity = {'schema_version': 1, 'plan_sha256': subject.digest(raw), 'interpreter': plan['interpreter'],
                        'sources': {name: row['revision'] for name, row in data['sources'].items()}}
            dependencies._publish(bundle / 'environments/sealed-intent.json', identity)
            for name in ('zpm', 'polyphony'):
                dependencies._publish(bundle / 'environments' / (name + '-sealed-intent.json'), identity | {'service': name})
        return bundle, data

    def test_ordinary_role_layout_contains_only_its_service_directories(self):
        bundle, data = self.role_environment_fixture(sealed=False)
        subject.verify_role_environments(bundle, data)

    def test_sealed_role_layout_accepts_exact_private_native_intents(self):
        bundle, data = self.role_environment_fixture(sealed=True)
        subject.verify_role_environments(bundle, data)

    def test_foreign_service_directory_is_still_refused(self):
        bundle, data = self.role_environment_fixture(sealed=True)
        (bundle / 'environments/openjules').mkdir(mode=0o700)
        with self.assertRaises(subject.RoleCheck):
            subject.verify_role_environments(bundle, data)

    def test_unknown_regular_file_is_not_skipped(self):
        bundle, data = self.role_environment_fixture(sealed=True)
        (bundle / 'environments/foreign.json').write_text('{}')
        with self.assertRaises(subject.RoleCheck):
            subject.verify_role_environments(bundle, data)

    def test_ordinary_layout_cannot_silently_adopt_sealed_intent_files(self):
        bundle, data = self.role_environment_fixture(sealed=False)
        (bundle / 'environments/sealed-intent.json').write_text('{}')
        with self.assertRaises(subject.RoleCheck):
            subject.verify_role_environments(bundle, data)

    def test_sealed_intent_requires_the_exact_original_source_revision(self):
        from zpm import dependencies
        bundle, data = self.role_environment_fixture(sealed=True)
        path = bundle / 'environments/zpm-sealed-intent.json'
        value = json.loads(path.read_bytes())
        value['sources']['zpm'] = 'c' * 40
        path.write_bytes(dependencies._encoded(value))
        with self.assertRaises(subject.RoleCheck):
            subject.verify_role_environments(bundle, data)

    def test_sealed_intent_bool_schema_cannot_alias_integer_schema(self):
        from zpm import dependencies
        bundle, data = self.role_environment_fixture(sealed=True)
        path = bundle / 'environments/zpm-sealed-intent.json'
        value = json.loads(path.read_bytes())
        value['schema_version'] = True
        path.write_bytes(dependencies._encoded(value))
        with self.assertRaises(subject.RoleCheck):
            subject.verify_role_environments(bundle, data)

    def test_service_symlink_is_not_a_role_environment(self):
        bundle, data = self.role_environment_fixture(sealed=False)
        (bundle / 'environments/zpm').rmdir()
        (bundle / 'environments/zpm').symlink_to(bundle / 'environments/polyphony', target_is_directory=True)
        with self.assertRaises(subject.RoleCheck):
            subject.verify_role_environments(bundle, data)

    def test_only_the_single_armed_ensure_gets_the_admin_allowance(self):
        frontend = object.__new__(subject.LifecycleTLS)
        frontend.lock = threading.Lock()
        callback = Mock()
        frontend.before_ensure = callback
        frontend.drop_next_ensure = False
        self.assertEqual(frontend.request_controls(False), (None, False, 3))
        self.assertIs(frontend.before_ensure, callback)
        self.assertEqual(frontend.request_controls(True), (callback, False, 20))
        self.assertEqual(frontend.request_controls(True), (None, False, 3))
        self.assertEqual(frontend.request_controls(False), (None, False, 3))
        callback.assert_not_called()

    def test_registry_restoration_requires_a_retained_three_second_policy(self):
        path = self.root / 'registry.sqlite3'
        with sqlite3.connect(path) as database:
            database.execute('CREATE TABLE registry_revisions (revision INTEGER, policy TEXT)')
            database.execute('INSERT INTO registry_revisions VALUES (1, ?)', (json.dumps({'timeout_seconds': 20}),))
        subject.check_registry_timeout(path, 20)
        with self.assertRaises(subject.RoleCheck):
            subject.check_registry_timeout(path, 3)
        with sqlite3.connect(path) as database:
            database.execute('INSERT INTO registry_revisions VALUES (2, ?)', (json.dumps({'timeout_seconds': 3}),))
        before = path.read_bytes()
        subject.check_registry_timeout(path, 3)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
