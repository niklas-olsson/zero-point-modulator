"""Deterministic runtime fixture, never evidence of production agent execution."""

from __future__ import annotations

import copy
import uuid

from zpm.models import OPERATIONS
from zpm.provider import ProviderError


class FixtureProvider:
    def __init__(self):
        self.records = {}
        self.specs = {}
        self.calls = []
        self.capable = True
        self.lose_launch_reply = False
        self.lose_drain_reply = False
        self.unreachable = set()
        self.capture_fails = False
        self.mutate_response = None

    def capabilities(self, deployment_id, repository, profile):
        return {"schema_version": 1, "deployment_id": deployment_id, "repository": repository, "profile": profile, "operations": sorted(OPERATIONS), "managed_dispatch": self.capable, "repository_access": self.capable, "profile_supported": profile == "fixture", "reason": "deterministic fixture"}

    def ensure(self, spec):
        self.calls.append(("ensure", copy.deepcopy(spec)))
        key = (spec["deployment_id"], spec["repository"], spec["launch_key"])
        if key in self.specs:
            if self.specs[key] != spec:
                raise ProviderError("changed input under old launch key")
        else:
            self.specs[key] = dict(spec)
            self.records[key] = dict(spec, worker_id=str(uuid.uuid4()), state="ready", reason="fixture ready", termination_confirmed=False, artifacts_captured=True, admission_open=True, active_executions=[])
        if self.lose_launch_reply:
            self.lose_launch_reply = False
            raise ProviderError("lost launch response")
        data = copy.deepcopy(self.records[key])
        if self.mutate_response:
            self.mutate_response(data)
        return data

    def discover(self, deployment_id, repository, launch_key=None):
        self.calls.append(("discover", deployment_id, repository, launch_key))
        return [copy.deepcopy(value) for key, value in self.records.items() if key[0] == deployment_id and key[1] == repository and (launch_key is None or key[2] == launch_key)]

    def _find(self, deployment_id, repository, worker_id):
        for key, value in self.records.items():
            if key[0] == deployment_id and key[1] == repository and value["worker_id"] == worker_id:
                return value
        raise ProviderError("worker not found in scope")

    def observe(self, deployment_id, repository, worker_id):
        self.calls.append(("observe", deployment_id, repository, worker_id))
        if worker_id in self.unreachable:
            raise ProviderError("runtime unavailable")
        return copy.deepcopy(self._find(deployment_id, repository, worker_id))

    def drain(self, deployment_id, repository, worker_id, request_key):
        self.calls.append(("drain", deployment_id, repository, worker_id, request_key))
        value = self._find(deployment_id, repository, worker_id)
        value.update(state="draining", admission_open=False, reason="fixture draining")
        if self.lose_drain_reply:
            self.lose_drain_reply = False
            raise ProviderError("lost drain reply")
        return copy.deepcopy(value)

    def stop(self, deployment_id, repository, worker_id, request_key):
        self.calls.append(("stop", deployment_id, repository, worker_id, request_key))
        value = self._find(deployment_id, repository, worker_id)
        if value["active_executions"] or value["admission_open"]:
            raise ProviderError("execution remains active")
        if self.capture_fails:
            value.update(state="unknown", termination_confirmed=True, artifacts_captured=False, reason="capture failed")
        else:
            value.update(state="stopped", termination_confirmed=True, artifacts_captured=True, reason="fixture stopped")
        return copy.deepcopy(value)
