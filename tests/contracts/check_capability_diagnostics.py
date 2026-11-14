"""Pair ZPM with the actual managed capability producer over loopback HTTP.

The producer's existing authority HTTP and Docker preflight fixtures are explicit.
No container, model inference, real Polyphony process or protected task runs.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from check_openjules import local_server, require, temporary_credential


def source_identity(path):
    def git(*arguments):
        return subprocess.run(["git", "-C", str(path), *arguments], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    return {"revision": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=all"))}


async def run_trial(checkout):
    consumer = Path(__file__).resolve().parents[2]
    # The two repositories both have a tests package. This process deliberately
    # selects the pinned producer's named fixtures, never an installed substitute.
    sys.path[:0] = [str(checkout), str(consumer)]
    import pytest
    from tests.integration import test_managed_capability_diagnostics as producer
    from tests.unit.test_managed_docker_runtime import setup_runtime
    from zpm.controller import Controller
    from zpm.models import ProviderConfig, UNSUPPORTED_AUTHORITY_REASON, capability_observation
    from zpm.provider import OpenJulesProvider
    from zpm.store import Store

    fixture_path = "tests/fixtures/capability-diagnostic-v1.json"
    fixture_bytes = (checkout / fixture_path).read_bytes()
    require(fixture_bytes == (consumer / fixture_path).read_bytes(),
            "producer and consumer diagnostic contract fixtures differ")
    fixtures = json.loads(fixture_bytes)
    diagnostic = fixtures["diagnostic"]
    checks = []
    with (TemporaryDirectory(prefix="zpm-diagnostics-") as temporary,
          pytest.MonkeyPatch.context() as monkeypatch,
          temporary_credential() as (reference, token)):
        root = Path(temporary)
        monkeypatch.setattr(producer, "WORKER_TOKEN", token)
        runtime_fixture = setup_runtime.__wrapped__(root)
        async with producer.composed_service(
            root, monkeypatch, runtime_fixture, "initial-v1",
        ) as (app, _client, authority, supported, spec, docker_fixture):
            with local_server(app, lifespan="off") as endpoint:
                provider = OpenJulesProvider(ProviderConfig.parse({
                    "schema_version": 1, "endpoint": endpoint,
                    "token_ref": "env:" + reference, "max_workers": 1,
                    "timeout_seconds": 3,
                }))
                scope = {"deployment_id": spec.deployment_id, "repository": spec.repository,
                         "profile": spec.profile}
                with Store(root / "zpm.sqlite3", spec.deployment_id) as store:
                    store.enroll({"schema_version": 1, "repository": spec.repository,
                                  "profile": spec.profile, "desired_workers": 1})
                    controller = Controller(store, provider, 1)
                    ready = provider.capabilities(**scope)
                    require(ready == fixtures["responses"]["supported"],
                            "actual supported producer response differs from its frozen vector")
                    require(controller.check()["repositories"][0]["validation"] == {"ready": True, "reason": "ready"},
                            "supported native producer was not ready")
                    retained = producer.retained_state(app, docker_fixture)

                    authority["payload"] = {key: value for key, value in supported.items()
                                            if key != "child_initial_outcome"}
                    unsupported = provider.capabilities(**scope)
                    require(unsupported == fixtures["responses"]["unsupported"],
                            "actual unsupported producer response differs from its frozen vector")
                    blocked = controller.tick()
                    expected = {"ready": False, "reason": UNSUPPORTED_AUTHORITY_REASON,
                                "diagnostic": diagnostic}
                    require(blocked["reserved"] == 0 and store.workers() == []
                            and blocked["repositories"][0]["validation"] == expected,
                            "typed refusal did not block new work with retained classification")
                    require(producer.retained_state(app, docker_fixture) == retained,
                            "capability refusal created or changed runtime work")
                    checks.append("actual scoped producer HTTP refusal blocks admission and retains only its bounded diagnostic")

                    # Change only display wording at the runtime projection. The
                    # authority request and typed producer fact still run normally.
                    runtime = app.state.worker_lifecycle.runtime
                    original = runtime.capabilities

                    def changed_wording(*args, **kwargs):
                        return replace(original(*args, **kwargs), reason="untrusted-private-detail")

                    with patch.object(runtime, "capabilities", changed_wording):
                        require(controller.check()["repositories"][0]["validation"] == expected,
                                "remote human wording changed the classified observation")
                    require("untrusted-private-detail" not in json.dumps(controller.plan()),
                            "remote prose leaked into retained status")
                    checks.append("changed human wording preserves the typed reason without copying remote prose")

                    authority.update(status=503, payload={"error": "untrusted-private-detail"})
                    unavailable = provider.capabilities(**scope)
                    require(unavailable == fixtures["responses"]["unavailable"],
                            "generic producer outage differs from its frozen vector")
                    require(controller.tick()["repositories"][0]["validation"] == {
                        "ready": False, "reason": "repository_access unavailable",
                    }, "unavailable authority retained a classified fact")
                    wrong_scope = scope | {"profile": "not-approved"}
                    wrong = provider.capabilities(**wrong_scope)
                    require(wrong["profile"] == "not-approved" and wrong["profile_supported"] is False
                            and "diagnostic" not in wrong,
                            "wrong profile inherited another profile's capability fact")
                    require(capability_observation(unsupported, wrong_scope)[2] is None,
                            "wrong requested profile accepted another profile's diagnostic")
                    checks.append("generic authority failure and wrong profile publish no trusted classified fact")

                    authority.update(status=200, payload=supported)
                    require(controller.check()["repositories"][0]["validation"] == {"ready": True, "reason": "ready"},
                            "current recovery did not remove the old refusal")
                    require(store.workers() == [] and producer.retained_state(app, docker_fixture) == retained,
                            "capability observations created protected, container or model work")
                    producer.assert_scoped_requests(authority, spec)
                    checks.append("recovery clears the refusal with no worker, protected task, container or model effect")

    return {
        "result": "passed", "checks": checks,
        "components": {"zpm": source_identity(consumer), "openjules": source_identity(checkout)},
        "fixture_sha256": "sha256:" + hashlib.sha256(fixture_bytes).hexdigest(),
        "scope": "actual ZPM/managed OpenJules HTTP with authority and Docker-preflight fixtures; no installed Docker, live model or Polyphony proof",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openjules-checkout", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_trial(args.openjules_checkout.resolve(strict=True))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
