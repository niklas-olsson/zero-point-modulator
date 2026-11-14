"""Pure, private installation receipt and explicitly fixture-only composition.

This first slice is not connected to preparation, bootstrap or service startup.
Supplied producer results are data, never proof that a native validator ran.
There is deliberately no native-config-validated state or admission operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from .models import ConfigurationError, identifier, repository


CONTRACT = "zpm-installation-admission/v1"
SERVICES = ("openjules", "keystone-polyphony")
COMPONENTS = {
    "openjules": ("niklas-olsson/OpenJules", "openjules-installation-v1"),
    "keystone-polyphony": ("niklas-olsson/keystone-polyphony", "managed-installation-v1"),
}
INPUTS = {
    "openjules": {"configuration": 1_000_000, "scope_declarations": 65_536},
    "keystone-polyphony": {"managed_policy": 1_000_000, "credentials": 1_000_000},
}
OPTIONAL_INPUTS = {("openjules", "scope_declarations"), ("keystone-polyphony", "managed_policy")}
MAX_SELECTION_BYTES = 131_072
MAX_RESULT_BYTES = 8_000_000
MAX_PROFILES = 100  # Existing installer composition limit, not a producer limit.
OPENJULES_CONTRACT_DIGEST = "1eaa70ae5ea2b28259c7f7f1884bb93335f7f51cd3dc1edbaf58710f9644573b"
HOST_CHECKS = [
    "worker_credentials", "environment_credentials", "source_and_image",
    "destination_state", "managed_installation_context",
    "public_host_bindings_when_selected", "live_authority_and_canonical_permission",
    "runtime_readiness", "provider_qualification_when_selected",
]
_HEX = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_RECEIPT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_PROFILE_FIELDS = {
    "repository", "profile", "base", "profile_digest", "attempt_seconds", "capabilities",
    "managed_dispatch", "external_root", "children", "child_grants", "child_initial_outcome",
    "child_policy", "profile_supported", "max_attempts", "budget_seconds",
}


class _Invalid(ValueError):
    """Internal fixed-code rejection. Never interpolate source values."""


def _require(condition, code="invalid_contract_data"):
    if not condition:
        raise _Invalid(code)


def _object(value, fields):
    _require(type(value) is dict and set(value) == set(fields))
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result)
        result[key] = value
    return result


def _decode(raw, limit):
    _require(type(raw) is bytes and 0 < len(raw) <= limit)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(_Invalid()))
        # Restrict nesting and finite scalar values even when JSON accepted an
        # overflow such as 1e999. No decoder/validator exception carries input.
        def walk(item, depth=0):
            _require(depth <= 24)
            if type(item) is dict:
                for key, child in item.items():
                    _require(len(key.encode("utf-8")) <= 512)
                    walk(child, depth + 1)
            elif type(item) is list:
                for child in item:
                    walk(child, depth + 1)
            elif type(item) is float:
                _require(math.isfinite(item))
            elif type(item) is str:
                item.encode("utf-8")  # Reject lone surrogates.
        walk(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _Invalid("invalid_contract_data") from None


def encode(value):
    """Encode private receipt data; this neither persists nor admits anything."""
    try:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        _require(len(raw) <= MAX_RESULT_BYTES)
        return raw
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ConfigurationError("invalid installation admission document") from None


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    _require(type(value) is str and _HEX.fullmatch(value) is not None)


def _integer(value, minimum=0, maximum=2**63 - 1):
    _require(type(value) is int and minimum <= value <= maximum)


def _version(value, expected=1):
    _require(type(value) is int and value == expected)


def _boolean(value):
    _require(type(value) is bool)


def _names(value):
    _require(type(value) is list and len(value) <= 100)
    _require(all(type(item) is str and 0 < len(item.encode("utf-8")) <= 512 for item in value))
    _require(len(value) == len(set(value)))


def _scope(repo, profile):
    _require(type(repo) is str and len(repo) <= 512 and repository(repo) == repo)
    _require(type(profile) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", profile) is not None)


def _selected(raw, inputs):
    selected = _object(_decode(raw, MAX_SELECTION_BYTES), {
        "schema_version", "deployment_id", "installation_plan_digest", "components", "profiles",
    })
    _version(selected["schema_version"])
    identifier(selected["deployment_id"], "deployment")
    _digest(selected["installation_plan_digest"])
    _object(selected["components"], SERVICES)
    _object(inputs, SERVICES)
    for service in SERVICES:
        component = _object(selected["components"][service], {
            "repository", "source_revision", "validation_contract", "contract_digest",
        })
        _require((component["repository"], component["validation_contract"]) == COMPONENTS[service])
        revision = component["source_revision"]
        _require(type(revision) is str and _REVISION.fullmatch(revision) is not None)
        _digest(component["contract_digest"])
        if service == "openjules":
            _require(component["contract_digest"] == OPENJULES_CONTRACT_DIGEST, "unsupported_contract")
        _object(inputs[service], INPUTS[service])
        digests = {}
        for name, limit in INPUTS[service].items():
            data = inputs[service][name]
            if data is None:
                _require((service, name) in OPTIONAL_INPUTS)
                digests[name] = None
            else:
                _require(type(data) is bytes and 0 < len(data) <= limit)
                digests[name] = _hash(data)
        component["input_digests"] = digests
    profiles = selected["profiles"]
    _require(type(profiles) is list and 1 <= len(profiles) <= MAX_PROFILES)
    keys = []
    for profile in profiles:
        _object(profile, {"repository", "profile", "base", "profile_digest", "capabilities"})
        _scope(profile["repository"], profile["profile"])
        _require(type(profile["base"]) is str and re.fullmatch(r"[0-9a-f]{40}", profile["base"]) is not None)
        _digest(profile["profile_digest"])
        _names(profile["capabilities"])
        keys.append((profile["repository"], profile["profile"]))
    _require(keys == sorted(set(keys)))
    return selected


def prepare_receipt(selection_bytes, inputs, *, receipt_id):
    """Bind exact explicitly supplied bytes without opening files or secrets.

    Source revisions and contract digests are selections, not attestations.
    The caller supplies a random UUID4 receipt ID; no private hash is used as
    its public identifier. Native validation and compatibility stay unknown.
    """
    try:
        _require(type(receipt_id) is str and _RECEIPT_ID.fullmatch(receipt_id) is not None)
        selected = _selected(selection_bytes, inputs)
        receipt = {
            "schema_version": 1, "receipt_contract": CONTRACT, "receipt_id": receipt_id,
            "deployment_id": selected["deployment_id"],
            "installation_plan_digest": selected["installation_plan_digest"],
            "selection_sha256": _hash(selection_bytes), "profiles": selected["profiles"],
            "components": {service: selected["components"][service] | {"valid": None, "errors": []}
                           for service in SERVICES},
            "phase": "offline-prepared", "compatible": None,
            "source_identity_verified": False, "native_validation_performed": False,
            "live_capabilities_checked": False, "execution_qualified": False,
        }
        _require(len(encode(receipt)) <= MAX_SELECTION_BYTES)
        return receipt
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ConfigurationError("invalid installation admission selection") from None


def _errors(value, limit):
    _require(type(value) is list and len(value) <= limit)
    for item in value:
        _object(item, {"code", "path"})
        _require(type(item["code"]) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item["code"]) is not None)
        path = item["path"]
        _require(type(path) is str and len(path) <= 256 and (
            path == "" or re.fullmatch(r"(?:/(?:[a-z_][a-z0-9_]*|0|[1-9][0-9]{0,5})){1,12}", path) is not None
        ))
        _require(not path.startswith("/credentials/") and not path.startswith("/credentials_file/"))


def _openjules(result, component):
    _object(result, {
        "schema_version", "service", "validation_contract", "contract_digest",
        "configuration_sha256", "scope_declarations_sha256", "valid", "errors",
        "descriptor", "host_checks_required",
    })
    _common(result, "openjules", component)
    _require(result["host_checks_required"] == HOST_CHECKS)
    _require(result["configuration_sha256"] == component["input_digests"]["configuration"]
             and result["scope_declarations_sha256"] == component["input_digests"]["scope_declarations"],
             "input_mismatch")
    _errors(result["errors"], 1)
    if not result["valid"]:
        _require(result["descriptor"] is None and len(result["errors"]) == 1)
        return None
    _require(not result["errors"])
    descriptor = _object(result["descriptor"], {
        "configuration_schema_version", "execution_destination_identity", "profiles",
    })
    _version(descriptor["configuration_schema_version"])
    _integer(descriptor["execution_destination_identity"], 0, 1)
    profiles = descriptor["profiles"]
    _require(type(profiles) is list and 1 <= len(profiles) <= MAX_PROFILES)
    keys = []
    for profile in profiles:
        _object(profile, {"deployment_id", "repository", "profile", "base_commit", "image", "features", "required_peer_contracts"})
        identifier(profile["deployment_id"], "deployment")
        _scope(profile["repository"], profile["profile"])
        _require(type(profile["base_commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", profile["base_commit"]) is not None)
        _require(type(profile["image"]) is str and len(profile["image"]) <= 1024)
        features = _object(profile["features"], {"model_broker", "children", "child_outcome", "child_session", "public_discussion"})
        if features["model_broker"] is not None:
            _version(features["model_broker"])
        for name, supported in (("children", "bounded-children-v1"), ("child_outcome", "initial-v1"),
                                ("child_session", "cold-clarification-v1"), ("public_discussion", "managed-public-pull-v1")):
            _require(features[name] is None or features[name] == supported, "unsupported_contract")
        peers = profile["required_peer_contracts"]
        _require(type(peers) is list and 1 <= len(peers) <= 2)
        expected = {"managed_dispatch": True, "external_root": True, "profile_supported": True}
        if features["children"] is not None:
            expected["child_grants"] = True
        if features["child_outcome"] is not None:
            expected["child_initial_outcome"] = 1
        _require(peers[0] == {"contract": "keystone-polyphony/managed-runtime-v1", "required_capabilities": expected}, "unsupported_contract")
        for peer in peers:
            _object(peer, {"contract", "required_capabilities"})
            for key, value in peer["required_capabilities"].items():
                if key in {"child_initial_outcome", "schema_version"}:
                    _version(value)
                elif key != "profile":
                    _boolean(value)
        if features["public_discussion"] is not None:
            _require(len(peers) == 2 and peers[1] == {
                "contract": "keystone-polyphony/polyphony-public-conversation-v1",
                "required_capabilities": {"schema_version": 1, "profile": "polyphony-public-conversation", "execution_authorized": False},
            }, "unsupported_contract")
        else:
            _require(len(peers) == 1)
        keys.append((profile["deployment_id"], profile["repository"], profile["profile"]))
    _require(keys == sorted(set(keys)))
    return descriptor


def _common(result, service, component):
    _version(result["schema_version"])
    _require(result["service"] == service and result["validation_contract"] == component["validation_contract"], "unsupported_contract")
    _require(result["contract_digest"] == component["contract_digest"], "contract_mismatch")
    _boolean(result["valid"])


def _polyphony(result, component):
    _object(result, {"schema_version", "service", "validation_contract", "contract_digest",
                     "valid", "managed_enabled", "descriptor", "errors"})
    _common(result, "keystone-polyphony", component)
    _errors(result["errors"], 16)
    if not result["valid"]:
        _require(result["managed_enabled"] is None and result["descriptor"] is None and result["errors"])
        return None
    _boolean(result["managed_enabled"])
    _require(not result["errors"])
    if not result["managed_enabled"]:
        _require(result["descriptor"] is None)
        return None
    _require(component["input_digests"]["managed_policy"] is not None, "input_mismatch")
    descriptor = _object(result["descriptor"], {"protocol", "deployment_id", "profiles"})
    _require(descriptor["protocol"] == "managed-runtime-v1", "unsupported_contract")
    identifier(descriptor["deployment_id"], "deployment")
    profiles = descriptor["profiles"]
    _require(type(profiles) is list and 1 <= len(profiles) <= MAX_PROFILES)
    keys = []
    for profile in profiles:
        _object(profile, _PROFILE_FIELDS)
        _scope(profile["repository"], profile["profile"])
        _require(type(profile["base"]) is str and re.fullmatch(r"[0-9a-f]{40}", profile["base"]) is not None)
        _digest(profile["profile_digest"])
        _names(profile["capabilities"])
        for name in ("managed_dispatch", "external_root", "children", "child_grants", "profile_supported"):
            _boolean(profile[name])
        _require(profile["managed_dispatch"] and profile["external_root"] and profile["profile_supported"] and not profile["children"])
        _version(profile["child_initial_outcome"])
        _require(profile["child_policy"] is None or type(profile["child_policy"]) is dict)
        _require(profile["child_grants"] == (profile["child_policy"] is not None))
        # Only validate the published scalar shape. Native policy relations,
        # attenuation, budgets and profile_digest construction stay producer-owned.
        for name in ("attempt_seconds", "budget_seconds"):
            _require(type(profile[name]) in {int, float} and 0 < profile[name] <= 604800)
        _integer(profile["max_attempts"], 1)
        keys.append((profile["repository"], profile["profile"]))
    _require(keys == sorted(set(keys)))
    return descriptor


def _observe(raw, service, component):
    if raw is None:
        return {"status": "missing", "fixture_valid": None, "code": "result_missing"}, None
    try:
        envelope = _object(_decode(raw, MAX_RESULT_BYTES), {"source_revision", "contract_digest", "input_digests", "result"})
        _require(envelope["source_revision"] == component["source_revision"], "source_mismatch")
        _require(envelope["contract_digest"] == component["contract_digest"], "contract_mismatch")
        _require(envelope["input_digests"] == component["input_digests"], "input_mismatch")
        result = envelope["result"]
        descriptor = (_openjules if service == "openjules" else _polyphony)(result, component)
        status = "valid" if result["valid"] else "invalid"
        if service == "keystone-polyphony" and result["valid"] and not result["managed_enabled"]:
            status = "disabled"
        return {"status": status, "fixture_valid": result["valid"], "code": "producer_" + status}, descriptor
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        allowed = {"source_mismatch", "contract_mismatch", "input_mismatch", "unsupported_contract"}
        code = str(error) if isinstance(error, _Invalid) and str(error) in allowed else "result_malformed"
        return {"status": "unvalidated", "fixture_valid": None, "code": code}, None


def _compatible(receipt, runtime, authority):
    if authority["deployment_id"] != receipt["deployment_id"]:
        return False, "deployment_mismatch"
    if any(p["deployment_id"] != receipt["deployment_id"] for p in runtime["profiles"]):
        return False, "deployment_mismatch"
    selected = {(p["repository"], p["profile"]): p for p in receipt["profiles"]}
    oj = {(p["repository"], p["profile"]): p for p in runtime["profiles"]}
    kp = {(p["repository"], p["profile"]): p for p in authority["profiles"]}
    if set(selected) != set(oj) or set(selected) != set(kp):
        return False, "scope_mismatch"
    for key, expected in selected.items():
        root, policy = oj[key], kp[key]
        if root["deployment_id"] != receipt["deployment_id"]:
            return False, "deployment_mismatch"
        if root["base_commit"] != expected["base"] or policy["base"] != expected["base"]:
            return False, "base_mismatch"
        if policy["profile_digest"] != expected["profile_digest"]:
            return False, "profile_digest_mismatch"
        if policy["capabilities"] != expected["capabilities"]:
            return False, "capabilities_mismatch"
        for peer in root["required_peer_contracts"]:
            if peer["contract"] != "keystone-polyphony/managed-runtime-v1":
                # The proposed installation descriptor does not attest public
                # conversation. Never infer it from a root or child capability.
                return None, "required_peer_unvalidated"
            for name, required in peer["required_capabilities"].items():
                if type(policy.get(name)) is not type(required) or policy[name] != required:
                    return False, "required_capability_mismatch"
    return True, "fixture_contracts_match"


def compare_fixtures(receipt_bytes, selection_bytes, inputs, observations):
    """Compare examples against exact selected identities, never native evidence.

    Observations are per-service bytes or None. Their source/input envelopes
    check correlation only. They cannot establish provenance or execution.
    An old receipt cannot be reused with changed selected input bytes.
    """
    try:
        receipt = _decode(receipt_bytes, MAX_SELECTION_BYTES)
        expected = prepare_receipt(selection_bytes, inputs, receipt_id=receipt["receipt_id"])
        # Serialized equality preserves type distinctions such as false/0/null.
        _require(encode(receipt) == encode(expected), "receipt_mismatch")
        _object(observations, SERVICES)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ConfigurationError("installation admission receipt does not match selected inputs") from None
    states, descriptors = {}, {}
    for service in SERVICES:
        states[service], descriptors[service] = _observe(observations[service], service, receipt["components"][service])
    if any(item["status"] == "unvalidated" or item["status"] == "missing" for item in states.values()):
        compatible, code = None, "producer_unvalidated"
    elif any(item["status"] in {"invalid", "disabled"} for item in states.values()):
        compatible, code = False, "producer_invalid_or_disabled"
    else:
        compatible, code = _compatible(receipt, descriptors["openjules"], descriptors["keystone-polyphony"])
    return {
        "schema_version": 1, "evidence": "contract-fixture", "receipt_id": receipt["receipt_id"],
        "fixture_compatible": compatible, "code": code, "components": states,
        "phase": "offline-prepared", "compatible": None,
        "source_identity_verified": False, "native_validation_performed": False,
        "live_capabilities_checked": False, "execution_qualified": False,
    }


def public_summary(report):
    """Allowlist the fixed fixture status; never export selected private inputs."""
    # Reparse to avoid borrowed mutable data or arbitrary mapping subclasses.
    try:
        value = _object(_decode(encode(report), MAX_SELECTION_BYTES), {
            "schema_version", "evidence", "receipt_id", "fixture_compatible", "code", "components",
            "phase", "compatible", "source_identity_verified", "native_validation_performed",
            "live_capabilities_checked", "execution_qualified",
        })
        _version(value["schema_version"])
        _require(value["evidence"] == "contract-fixture" and value["phase"] == "offline-prepared")
        _require(type(value["receipt_id"]) is str and _RECEIPT_ID.fullmatch(value["receipt_id"]) is not None)
        _require(value["compatible"] is None)
        for key in ("source_identity_verified", "native_validation_performed", "live_capabilities_checked", "execution_qualified"):
            _require(value[key] is False)
        _require(value["fixture_compatible"] is None or type(value["fixture_compatible"]) is bool)
        _require(value["code"] in {
            "producer_unvalidated", "producer_invalid_or_disabled", "deployment_mismatch", "scope_mismatch",
            "base_mismatch", "profile_digest_mismatch", "capabilities_mismatch", "required_peer_unvalidated",
            "required_capability_mismatch", "fixture_contracts_match",
        })
        _object(value["components"], SERVICES)
        for item in value["components"].values():
            _object(item, {"status", "fixture_valid", "code"})
            _require(item["status"] in {"missing", "valid", "invalid", "disabled", "unvalidated"})
            _require(item["fixture_valid"] is None or type(item["fixture_valid"]) is bool)
            _require(item["code"] in {"result_missing", "producer_valid", "producer_invalid", "producer_disabled",
                                      "source_mismatch", "contract_mismatch", "input_mismatch", "unsupported_contract", "result_malformed"})
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ConfigurationError("invalid installation admission public summary") from None
