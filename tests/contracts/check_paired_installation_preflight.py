"""Installed opt-in paired preflight, separate from task execution evidence.

The production manager runs both producers. This helper checks its actual safe
result, immutable receipt reuse and a sealed-input start refusal. It never
constructs producer receipts or turns fixture comparison into native evidence.
"""

from __future__ import annotations

import json
import copy
import hashlib
import os
from pathlib import Path
import stat
import sys
import uuid
from unittest.mock import patch

from check_openjules_installation_validation import (
    bounded_file, private_file, protected_snapshot, require, run_bounded,
)


CONTRACT = "zpm-installation-admission/v1"
REVISIONS = {
    "openjules": "7135d0988dff050df63acb89f75562dafccacf79",
    "polyphony": "324ace275c4c62414439dc2eb620ab1f5177b4a9",
}
REPORT_KEYS = {
    "schema_version", "receipt_contract", "receipt_id", "phase", "compatible",
    "source_identity_verified", "native_validation_performed",
    "live_capabilities_checked", "execution_qualified", "services_started",
    "components", "errors",
}
START_GUARD = """import os,pathlib,runpy,sys
os.dup2(1,2)
def audit(event,args):
    if event == 'subprocess.Popen':
        argv=args[1]
        if not isinstance(argv,(list,tuple)) or not argv or pathlib.Path(argv[0]).name != 'git':
            raise RuntimeError('paired_trial_unexpected_service_start')
sys.addaudithook(audit)
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
MERGED_OUTPUT = """import os,runpy,sys
os.dup2(1,2)
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
NATIVE_START_GUARD = """import json,os,pathlib,runpy,sys
os.dup2(1,2)
allowed_inputs=json.loads(sys.argv.pop(1))
def audit(event,args):
    if event == 'subprocess.Popen':
        argv=args[1]
        allowed=False
        if isinstance(argv,(list,tuple)) and argv:
            allowed=pathlib.Path(argv[0]).name == 'git' or argv == allowed_inputs['context']
            selected=allowed_inputs['commands'].get(argv[0])
            if selected and len(argv) >= 9 and argv[1:4] == ['-I','-B','-X'] and argv[5] == '-c':
                probe=argv[6:] == [allowed_inputs['probe'],selected['checkout'],selected['package'],selected['module']]
                native=argv[6:9] == [allowed_inputs['native'],selected['checkout'],selected['command']]
                if native and selected['verb'] is not None:
                    native=len(argv) >= 10 and argv[9] == selected['verb']
                allowed=allowed or probe or native
        if not allowed:
            raise RuntimeError('paired_trial_unexpected_service_start')
sys.addaudithook(audit)
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""


def accepted_report(raw):
    """Keep the published result closed, including explicit nonclaims."""
    from zpm.installation_admission import _decode

    require(type(raw) is bytes and 0 < len(raw) <= 16384,
            "paired_trial_report_bound")
    try:
        value = _decode(raw, 16384)
        require(type(value) is dict and type(value.get("receipt_id")) is str,
                "paired_trial_report_shape")
        identity = uuid.UUID(value["receipt_id"])
    except (ValueError, TypeError, KeyError):
        raise AssertionError("paired_trial_report_shape") from None
    require(type(value) is dict and set(value) == REPORT_KEYS
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["receipt_contract"] == CONTRACT
            and identity.version == 4 and str(identity) == value["receipt_id"]
            and value["phase"] == "native-config-validated"
            and value["compatible"] is True
            and value["source_identity_verified"] is True
            and value["native_validation_performed"] is True
            and value["live_capabilities_checked"] is False
            and value["execution_qualified"] is False
            and value["services_started"] is False
            and value["components"] == {
                "openjules": {"status": "valid"},
                "keystone-polyphony": {"status": "valid"},
            }
            and value["errors"] == [], "paired_trial_report_shape")
    return value


def retained_receipt(bundle, work):
    require(bounded_file(Path(bundle) / "state/installation-admission.json", 8_000_000)
            == bounded_file(Path(work) / "original-receipt.json", 8_000_000),
            "paired_trial_scene_changed_original_receipt")
    return True


def refused_report(raw, expected_code):
    """The last manager line follows bounded package output during bootstrap."""
    from zpm.installation_admission import _decode

    require(expected_code in {"required_peer_unvalidated", "scope_mismatch", "base_mismatch", "producer_invalid"},
            "paired_trial_refusal_case")
    owner = "openjules" if expected_code == "producer_invalid" else "zpm"
    statuses = ({"openjules": {"status": "invalid"}, "keystone-polyphony": {"status": "unvalidated"}}
                if expected_code == "producer_invalid" else {
                    "openjules": {"status": "valid"}, "keystone-polyphony": {"status": "valid"}})
    try:
        value = _decode(raw.rstrip().rsplit(b"\n", 1)[-1], 16384)
    except (ValueError, TypeError, IndexError):
        raise AssertionError("paired_trial_unsupported_report") from None
    require(type(value) is dict and set(value) == REPORT_KEYS
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["receipt_contract"] == CONTRACT
            and type(value["receipt_id"]) is str
            and str(uuid.UUID(value["receipt_id"])) == value["receipt_id"]
            and uuid.UUID(value["receipt_id"]).version == 4
            and value["phase"] == "native-config-refused"
            and value["compatible"] is None
            and type(value["source_identity_verified"]) is bool
            and value["native_validation_performed"] is True
            and value["live_capabilities_checked"] is False
            and value["execution_qualified"] is False
            and value["services_started"] is False
            and value["components"] == statuses
            and value["errors"] == [{"owner": owner, "code": expected_code}],
            "paired_trial_unsupported_report")
    return value


def unsupported_report(raw):
    return refused_report(raw, "required_peer_unvalidated")


def configuration_state_snapshot(bundle):
    """Snapshot protected bytes before any interpreter or selector exists."""
    paths = [bundle / "manifest.json", bundle / "manage.py"]
    for directory in ("config", "secrets", "state"):
        paths.append(bundle / directory)
        paths.extend((bundle / directory).rglob("*"))
    result, total = {}, 0
    for path in sorted(paths):
        relative = str(path.relative_to(bundle))
        if relative in {"state/stack.lock", "state/installation-admission-status.json"}:
            continue
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), "paired_trial_protected_path")
        if stat.S_ISDIR(info.st_mode):
            result[relative] = ["directory", stat.S_IMODE(info.st_mode)]
        else:
            raw = bounded_file(path, 8_000_000)
            total += len(raw)
            require(total <= 16_000_000, "paired_trial_protected_bound")
            result[relative] = [hashlib.sha256(raw).hexdigest(), stat.S_IMODE(info.st_mode), len(raw)]
    return result


def qualify_unsupported_public(bundle, work):
    """A separate native-valid pair whose additional peer stays unqualified."""
    from zpm.deployment import prepare, verify

    bundle, work = Path(bundle), Path(work)
    data = json.loads(bounded_file(bundle / "manifest.json", 2_000_000))["configuration"]
    require(data.get("installation_admission") == CONTRACT and "child_policy" in data,
            "paired_trial_negative_requires_children")
    work.mkdir(mode=0o700)
    scope_key = work / "public-scope.key"
    private_file(scope_key, os.urandom(32).hex().encode("ascii"))
    recipe = copy.deepcopy(data)
    recipe["deployment_id"] += "-unsupported"
    recipe["command"] += ["--public-socket", "/run/openjules-model/public.sock"]
    recipe["child_policy"]["outcome_protocol"] = "initial-v1"
    recipe["child_policy"]["public_discussion"] = {
        "protocol": "managed-public-pull-v1", "public_approved": True,
        "topic": "Paired preflight unsupported peer fixture",
        "server": {"scope_key_file": str(scope_key),
                   "bootstrap": {"host": "127.0.0.1", "port": 41001}},
        "personas": {"reviewer": "Keep this validation fixture read-only."},
        "instructions": {"question": "This fixture starts no public participant."},
        "invitations": {"peer": {"target": "0123456789abcdef",
                                 "content": "No invitation is sent by this fixture."}},
    }
    negative = work / "installation"
    prepare(recipe, negative)
    verify(negative)
    return qualify_refused_pair(negative, work, "required_peer_unvalidated") | {
        "evidence": "actual-unsupported-paired-native-composition", "public_participant_started": False,
    }


def qualify_refused_pair(negative, work, expected_code):
    """Real bootstrap and audited start; no native-result substitution."""
    from zpm.deployment import verify
    from zpm import installation_preflight

    negative, work = Path(negative), Path(work)
    verify(negative)
    recipe = json.loads(bounded_file(negative / "manifest.json", 2_000_000))["configuration"]
    selection_path = negative / "config/installation-admission.json"
    selection = bounded_file(selection_path, 131072)
    receipt_id = json.loads(selection)["receipt_id"]

    before = configuration_state_snapshot(negative)
    code, raw = run_bounded(
        [sys.executable, "-I", "-B", "-c", MERGED_OUTPUT,
         str(negative / "manage.py"), "bootstrap"], negative, seconds=900,
    )
    require(code == 2, "paired_trial_" + expected_code + "_bootstrap_was_not_refused")
    bootstrap = refused_report(raw, expected_code)
    private_file(work / "bootstrap.log", raw)
    interpreters = [str(negative / "environments" / service / "bin/python")
                    for service in ("openjules", "polyphony")]
    permitted = {
        "context": [interpreters[0], "-m", "src.worker.execution_context", "--probe-managed-installation"],
        "probe": installation_preflight._PROBE, "native": installation_preflight._NATIVE,
        "commands": {
            str(negative / "environments" / spec["source"] / "bin/python"): {
                "checkout": recipe["sources"][spec["source"]]["checkout"],
                "package": spec["package"], "module": spec["module"], "command": spec["command"],
                "verb": "validate-installation" if service == "keystone-polyphony" else None,
            } for service, spec in installation_preflight._SPECS.items()
        },
    }
    code, raw = run_bounded(
        [sys.executable, "-I", "-B", "-c", NATIVE_START_GUARD, json.dumps(permitted),
         str(negative / "manage.py"), "start"], negative, limit=16384, seconds=120,
    )
    require(code == 2, "paired_trial_" + expected_code + "_start_was_not_refused")
    start = refused_report(raw, expected_code)
    private_file(work / "start-refusal.json", raw)
    require(bootstrap["receipt_id"] == start["receipt_id"] == receipt_id
            and bounded_file(selection_path, 131072) == selection
            and not (negative / "state/installation-admission.json").exists()
            and configuration_state_snapshot(negative) == before,
            "paired_trial_" + expected_code + "_changed_identity_or_state")
    verify(negative)
    return {"schema_version": 1, "evidence": "actual-native-paired-refusal",
            "bootstrap": bootstrap, "start": start,
            "before_service_launch": True, "original_prepared_identity_retained": True,
            "success_receipt_created": False, "protected_configuration_and_state_unchanged": True,
            "live_provider_qualified": False}


def prepare_mismatched_pair(recipe, destination, case):
    """Inject one initial-generation fault before any selection or seal exists.

    This test-only seam changes generated OpenJules input in a new unpublished
    staging directory. The genuine installer then creates its first UUID and
    input bindings. No accepted bundle, producer receipt or native command is
    modified or replaced.
    """
    from zpm import deployment, installation_admission

    destination = Path(destination)
    require(case in {"repository", "profile", "base"} and not destination.exists(),
            "paired_trial_fresh_mismatch_required")
    original = deployment._finish_bundle
    observed = []
    def finish(data, stage, final, readme):
        require(data["schema_version"] == 1 and final == destination
                and not final.exists() and not (stage / "config/installation-admission.json").exists()
                and not (stage / "manifest.json").exists(), "paired_trial_mismatch_after_sealing")
        path = stage / "config/openjules.json"
        value = json.loads(bounded_file(path, 1_000_000))
        require(len(value["profiles"]) == 1 and "models" not in value and "children" not in value,
                "paired_trial_mismatch_requires_root_fixture")
        profile = value["profiles"][0]
        if case == "repository":
            foreign = "github:github.com/paired-fixture/foreign"
            require(profile["repository"] != foreign, "paired_trial_mismatch_fixture_collision")
            profile["repository"] = foreign
        elif case == "profile":
            require(profile["name"] != "preflight-foreign", "paired_trial_mismatch_fixture_collision")
            profile["name"] = "preflight-foreign"
        else:
            base = profile["base_commit"]
            profile["base_commit"] = ("0" if base[0] != "0" else "1") + base[1:]
        raw = installation_admission.encode(value)
        require(len(raw) <= 1_000_000, "paired_trial_mismatch_input_bound")
        path.write_bytes(raw)
        observed.append(case)
        return original(data, stage, final, readme)
    with patch.object(deployment, "_finish_bundle", side_effect=finish):
        deployment.prepare(recipe, destination)
    require(observed == [case], "paired_trial_mismatch_not_generated_once")
    deployment.verify(destination)


def qualify_sibling_mismatches(bundle, work):
    """Fresh pairs refuse at their native or paired compatibility boundary."""
    bundle, work = Path(bundle), Path(work)
    original = json.loads(bounded_file(bundle / "manifest.json", 2_000_000))["configuration"]
    require(original.get("installation_admission") == CONTRACT and original["schema_version"] == 1,
            "paired_trial_mismatch_requires_selected_single_host")
    require(all(original["sources"][name]["revision"] == revision for name, revision in REVISIONS.items()),
            "paired_trial_mismatch_pin_changed")
    work.mkdir(mode=0o700)
    reports = {}
    for case, code in (("repository", "producer_invalid"), ("profile", "scope_mismatch"), ("base", "base_mismatch")):
        case_work = work / case
        case_work.mkdir(mode=0o700)
        recipe = copy.deepcopy(original)
        recipe["deployment_id"] += "-mismatch-" + case
        recipe.pop("model_policy", None)
        recipe.pop("child_policy", None)
        recipe["environment_refs"] = {}
        recipe["command"] = ["python3", "-c", "print('preflight fixture must not execute')"]
        negative = case_work / "installation"
        prepare_mismatched_pair(recipe, negative, case)
        reports[case] = qualify_refused_pair(negative, case_work, code) | {
            "initial_generation_fault": {"repository": "openjules-repository",
                                         "profile": "openjules-profile-name", "base": "openjules-base-commit"}[case],
            "rejection_boundary": "producer-native-validation" if case == "repository" else "paired-compatibility",
        }
    require(len({value["bootstrap"]["receipt_id"] for value in reports.values()}) == 3,
            "paired_trial_mismatch_identity_reused")
    return {"schema_version": 1, "evidence": "actual-native-sibling-mismatches",
            "fixture_boundary": "initial generation before first sealing; no native mocks",
            "cases": reports, "services_started": False, "live_provider_qualified": False}


def qualify(bundle, work, *, profile_case):
    from zpm.deployment import verify

    require(profile_case in {"root", "model", "children", "multiple"},
            "paired_trial_case")
    bundle, work = Path(bundle), Path(work)
    verify(bundle)
    data = json.loads(bounded_file(bundle / "manifest.json", 2_000_000))["configuration"]
    require(data.get("installation_admission") == CONTRACT,
            "paired_trial_opt_in_missing")
    require(all(data["sources"][service]["revision"] == revision
                for service, revision in REVISIONS.items()), "paired_trial_pin_mismatch")
    work.mkdir(mode=0o700)
    receipt = bundle / "state/installation-admission.json"
    original_receipt = bounded_file(receipt, 8_000_000)
    private_file(work / "original-receipt.json", original_receipt)
    before = protected_snapshot(bundle)
    command = [sys.executable, str(bundle / "manage.py"), "preflight"]
    observed = []
    for name in ("original", "repeat"):
        # Leave the production 90-second operation budget and owned cleanup
        # room to finish before the trial's outer process bound.
        code, raw = run_bounded(command, bundle, limit=16384, seconds=120)
        require(code == 0, "paired_trial_native_preflight_refused")
        observed.append(accepted_report(raw))
        private_file(work / (name + "-report.json"), raw)
        require(bounded_file(receipt, 8_000_000) == original_receipt
                and protected_snapshot(bundle) == before,
                "paired_trial_preflight_changed_retained_state")
    require(observed[0] == observed[1], "paired_trial_repeat_changed_identity")

    # Deliberately invalidate the exact seal, without changing native JSON
    # meaning. This is an integrity refusal, not a native-parser rejection.
    # The audit guard refuses every non-Git child launch, so even an unexpected
    # path cannot start an authority, runtime or controller during this probe.
    config = bundle / "config/openjules.json"
    original_config = bounded_file(config, 1_000_000)
    try:
        config.write_bytes(original_config + b" ")
        code, raw = run_bounded(
            [sys.executable, "-I", "-B", "-c", START_GUARD,
             str(bundle / "manage.py"), "start"],
            bundle, limit=8192, seconds=60,
        )
        refusal = json.loads(raw)
        require(code == 2 and type(refusal) is dict and set(refusal) == REPORT_KEYS
                and refusal["phase"] == "native-config-refused"
                and refusal["compatible"] is None
                and refusal["native_validation_performed"] is False
                and refusal["services_started"] is False
                and refusal["errors"] in (
                    [{"owner": "zpm", "code": "selection_mismatch"}],
                    [{"owner": "openjules", "code": "input_mismatch"}],
                ), "paired_trial_start_integrity_refusal_missing")
        private_file(work / "sealed-input-start-refusal.json", raw)
    finally:
        config.write_bytes(original_config)
    require(bounded_file(receipt, 8_000_000) == original_receipt
            and protected_snapshot(bundle) == before,
            "paired_trial_refusal_changed_retained_state")
    verify(bundle)
    return {
        "schema_version": 1, "evidence": "actual-installed-paired-preflight",
        "profile_case": profile_case, "preflight": observed[0],
        "bootstrap_receipt_reused": True, "unchanged_repeat": True,
        "sealed_input_start_refused_before_service_launch": True,
        "original_receipt_and_protected_state_unchanged": True,
        "negative_scope": "prepared-input-binding; not a native-parser rejection",
        "live_provider_qualified": False,
    }
